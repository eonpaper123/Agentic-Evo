[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Plan", "Run", "Elevated")]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [string]$LabId,
    [Parameter(Mandatory = $true)]
    [string]$RunId,
    [Parameter(Mandatory = $true)]
    [string]$ArtifactPath,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedArtifactSha256,
    [Parameter(Mandatory = $true)]
    [string]$EvidenceRoot,
    [string]$ExpectedScriptSha256 = "",
    [string]$VerifiedScriptPath = "",
    [string]$PlanSha256 = "",
    [string]$Challenge = "",
    [string]$PipeName = "",
    [ValidateRange(30, 300)]
    [int]$TimeoutSeconds = 120
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:SystemDirectory = [Environment]::SystemDirectory
$script:WindowsDirectory = [IO.Directory]::GetParent(
    $script:SystemDirectory
).FullName
$env:SystemRoot = $script:WindowsDirectory
$env:WINDIR = $script:WindowsDirectory
$script:PowerShellHome = [IO.Path]::Combine(
    $script:SystemDirectory,
    "WindowsPowerShell",
    "v1.0"
)
if (
    -not [string]::Equals(
        [IO.Path]::GetFullPath($PSHOME).TrimEnd([IO.Path]::DirectorySeparatorChar),
        [IO.Path]::GetFullPath($script:PowerShellHome).TrimEnd(
            [IO.Path]::DirectorySeparatorChar
        ),
        [StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "gate_b_requires_trusted_windows_powershell"
}
$env:PSModulePath = [IO.Path]::Combine($script:PowerShellHome, "Modules")
$PSModuleAutoLoadingPreference = "None"
foreach ($moduleName in @(
    "Microsoft.PowerShell.Management",
    "Microsoft.PowerShell.Security",
    "Microsoft.PowerShell.Utility",
    "CimCmdlets"
)) {
    $manifest = [IO.Path]::Combine(
        $script:PowerShellHome,
        "Modules",
        $moduleName,
        "$moduleName.psd1"
    )
    Microsoft.PowerShell.Core\Import-Module `
        -Name $manifest `
        -Force `
        -ErrorAction Stop
}
$script:ProgramFiles = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::ProgramFiles
)
$script:ProgramData = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::CommonApplicationData
)
$script:ScExe = [IO.Path]::Combine($script:SystemDirectory, "sc.exe")
$script:PowerShellExe = [IO.Path]::Combine(
    $script:PowerShellHome,
    "powershell.exe"
)
$script:ArtifactName = "AgenticEvo.ScmProbe.exe"
$script:RepositoryRoot = [IO.Directory]::GetParent(
    $(if ($Mode -eq "Elevated" -and -not [string]::IsNullOrWhiteSpace(
        $VerifiedScriptPath
    )) {
        [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($VerifiedScriptPath))
    } else {
        $PSScriptRoot
    })
).FullName

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-TextSha256([string]$Value) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
        return ([BitConverter]::ToString($hash.ComputeHash($bytes))).Replace(
            "-",
            ""
        ).ToLowerInvariant()
    }
    finally {
        $hash.Dispose()
    }
}

function Test-ObjectExists([string]$Path) {
    try {
        [void](Get-Item -LiteralPath $Path -Force -ErrorAction Stop)
        return $true
    }
    catch [Management.Automation.ItemNotFoundException] {
        return $false
    }
}

function Assert-NoReparsePath([string]$Path) {
    $currentPath = [IO.Path]::GetFullPath($Path)
    while ($true) {
        $item = Get-Item -LiteralPath $currentPath -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "reparse_path_rejected: $currentPath"
        }
        $parent = [IO.Directory]::GetParent($currentPath)
        if ($null -eq $parent) {
            return
        }
        $currentPath = $parent.FullName
    }
}

function Get-ServiceSnapshot([string]$Name) {
    $presence = Invoke-Sc @("query", $Name)
    if ($presence.exit_code -eq 1060) {
        return $null
    }
    Assert-ScSuccess $presence "query_service_presence"
    $service = Get-CimInstance Win32_Service `
        -Filter "Name='$Name'" `
        -OperationTimeoutSec 5 `
        -ErrorAction Stop
    if ($null -eq $service) {
        throw "service_presence_snapshot_mismatch"
    }
    return [ordered]@{
        name = [string]$service.Name
        state = [string]$service.State
        process_id = [int]$service.ProcessId
        service_type = [string]$service.ServiceType
        start_name = [string]$service.StartName
        path_name = [string]$service.PathName
    }
}

function Get-Plan {
    if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
        throw "gate_b_requires_64_bit_powershell"
    }
    if ($RunId -notmatch "^[0-9a-f]{32}$") {
        throw "run_id_must_be_32_lower_hex"
    }
    if ($LabId -notmatch "^[a-z0-9][a-z0-9-]*$") {
        throw "lab_id_invalid"
    }
    $labDirectory = [IO.Path]::GetFullPath(
        (Join-Path $script:RepositoryRoot "experiments\labs")
    )
    $labDeclarationPath = "experiments/labs/$LabId.json"
    $labDeclarationFile = [IO.Path]::GetFullPath(
        (Join-Path $labDirectory "$LabId.json")
    )
    if (
        -not $labDeclarationFile.StartsWith(
            $labDirectory.TrimEnd([IO.Path]::DirectorySeparatorChar) +
            [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        -not [IO.File]::Exists($labDeclarationFile)
    ) {
        throw "lab_declaration_missing"
    }
    Assert-NoReparsePath $labDeclarationFile
    try {
        $labDeclaration = [IO.File]::ReadAllText(
            $labDeclarationFile,
            [Text.UTF8Encoding]::new($false, $true)
        ) | ConvertFrom-Json
    }
    catch {
        throw "lab_declaration_invalid"
    }
    if (
        $labDeclaration.schema -ne "agentic-evo.lab-declaration.v1" -or
        $labDeclaration.lab_id -ne $LabId -or
        $labDeclaration.status -ne "active_reference_lab" -or
        [string]::IsNullOrWhiteSpace($labDeclaration.evidence_namespace)
    ) {
        throw "lab_declaration_invalid"
    }
    $evidenceNamespace = [string]$labDeclaration.evidence_namespace
    if (
        [IO.Path]::IsPathRooted($evidenceNamespace) -or
        ($evidenceNamespace -split "[\\/]+") -contains ".."
    ) {
        throw "lab_evidence_namespace_invalid"
    }
    $evidenceNamespaceRoot = [IO.Path]::GetFullPath(
        (Join-Path $script:RepositoryRoot $evidenceNamespace)
    )
    if (-not $evidenceNamespaceRoot.StartsWith(
        $script:RepositoryRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) +
        [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "lab_evidence_namespace_invalid"
    }
    $evidenceRoot = Join-Path $evidenceNamespaceRoot "windows-gate-b\$RunId"
    $environment = [ordered]@{
        os_family = "Windows"
        os_version = [Environment]::OSVersion.Version.ToString()
        os_architecture = $(if ([Environment]::Is64BitOperatingSystem) {
            "x64"
        } else {
            "x86"
        })
        powershell_edition = [string]$PSVersionTable.PSEdition
        powershell_version = $PSVersionTable.PSVersion.ToString()
        trusted_system_directory = $script:SystemDirectory
    }
    $environmentSha256 = Get-TextSha256 (ConvertTo-CompactJson $environment)
    $digest = $ExpectedArtifactSha256.ToLowerInvariant()
    if ($digest -notmatch "^[0-9a-f]{64}$") {
        throw "expected_artifact_sha256_must_be_64_lower_hex"
    }
    $source = [IO.Path]::GetFullPath($ArtifactPath)
    if (-not [IO.File]::Exists($source)) {
        throw "source_artifact_missing"
    }
    if ([IO.Path]::GetFileName($source) -ne $script:ArtifactName) {
        throw "unexpected_source_artifact_name"
    }
    Assert-NoReparsePath $source
    $header = [IO.File]::ReadAllBytes($source)
    if (
        $header.Length -lt 2 -or
        $header[0] -ne [byte][char]"M" -or
        $header[1] -ne [byte][char]"Z"
    ) {
        throw "source_artifact_is_not_pe"
    }
    if ((Get-FileSha256 $source) -ne $digest) {
        throw "source_artifact_sha256_mismatch"
    }

    $serviceName = "AgenticEvoGateB_$RunId"
    $artifactProductBase = Join-Path $script:ProgramFiles "Agentic-Evo"
    $stateProductBase = Join-Path $script:ProgramData "Agentic-Evo"
    $artifactRoot = Join-Path $artifactProductBase "GateB\$RunId"
    $stateRoot = Join-Path $stateProductBase "GateB\$RunId"
    foreach ($target in @(
        $artifactProductBase,
        $stateProductBase,
        $artifactRoot,
        $stateRoot
    )) {
        if (Test-ObjectExists $target) {
            throw "random_target_already_exists: $target"
        }
    }
    if ($null -ne (Get-ServiceSnapshot $serviceName)) {
        throw "random_service_already_exists"
    }

    return [ordered]@{
        schema = "agentic-evo.windows-gate-b-plan.v2"
        mode = "plan"
        lab_id = $LabId
        run_id = $RunId
        service_name = $serviceName
        source_artifact = $source
        artifact_sha256 = $digest
        artifact_product_base = $artifactProductBase
        artifact_root = $artifactRoot
        artifact_path = Join-Path $artifactRoot $script:ArtifactName
        state_product_base = $stateProductBase
        state_root = $stateRoot
        probe_path = Join-Path $stateRoot "scm-write.probe"
        evidence_root = $evidenceRoot
        trusted_system_directory = $script:SystemDirectory
        lab_declaration_path = $labDeclarationPath
        lab_declaration_sha256 = Get-FileSha256 $labDeclarationFile
        evidence_namespace = $evidenceNamespace
        environment = $environment
        environment_sha256 = $environmentSha256
        authorized_effects = [ordered]@{
            temporary_service = $true
            permanent_service = $false
            hook = $false
            genesis = $false
            system_restart = $false
        }
        claim_ceiling = [ordered]@{
            gate_b = "not_established"
            restricted_service_sid_configuration = "configuration_probe_only"
            C01 = "not_run"
            C02 = "not_run"
            I01 = "not_run"
            S01 = "not_run"
            S02 = "not_run"
            S03 = "not_run"
            P01 = "not_run"
            P02 = "not_run"
            L01 = "not_run"
            R01 = "not_run"
            R02 = "not_run"
            U01 = "partial_cleanup_if_service_lifecycle_occurs"
            native_security_verified = $false
            ready_to_install = $false
        }
    }
}

function ConvertTo-CompactJson($Value) {
    return ($Value | ConvertTo-Json -Depth 12 -Compress)
}

function Write-AtomicJson([string]$Path, $Value) {
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(
        (ConvertTo-CompactJson $Value) + "`n"
    )
    $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    $stream = [IO.FileStream]::new(
        $temporary,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None,
        4096,
        [IO.FileOptions]::WriteThrough
    )
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
    [IO.File]::Move($temporary, $Path)
}

function ConvertTo-WindowsArgument([string]$Value) {
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }
    $builder = [Text.StringBuilder]::new('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $slashes += 1
        }
        elseif ($character -eq [char]34) {
            [void]$builder.Append((-join ('\' * (2 * $slashes + 1))))
            [void]$builder.Append([char]34)
            $slashes = 0
        }
        else {
            if ($slashes) {
                [void]$builder.Append((-join ('\' * $slashes)))
                $slashes = 0
            }
            [void]$builder.Append($character)
        }
    }
    if ($slashes) {
        [void]$builder.Append((-join ('\' * (2 * $slashes)))
        )
    }
    [void]$builder.Append([char]34)
    return $builder.ToString()
}

function Invoke-Sc([string[]]$Arguments) {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $script:ScExe
    $start.Arguments = (
        ($Arguments | ForEach-Object { ConvertTo-WindowsArgument $_ }) -join " "
    )
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) {
            throw "sc.exe_start_failed"
        }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $timedOut = -not $process.WaitForExit(10000)
        if ($timedOut) {
            $process.Kill()
            [void]$process.WaitForExit(5000)
        }
        $result = [ordered]@{
            arguments = @($Arguments)
            exit_code = $(if ($timedOut) { -2 } else { $process.ExitCode })
            timed_out = $timedOut
            output = (($stdout.Result, $stderr.Result) -join "`n").Trim()
        }
    }
    finally {
        $process.Dispose()
    }
    return $result
}

function Assert-ScSuccess($Result, [string]$Action) {
    if ($Result.exit_code -ne 0) {
        throw "$Action failed with sc.exe exit $($Result.exit_code)"
    }
}

function New-DirectoryRule([string]$Sid, [Security.AccessControl.FileSystemRights]$Rights) {
    $identity = [Security.Principal.SecurityIdentifier]::new($Sid)
    return [Security.AccessControl.FileSystemAccessRule]::new(
        $identity,
        $Rights,
        (
            [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [Security.AccessControl.InheritanceFlags]::ObjectInherit
        ),
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow
    )
}

function New-DirectorySecurity([hashtable]$RightsBySid) {
    $security = [Security.AccessControl.DirectorySecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner(
        [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
    )
    foreach ($sid in ($RightsBySid.Keys | Sort-Object)) {
        [void]$security.AddAccessRule(
            (New-DirectoryRule $sid $RightsBySid[$sid])
        )
    }
    return $security
}

function Set-DirectorySecurity([string]$Path, [hashtable]$RightsBySid) {
    Set-Acl -LiteralPath $Path -AclObject (New-DirectorySecurity $RightsBySid)
}

function Assert-DirectorySecurity([string]$Path, [hashtable]$RightsBySid) {
    $acl = Get-Acl -LiteralPath $Path
    $owner = $acl.GetOwner(
        [Security.Principal.SecurityIdentifier]
    ).Value
    $rules = @(
        $acl.GetAccessRules(
            $true,
            $true,
            [Security.Principal.SecurityIdentifier]
        )
    )
    if (
        -not $acl.AreAccessRulesProtected -or
        $owner -ne "S-1-5-32-544" -or
        $rules.Count -ne $RightsBySid.Count
    ) {
        throw "directory_acl_mismatch: $Path"
    }
    foreach ($rule in $rules) {
        if (
            $rule.IsInherited -or
            $rule.AccessControlType -ne (
                [Security.AccessControl.AccessControlType]::Allow
            ) -or
            -not $RightsBySid.ContainsKey($rule.IdentityReference.Value) -or
            [int]$rule.FileSystemRights -ne (
                [int]$RightsBySid[$rule.IdentityReference.Value]
            )
        ) {
            throw "directory_acl_rule_mismatch: $Path"
        }
    }
}

function New-ProtectedDirectoryInTrustedParent(
    [string]$Path,
    [hashtable]$RightsBySid,
    [Collections.Generic.List[string]]$Created
) {
    if (Test-ObjectExists $Path) {
        throw "protected_directory_collision: $Path"
    }
    Assert-NoReparsePath (Split-Path -Parent $Path)
    [IO.DirectoryInfo]::new($Path).Create((New-DirectorySecurity $RightsBySid))
    $Created.Add($Path)
    Assert-NoReparsePath $Path
    Assert-DirectorySecurity $Path $RightsBySid
}

function New-ProtectedDirectory(
    [string]$Path,
    [hashtable]$RightsBySid,
    [Collections.Generic.List[string]]$Created
) {
    if ($null -eq ("AgenticEvoDirectoryNative" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class AgenticEvoDirectoryNative
{
    [StructLayout(LayoutKind.Sequential)]
    public struct SECURITY_ATTRIBUTES
    {
        public int nLength;
        public IntPtr lpSecurityDescriptor;
        [MarshalAs(UnmanagedType.Bool)]
        public bool bInheritHandle;
    }

    [DllImport(
        "kernel32.dll",
        CharSet = CharSet.Unicode,
        ExactSpelling = true,
        SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool CreateDirectoryW(
        string path,
        ref SECURITY_ATTRIBUTES securityAttributes);
}
"@
    }

    $security = New-DirectorySecurity $RightsBySid
    $descriptor = $security.GetSecurityDescriptorBinaryForm()
    $descriptorPointer = [Runtime.InteropServices.Marshal]::AllocHGlobal(
        $descriptor.Length
    )
    try {
        [Runtime.InteropServices.Marshal]::Copy(
            $descriptor,
            0,
            $descriptorPointer,
            $descriptor.Length
        )
        $attributes = [AgenticEvoDirectoryNative+SECURITY_ATTRIBUTES]::new()
        $attributes.nLength = [Runtime.InteropServices.Marshal]::SizeOf(
            $attributes
        )
        $attributes.lpSecurityDescriptor = $descriptorPointer
        $attributes.bInheritHandle = $false
        if (
            -not [AgenticEvoDirectoryNative]::CreateDirectoryW(
                $Path,
                [ref]$attributes
            )
        ) {
            $nativeError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            if ($nativeError -eq 183) {
                throw "protected_directory_collision: $Path"
            }
            throw "protected_directory_create_failed_${nativeError}: $Path"
        }
    }
    finally {
        [Runtime.InteropServices.Marshal]::FreeHGlobal($descriptorPointer)
    }

    $Created.Add($Path)
    Assert-NoReparsePath $Path
    Assert-DirectorySecurity $Path $RightsBySid
}

function Set-FileSecurity([string]$Path, [hashtable]$RightsBySid) {
    $security = [Security.AccessControl.FileSecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner(
        [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
    )
    foreach ($sid in ($RightsBySid.Keys | Sort-Object)) {
        [void]$security.AddAccessRule(
            [Security.AccessControl.FileSystemAccessRule]::new(
                [Security.Principal.SecurityIdentifier]::new($sid),
                $RightsBySid[$sid],
                [Security.AccessControl.AccessControlType]::Allow
            )
        )
    }
    Set-Acl -LiteralPath $Path -AclObject $security
}

function Get-AclSnapshot([string]$Path) {
    $acl = Get-Acl -LiteralPath $Path
    return [ordered]@{
        owner = $acl.Owner
        protected = [bool]$acl.AreAccessRulesProtected
        rules = @(
            $acl.GetAccessRules(
                $true,
                $true,
                [Security.Principal.SecurityIdentifier]
            ) | ForEach-Object {
                [ordered]@{
                    sid = $_.IdentityReference.Value
                    rights = [int]$_.FileSystemRights
                    type = [string]$_.AccessControlType
                    inherited = [bool]$_.IsInherited
                }
            }
        )
    }
}

function New-ControllerPipe([string]$Name, [string]$UserSid) {
    $security = [IO.Pipes.PipeSecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    [void]$security.AddAccessRule(
        [IO.Pipes.PipeAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($UserSid),
            [IO.Pipes.PipeAccessRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        )
    )
    return [IO.Pipes.NamedPipeServerStream]::new(
        $Name,
        [IO.Pipes.PipeDirection]::InOut,
        1,
        [IO.Pipes.PipeTransmissionMode]::Message,
        [IO.Pipes.PipeOptions]::Asynchronous,
        65536,
        65536,
        $security
    )
}

function Get-PipeClientProcessId([IO.Pipes.NamedPipeServerStream]$Pipe) {
    if ($null -eq ("AgenticEvoPipeNative" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class AgenticEvoPipeNative
{
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool GetNamedPipeClientProcessId(
        IntPtr pipe,
        out uint clientProcessId);
}
"@
    }
    [uint32]$clientPid = 0
    if (
        -not [AgenticEvoPipeNative]::GetNamedPipeClientProcessId(
            $Pipe.SafePipeHandle.DangerousGetHandle(),
            [ref]$clientPid
        )
    ) {
        throw "could_not_bind_elevated_pipe_client"
    }
    return [int]$clientPid
}

function Get-PipeMessage(
    [IO.StreamReader]$Reader,
    [Diagnostics.Process]$Process,
    [DateTime]$Deadline
) {
    $task = $Reader.ReadLineAsync()
    while (-not $task.IsCompleted -and [DateTime]::UtcNow -lt $Deadline) {
        if ($Process.HasExited) {
            [void]$task.Wait(1000)
            if (-not $task.IsCompleted) {
                throw "elevated_process_exited_before_report"
            }
            break
        }
        Start-Sleep -Milliseconds 200
    }
    if (-not $task.IsCompleted -or [string]::IsNullOrWhiteSpace($task.Result)) {
        throw "elevated_report_timeout"
    }
    return ($task.Result | ConvertFrom-Json)
}

function Test-ServiceBoundToPlan($Service, $Plan, [string]$ImagePath) {
    return (
        $null -ne $Service -and
        [string]::Equals(
            [string]$Service.name,
            [string]$Plan.service_name,
            [StringComparison]::OrdinalIgnoreCase
        ) -and
        [string]::Equals(
            [string]$Service.start_name,
            "NT AUTHORITY\LocalService",
            [StringComparison]::OrdinalIgnoreCase
        ) -and
        [string]::Equals(
            [string]$Service.service_type,
            "Own Process",
            [StringComparison]::OrdinalIgnoreCase
        ) -and
        [string]::Equals(
            [string]$Service.path_name,
            $ImagePath,
            [StringComparison]::Ordinal
        )
    )
}

function Test-PathAbsentForCleanup(
    [string]$Path,
    [string]$Label,
    [Collections.Generic.List[string]]$Errors
) {
    try {
        return -not (Test-ObjectExists $Path)
    }
    catch {
        $Errors.Add("$Label presence: $($_.Exception.Message)")
        return $false
    }
}

function Wait-ServiceAbsent([string]$Name, [int]$TimeoutSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $presence = Invoke-Sc @("query", $Name)
        if ($presence.exit_code -eq 1060) {
            return $true
        }
        if ($presence.exit_code -notin @(0, 1072)) {
            throw "query_service_delete_state failed with sc.exe exit $($presence.exit_code)"
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Invoke-Cleanup(
    $Plan,
    [bool]$ServiceCreated,
    [string]$ImagePath,
    $Created
) {
    $errors = [Collections.Generic.List[string]]::new()
    $deleteResult = $null
    $serviceOwned = $ServiceCreated
    try {
        $service = Get-ServiceSnapshot $Plan.service_name
        if ($null -ne $service) {
            if (-not (Test-ServiceBoundToPlan $service $Plan $ImagePath)) {
                throw "cleanup_refused_unbound_service"
            }
            $serviceOwned = $true
            $deleteResult = Invoke-Sc @("delete", $Plan.service_name)
            if ($deleteResult.exit_code -notin @(0, 1060, 1072)) {
                throw "delete_service failed with sc.exe exit $($deleteResult.exit_code)"
            }
            if (-not (Wait-ServiceAbsent $Plan.service_name 30)) {
                throw "service_delete_not_observed"
            }
        }
    }
    catch {
        $errors.Add("service_cleanup: $($_.Exception.Message)")
    }

    $serviceAbsent = $false
    try {
        $serviceAbsent = $null -eq (Get-ServiceSnapshot $Plan.service_name)
    }
    catch {
        $errors.Add("service_presence: $($_.Exception.Message)")
    }
    if ($serviceAbsent) {
        $administratorFull = @{
            "S-1-5-18" = [Security.AccessControl.FileSystemRights]::FullControl
            "S-1-5-32-544" = [Security.AccessControl.FileSystemRights]::FullControl
        }
        for ($index = $Created.Count - 1; $index -ge 0; $index -= 1) {
            $path = $Created[$index]
            try {
                if (Test-ObjectExists $path) {
                    Assert-NoReparsePath $path
                    Set-DirectorySecurity $path $administratorFull
                    $recursive = $path -in @($Plan.artifact_root, $Plan.state_root)
                    Remove-Item -LiteralPath $path -Force -Recurse:$recursive
                }
            }
            catch {
                $errors.Add("path_cleanup $path`: $($_.Exception.Message)")
            }
        }
    }
    else {
        $errors.Add("protected_paths_preserved_because_service_remains")
    }

    try {
        $serviceAbsent = $null -eq (Get-ServiceSnapshot $Plan.service_name)
    }
    catch {
        $serviceAbsent = $false
        $errors.Add("final_service_presence: $($_.Exception.Message)")
    }
    $artifactTreeAbsent = Test-PathAbsentForCleanup `
        $Plan.artifact_product_base `
        "artifact_tree" `
        $errors
    $stateTreeAbsent = Test-PathAbsentForCleanup `
        $Plan.state_product_base `
        "state_tree" `
        $errors
    return [ordered]@{
        complete = (
            $serviceAbsent -and
            $artifactTreeAbsent -and
            $stateTreeAbsent -and
            $errors.Count -eq 0
        )
        service_created_or_reconciled = $serviceOwned
        delete = $deleteResult
        service_absent = $serviceAbsent
        artifact_tree_absent = $artifactTreeAbsent
        state_tree_absent = $stateTreeAbsent
        errors = @($errors)
    }
}

function Invoke-Elevated($Plan, [string]$PlanJson) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (
        -not $principal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator
        )
    ) {
        throw "elevated_mode_requires_administrator"
    }
    if (
        $ExpectedScriptSha256 -notmatch "^[0-9a-f]{64}$" -or
        [string]::IsNullOrWhiteSpace($VerifiedScriptPath) -or
        $PlanSha256 -notmatch "^[0-9a-f]{64}$" -or
        (Get-TextSha256 $PlanJson) -ne $PlanSha256 -or
        $Plan.schema -ne "agentic-evo.windows-gate-b-plan.v2" -or
        $Plan.lab_id -ne $LabId -or
        $Plan.environment_sha256 -ne (
            Get-TextSha256 (ConvertTo-CompactJson $Plan.environment)
        ) -or
        $Challenge -notmatch "^[0-9a-f]{32}$" -or
        $PipeName -ne "AgenticEvoGateB-$($Plan.run_id)-$Challenge"
    ) {
        throw "elevated_handoff_binding_invalid"
    }
    $pipe = [IO.Pipes.NamedPipeClientStream]::new(
        ".",
        $PipeName,
        [IO.Pipes.PipeDirection]::Out,
        [IO.Pipes.PipeOptions]::Asynchronous
    )
    $pipe.Connect(15000)
    $writer = [IO.StreamWriter]::new(
        $pipe,
        [Text.UTF8Encoding]::new($false),
        4096,
        $true
    )
    $writer.AutoFlush = $true

    $created = [Collections.Generic.List[string]]::new()
    $serviceCreated = $false
    $restrictedConfigured = $false
    $observation = $null
    $errorMessage = ""
    $imagePath = (
        '"' + $Plan.artifact_path + '" service --service-name ' +
        $Plan.service_name + ' --probe-path "' + $Plan.probe_path + '"'
    )
    try {
        Assert-NoReparsePath $script:ProgramFiles
        Assert-NoReparsePath $script:ProgramData
        if (
            (Test-ObjectExists $Plan.artifact_product_base) -or
            (Test-ObjectExists $Plan.state_product_base) -or
            $null -ne (Get-ServiceSnapshot $Plan.service_name)
        ) {
            throw "elevated_target_collision"
        }
        $sourceBytes = [IO.File]::ReadAllBytes($Plan.source_artifact)
        $hash = [Security.Cryptography.SHA256]::Create()
        try {
            $sourceDigest = (
                [BitConverter]::ToString($hash.ComputeHash($sourceBytes))
            ).Replace("-", "").ToLowerInvariant()
        }
        finally {
            $hash.Dispose()
        }
        if ($sourceDigest -ne $Plan.artifact_sha256) {
            throw "elevated_source_artifact_sha256_mismatch"
        }

        $administratorFull = @{
            "S-1-5-18" = [Security.AccessControl.FileSystemRights]::FullControl
            "S-1-5-32-544" = [Security.AccessControl.FileSystemRights]::FullControl
        }
        foreach ($path in @(
            $Plan.artifact_product_base,
            (Split-Path -Parent $Plan.artifact_root),
            $Plan.artifact_root
        )) {
            New-ProtectedDirectoryInTrustedParent `
                $path `
                $administratorFull `
                $created
        }
        $env:TEMP = $Plan.artifact_root
        $env:TMP = $Plan.artifact_root
        foreach ($path in @(
            $Plan.state_product_base,
            (Split-Path -Parent $Plan.state_root),
            $Plan.state_root
        )) {
            New-ProtectedDirectory $path $administratorFull $created
        }
        [IO.File]::WriteAllBytes($Plan.artifact_path, $sourceBytes)
        if ((Get-FileSha256 $Plan.artifact_path) -ne $Plan.artifact_sha256) {
            throw "protected_artifact_sha256_mismatch"
        }

        $create = $null
        try {
            $create = Invoke-Sc @(
                "create",
                $Plan.service_name,
                "type=",
                "own",
                "start=",
                "demand",
                "error=",
                "normal",
                "obj=",
                "NT AUTHORITY\LocalService",
                "binPath=",
                $imagePath
            )
        }
        finally {
            $createdService = Get-ServiceSnapshot $Plan.service_name
            if ($null -ne $createdService) {
                if (
                    -not (
                        Test-ServiceBoundToPlan `
                            $createdService `
                            $Plan `
                            $imagePath
                    )
                ) {
                    throw "created_service_binding_mismatch"
                }
                $serviceCreated = $true
            }
        }
        Assert-ScSuccess $create "create_service"
        if (-not $serviceCreated) {
            throw "service_create_not_observed"
        }
        $sidtype = Invoke-Sc @("sidtype", $Plan.service_name, "restricted")
        Assert-ScSuccess $sidtype "configure_restricted_sid"
        $restrictedConfigured = $true
        $qsidtype = Invoke-Sc @("qsidtype", $Plan.service_name)
        Assert-ScSuccess $qsidtype "query_restricted_sid_configuration"
        $serviceSid = (
            [Security.Principal.NTAccount]::new(
                "NT SERVICE\$($Plan.service_name)"
            ).Translate([Security.Principal.SecurityIdentifier]).Value
        )
        $artifactRights = @{
            "S-1-5-18" = [Security.AccessControl.FileSystemRights]::FullControl
            "S-1-5-32-544" = [Security.AccessControl.FileSystemRights]::FullControl
            $serviceSid = [Security.AccessControl.FileSystemRights]::ReadAndExecute
        }
        $stateRights = @{
            "S-1-5-18" = [Security.AccessControl.FileSystemRights]::FullControl
            "S-1-5-32-544" = [Security.AccessControl.FileSystemRights]::FullControl
            $serviceSid = [Security.AccessControl.FileSystemRights]::FullControl
        }
        Set-DirectorySecurity $Plan.artifact_root $artifactRights
        Set-FileSecurity $Plan.artifact_path $artifactRights
        Set-DirectorySecurity $Plan.state_root $stateRights

        $service = Get-ServiceSnapshot $Plan.service_name
        if (-not (Test-ServiceBoundToPlan $service $Plan $imagePath)) {
            throw "configured_service_binding_mismatch"
        }
        $observation = [ordered]@{
            create = $create
            sidtype = $sidtype
            qsidtype = $qsidtype
            service = $service
            artifact_sha256 = Get-FileSha256 $Plan.artifact_path
            artifact_acl = Get-AclSnapshot $Plan.artifact_path
            state_acl = Get-AclSnapshot $Plan.state_root
            service_started = $false
            probe_created = $false
            restricted_service_sid_configuration = (
                "configuration_write_accepted_before_cleanup"
            )
            running_token_restricted_sid = "not_observed"
        }
    }
    catch {
        $errorMessage = $_.Exception.Message
    }
    finally {
        $cleanup = Invoke-Cleanup `
            $Plan `
            $serviceCreated `
            $imagePath `
            $created
        $report = [ordered]@{
            schema = "agentic-evo.windows-gate-b-config-probe.v2"
            lab_id = $Plan.lab_id
            run_id = $Plan.run_id
            challenge = $Challenge
            plan_sha256 = $PlanSha256
            script_sha256 = $ExpectedScriptSha256
            environment_sha256 = $Plan.environment_sha256
            status = $(if ($errorMessage) {
                "failed"
            } else {
                "configuration_probe_completed"
            })
            error = $errorMessage
            elevated_administrator = $true
            observation = $observation
            cleanup = $cleanup
            claims = [ordered]@{
                gate_a_complete = $false
                gate_b_outcome = "not_established"
                native_security_verified = $false
                ready_to_install = $false
                temporary_service_created = $serviceCreated
                restricted_sid_configured = $restrictedConfigured
                restricted_service_sid_configuration_write = $(
                    if ($restrictedConfigured) {
                        "accepted_before_cleanup"
                    } else { "not_run" }
                )
                reboot_validation = $(
                    if ($restrictedConfigured) {
                        if ($cleanup.service_absent) {
                            "not_performed_service_removed"
                        } else {
                            "not_performed_service_cleanup_failed"
                        }
                    } else { "not_run" }
                )
                genesis_requested = $false
                genesis_count = "not_measured"
                all_attack_cases = "not_run"
                U01 = $(if (
                    $cleanup.service_created_or_reconciled -and
                    $cleanup.complete
                ) {
                    "partial_cleanup_pass_genesis_not_measured"
                } elseif ($cleanup.service_created_or_reconciled) {
                    "fail"
                } else {
                    "not_run"
                })
            }
        }
        $writer.WriteLine((ConvertTo-CompactJson $report))
        $writer.Dispose()
        $pipe.Dispose()
        if ($errorMessage -or -not $cleanup.complete) {
            exit 1
        }
    }
}

function ConvertTo-Base64Utf8([string]$Value) {
    return [Convert]::ToBase64String(
        [Text.UTF8Encoding]::new($false).GetBytes($Value)
    )
}

function New-Bootstrap(
    $Plan,
    [string]$ScriptSha,
    [string]$PlanDigest,
    [string]$ChallengeValue,
    [string]$Pipe
) {
    $scriptPath64 = ConvertTo-Base64Utf8 $VerifiedScriptPath
    $artifact64 = ConvertTo-Base64Utf8 $Plan.source_artifact
    $evidence64 = ConvertTo-Base64Utf8 $Plan.evidence_root
    return @"
`$ErrorActionPreference = "Stop"
function Decode([string]`$Value) {
    [Text.UTF8Encoding]::new(`$false, `$true).GetString(
        [Convert]::FromBase64String(`$Value)
    )
}
`$path = Decode("$scriptPath64")
`$bytes = [IO.File]::ReadAllBytes(`$path)
`$hash = [Security.Cryptography.SHA256]::Create()
try {
    `$actual = ([BitConverter]::ToString(
        `$hash.ComputeHash(`$bytes)
    )).Replace("-", "").ToLowerInvariant()
}
finally {
    `$hash.Dispose()
}
if (`$actual -ne "$ScriptSha") {
    throw "bootstrap_script_sha256_mismatch"
}
`$source = [Text.UTF8Encoding]::new(`$false, `$true).GetString(`$bytes)
& ([ScriptBlock]::Create(`$source)) ``
    -Mode Elevated ``
    -RunId "$($Plan.run_id)" ``
    -LabId "$($Plan.lab_id)" ``
    -ArtifactPath (Decode("$artifact64")) ``
    -ExpectedArtifactSha256 "$($Plan.artifact_sha256)" ``
    -EvidenceRoot (Decode("$evidence64")) ``
    -ExpectedScriptSha256 "$ScriptSha" ``
    -VerifiedScriptPath (Decode("$scriptPath64")) ``
    -PlanSha256 "$PlanDigest" ``
    -Challenge "$ChallengeValue" ``
    -PipeName "$Pipe" ``
    -TimeoutSeconds $TimeoutSeconds
"@
}

function Invoke-Controller($Plan, [string]$PlanJson) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (
        $principal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator
        )
    ) {
        throw "Run mode must begin non-elevated"
    }
    if (
        $ExpectedScriptSha256 -notmatch "^[0-9a-f]{64}$" -or
        [string]::IsNullOrWhiteSpace($VerifiedScriptPath)
    ) {
        throw "run_requires_externally_pinned_script"
    }
    $verifiedPath = [IO.Path]::GetFullPath($VerifiedScriptPath)
    if (-not [IO.File]::Exists($verifiedPath)) {
        throw "verified_script_missing"
    }
    Assert-NoReparsePath $verifiedPath
    if ((Get-FileSha256 $verifiedPath) -ne $ExpectedScriptSha256) {
        throw "verified_script_sha256_mismatch"
    }
    $script:VerifiedScriptPath = $verifiedPath
    if (Test-ObjectExists $Plan.evidence_root) {
        throw "evidence_root_must_not_exist"
    }
    [void][IO.Directory]::CreateDirectory($Plan.evidence_root)
    Write-AtomicJson (Join-Path $Plan.evidence_root "plan.json") $Plan

    $scriptSha = $ExpectedScriptSha256
    $planDigest = Get-TextSha256 $PlanJson
    $challengeValue = [Guid]::NewGuid().ToString("N")
    $pipeName = "AgenticEvoGateB-$($Plan.run_id)-$challengeValue"
    $server = New-ControllerPipe $pipeName $identity.User.Value
    $connection = $server.WaitForConnectionAsync()
    $bootstrap = New-Bootstrap `
        $Plan `
        $scriptSha `
        $planDigest `
        $challengeValue `
        $pipeName
    $encoded = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($bootstrap)
    )
    try {
        $elevated = Start-Process `
            -FilePath $script:PowerShellExe `
            -ArgumentList @("-NoProfile", "-NonInteractive", "-EncodedCommand", $encoded) `
            -Verb RunAs `
            -PassThru `
            -WindowStyle Hidden
    }
    catch {
        $server.Dispose()
        $exception = $_.Exception
        $wasCancelled = $false
        while ($null -ne $exception) {
            if (
                $null -ne $exception.PSObject.Properties["NativeErrorCode"] -and
                [int]$exception.NativeErrorCode -eq 1223
            ) {
                $wasCancelled = $true
                break
            }
            $exception = $exception.InnerException
        }
        $failedElevation = [ordered]@{
            schema = "agentic-evo.windows-gate-b-result.v2"
            lab_id = $Plan.lab_id
            run_id = $Plan.run_id
            challenge = $challengeValue
            plan_sha256 = $planDigest
            environment_sha256 = $Plan.environment_sha256
            status = $(if ($wasCancelled) {
                "elevation_cancelled"
            } else {
                "elevation_failed"
            })
            privileged_residue = $(if ($wasCancelled) {
                $false
            } else {
                "not_established"
            })
            error = $_.Exception.Message
        }
        Write-AtomicJson `
            (Join-Path $Plan.evidence_root "result.json") `
            $failedElevation
        Write-Output (ConvertTo-CompactJson $failedElevation)
        exit $(if ($wasCancelled) { 2 } else { 1 })
    }

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds + 120)
    while (-not $connection.IsCompleted -and [DateTime]::UtcNow -lt $deadline) {
        if ($elevated.HasExited) {
            throw "elevated_process_exited_before_pipe_connection"
        }
        Start-Sleep -Milliseconds 200
    }
    if (-not $connection.IsCompleted) {
        throw "elevated_pipe_connection_timeout"
    }
    [void]$connection.GetAwaiter().GetResult()
    $pipeClientPid = Get-PipeClientProcessId $server
    if ($pipeClientPid -ne $elevated.Id) {
        throw "elevated_pipe_client_pid_mismatch"
    }
    $server.ReadMode = [IO.Pipes.PipeTransmissionMode]::Message
    $reader = [IO.StreamReader]::new(
        $server,
        [Text.UTF8Encoding]::new($false, $true),
        $false,
        4096,
        $true
    )
    $report = Get-PipeMessage $reader $elevated $deadline
    $reader.Dispose()
    $server.Dispose()
    [void]$elevated.WaitForExit(
        [Math]::Max(1000, [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds)
    )
    if (-not $elevated.HasExited) {
        throw "elevated_cleanup_deadline_exceeded"
    }
    if (
        $report.lab_id -ne $Plan.lab_id -or
        $report.run_id -ne $Plan.run_id -or
        $report.challenge -ne $challengeValue -or
        $report.plan_sha256 -ne $planDigest -or
        $report.script_sha256 -ne $scriptSha -or
        $report.environment_sha256 -ne $Plan.environment_sha256
    ) {
        throw "elevated_report_binding_mismatch"
    }

    $serviceAbsent = $null -eq (Get-ServiceSnapshot $Plan.service_name)
    $artifactAbsent = -not (Test-ObjectExists $Plan.artifact_product_base)
    $stateAbsent = -not (Test-ObjectExists $Plan.state_product_base)
    $independentCleanup = [ordered]@{
        complete = $serviceAbsent -and $artifactAbsent -and $stateAbsent
        service_absent = $serviceAbsent
        artifact_tree_absent = $artifactAbsent
        state_tree_absent = $stateAbsent
    }
    $result = [ordered]@{
        schema = "agentic-evo.windows-gate-b-result.v2"
        lab_id = $Plan.lab_id
        run_id = $Plan.run_id
        challenge = $challengeValue
        plan_sha256 = $planDigest
        environment_sha256 = $Plan.environment_sha256
        status = $(
            if (
                $report.status -eq "configuration_probe_completed" -and
                $report.cleanup.complete -and
                $independentCleanup.complete -and
                $elevated.ExitCode -eq 0
            ) {
                "configuration_probe_completed"
            }
            else {
                "failed"
            }
        )
        gate_b_outcome = "not_established"
        elevated_exit_code = $elevated.ExitCode
        elevated_pipe_client_pid = $pipeClientPid
        report = $report
        independent_cleanup = $independentCleanup
    }
    if ($result.challenge -ne $report.challenge) {
        throw "result_challenge_binding_mismatch"
    }
    Write-AtomicJson (Join-Path $Plan.evidence_root "result.json") $result
    Write-Output (ConvertTo-CompactJson $result)
    if ($result.status -eq "failed") {
        exit 1
    }
}

$plan = Get-Plan
$planJson = ConvertTo-CompactJson $plan
switch ($Mode) {
    "Plan" { Write-Output $planJson }
    "Run" { Invoke-Controller $plan $planJson }
    "Elevated" { Invoke-Elevated $plan $planJson }
}
