"""Launch a suspended least-privileged AppContainer child on Windows.

The caller owns the AppContainer profile and the outer Job Object.  This
module creates no profiles, grants no ACLs, and supplies only the proven-
necessary ``registryRead`` capability.
"""

from __future__ import annotations

from collections.abc import Mapping
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import math
from pathlib import Path
import subprocess
import sys


_TOKEN_DUPLICATE = 0x0002
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_TOKEN_IS_APP_CONTAINER = 29
_TOKEN_CAPABILITIES = 30
_TOKEN_APP_CONTAINER_SID = 31
_HANDLE_FLAG_INHERIT = 0x00000001
_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_NO_WINDOW = 0x08000000
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY = 0x0002000F
_PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT = 0x00000001
_ERROR_INSUFFICIENT_BUFFER = 122
_SDDL_REVISION_1 = 1
_SECURITY_IMPERSONATION = 2
_TOKEN_IMPERSONATION = 2
_GENERIC_READ = 0x80000000
_FILE_GENERIC_READ = 0x00120089
_FILE_GENERIC_WRITE = 0x00120116
_FILE_GENERIC_EXECUTE = 0x001200A0
_FILE_ALL_ACCESS = 0x001F01FF
_ALL_APPLICATION_PACKAGES_SID = "S-1-15-2-1"
_ALLOWED_CAPABILITY_NAMES = ("registryRead",)
_SE_GROUP_ENABLED = 0x00000004
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [
        ("Sid", ctypes.c_void_p),
        ("Attributes", wintypes.DWORD),
    ]


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.POINTER(_SidAndAttributes)),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


class _TokenAppContainerInformation(ctypes.Structure):
    _fields_ = [("TokenAppContainer", ctypes.c_void_p)]


class _TokenGroups(ctypes.Structure):
    _fields_ = [
        ("GroupCount", wintypes.DWORD),
        ("Groups", _SidAndAttributes * 1),
    ]


class _GenericMapping(ctypes.Structure):
    _fields_ = [
        ("GenericRead", wintypes.DWORD),
        ("GenericWrite", wintypes.DWORD),
        ("GenericExecute", wintypes.DWORD),
        ("GenericAll", wintypes.DWORD),
    ]


class _StartupInfoW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoExW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _StartupInfoW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernelbase = ctypes.WinDLL("kernelbase", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    _kernel32.GetHandleInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetHandleInformation.restype = wintypes.BOOL
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.SetHandleInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _kernel32.SetHandleInformation.restype = wintypes.BOOL
    _kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    _kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    _kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    _kernel32.DeleteProcThreadAttributeList.restype = None
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL

    _kernelbase.DeriveCapabilitySidsFromName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernelbase.DeriveCapabilitySidsFromName.restype = wintypes.BOOL

    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    _advapi32.MakeAbsoluteSD.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.MakeAbsoluteSD.restype = wintypes.BOOL
    _advapi32.DuplicateTokenEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.DuplicateTokenEx.restype = wintypes.BOOL
    _advapi32.MapGenericMask.argtypes = [
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(_GenericMapping),
    ]
    _advapi32.MapGenericMask.restype = None
    _advapi32.AccessCheck.argtypes = [
        ctypes.c_void_p,
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(_GenericMapping),
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.BOOL),
    ]
    _advapi32.AccessCheck.restype = wintypes.BOOL
    _advapi32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_StartupInfoExW),
        ctypes.POINTER(_ProcessInformation),
    ]
    _advapi32.CreateProcessAsUserW.restype = wintypes.BOOL


@dataclass(frozen=True, slots=True)
class LpacTokenProfile:
    is_app_container: bool
    is_less_privileged_app_container: bool
    capability_count: int
    capability_sids: tuple[str, ...]
    appcontainer_sid: str


@dataclass(slots=True)
class _NativeCapabilitySid:
    sid: int
    sid_string: str
    _group_sids: ctypes.POINTER(ctypes.c_void_p)
    _group_sid_count: int
    _capability_sids: ctypes.POINTER(ctypes.c_void_p)
    _capability_sid_count: int
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _free_derived_sid_array(self._group_sids, self._group_sid_count)
        _free_derived_sid_array(
            self._capability_sids,
            self._capability_sid_count,
        )


class LpacWindowsProcess:
    """Own an LPAC child created in the suspended state."""

    def __init__(
        self,
        *,
        command: tuple[str, ...],
        process_handle: int,
        thread_handle: int,
        pid: int,
        token_profile: LpacTokenProfile,
    ) -> None:
        self._command = command
        self._process_handle: int | None = process_handle
        self._thread_handle: int | None = thread_handle
        self._returncode: int | None = None
        self.pid = pid
        self.token_profile = token_profile

    @property
    def process_handle(self) -> int:
        if self._process_handle is None:
            raise OSError("LPAC Windows process is closed")
        return self._process_handle

    def resume(self) -> int:
        thread_handle = self._thread_handle
        if thread_handle is None:
            raise RuntimeError("LPAC Windows process was already resumed")
        previous_count = _kernel32.ResumeThread(thread_handle)
        if previous_count == _WAIT_FAILED:
            error = ctypes.get_last_error()
            _kernel32.CloseHandle(thread_handle)
            self._thread_handle = None
            self._terminate_if_live()
            raise ctypes.WinError(error)
        _kernel32.CloseHandle(thread_handle)
        self._thread_handle = None
        if previous_count != 1:
            self._terminate_if_live()
            raise OSError("LPAC Windows process did not have exactly one suspend count")
        return int(previous_count)

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        handle = self._process_handle
        if handle is None:
            raise OSError("LPAC Windows process is closed")
        result = _kernel32.WaitForSingleObject(handle, 0)
        if result == _WAIT_TIMEOUT:
            return None
        if result == _WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        if result != _WAIT_OBJECT_0:
            raise OSError(f"unexpected Windows wait result: {result}")
        self._returncode = _exit_code(handle)
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        if self._returncode is not None:
            return self._returncode
        handle = self._process_handle
        if handle is None:
            raise OSError("LPAC Windows process is closed")
        result = _kernel32.WaitForSingleObject(handle, _wait_timeout_ms(timeout))
        if result == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self._command, timeout)
        if result == _WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        if result != _WAIT_OBJECT_0:
            raise OSError(f"unexpected Windows wait result: {result}")
        self._returncode = _exit_code(handle)
        return self._returncode

    def terminate(self) -> None:
        self._terminate_if_live()

    def kill(self) -> None:
        self.terminate()

    def close(self) -> None:
        process_handle = self._process_handle
        thread_handle = self._thread_handle
        if process_handle is None:
            return
        if thread_handle is not None:
            self._terminate_if_live()
            _kernel32.WaitForSingleObject(process_handle, 5_000)
            if self._returncode is None:
                try:
                    self._returncode = _exit_code(process_handle)
                except OSError:
                    pass
            _kernel32.CloseHandle(thread_handle)
            self._thread_handle = None
        _kernel32.CloseHandle(process_handle)
        self._process_handle = None

    def _terminate_if_live(self) -> None:
        handle = self._process_handle
        if handle is None or self.poll() is not None:
            return
        if not _kernel32.TerminateProcess(handle, 1):
            error = ctypes.get_last_error()
            if self.poll() is None:
                raise ctypes.WinError(error)


def capability_sids_for_names(names: tuple[str, ...]) -> tuple[str, ...]:
    """Return the canonical capability SID set for the fixed LPAC contract."""

    normalized_names = _validate_capability_names(names)
    derived = _derive_capability_sids(normalized_names)
    try:
        return tuple(sorted(item.sid_string for item in derived))
    finally:
        for item in derived:
            item.close()


def spawn_lpac_suspended_process(
    command: tuple[str, ...],
    *,
    inherited_handles: tuple[int, ...],
    cwd: Path,
    environment: Mapping[str, str],
    appcontainer_sid: int,
    capability_names: tuple[str, ...] = _ALLOWED_CAPABILITY_NAMES,
) -> LpacWindowsProcess:
    """Launch one registryRead-only LPAC process with exactly given handles.

    ``appcontainer_sid`` is an owned PSID supplied by the caller and must stay
    valid for the duration of this call.  The caller assigns the returned
    process to its Job Object before calling :meth:`LpacWindowsProcess.resume`.
    """

    _require_windows()
    normalized_handles = _validate_inherited_handles(inherited_handles)
    normalized_capability_names = _validate_capability_names(capability_names)
    try:
        if appcontainer_sid <= 0:
            raise ValueError("appcontainer SID must be a non-null PSID address")
        return _spawn_lpac_suspended_process(
            command,
            inherited_handles=normalized_handles,
            cwd=cwd,
            environment=environment,
            appcontainer_sid=appcontainer_sid,
            capability_names=normalized_capability_names,
        )
    finally:
        _best_effort_clear_handle_inheritance(normalized_handles)


def _spawn_lpac_suspended_process(
    command: tuple[str, ...],
    *,
    inherited_handles: tuple[int, ...],
    cwd: Path,
    environment: Mapping[str, str],
    appcontainer_sid: int,
    capability_names: tuple[str, ...],
) -> LpacWindowsProcess:
    normalized_command = _validate_command(command)
    cwd_text = str(cwd)
    if "\0" in cwd_text:
        raise ValueError("working directory contains NUL")
    expected_sid = _sid_to_string(appcontainer_sid)
    environment_buffer = _environment_block(environment)
    command_buffer = ctypes.create_unicode_buffer(
        subprocess.list2cmdline(normalized_command)
    )
    handle_array = (wintypes.HANDLE * len(inherited_handles))(*inherited_handles)
    all_application_packages_policy = wintypes.DWORD(
        _PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT
    )
    attribute_size = ctypes.c_size_t()
    _kernel32.InitializeProcThreadAttributeList(
        None,
        3,
        0,
        ctypes.byref(attribute_size),
    )
    if not attribute_size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
    attribute_list = ctypes.cast(attribute_buffer, ctypes.c_void_p)
    if not _kernel32.InitializeProcThreadAttributeList(
        attribute_list,
        3,
        0,
        ctypes.byref(attribute_size),
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    process_info = _ProcessInformation()
    transferred = False
    derived_capabilities: tuple[_NativeCapabilitySid, ...] = ()
    try:
        derived_capabilities = _derive_capability_sids(capability_names)
        expected_capability_sids = tuple(
            sorted(item.sid_string for item in derived_capabilities)
        )
        capability_array = (_SidAndAttributes * len(derived_capabilities))(
            *(
                _SidAndAttributes(
                    ctypes.c_void_p(item.sid),
                    _SE_GROUP_ENABLED,
                )
                for item in derived_capabilities
            )
        )
        security_capabilities = _SecurityCapabilities()
        security_capabilities.AppContainerSid = appcontainer_sid
        security_capabilities.Capabilities = ctypes.cast(
            capability_array,
            ctypes.POINTER(_SidAndAttributes),
        )
        security_capabilities.CapabilityCount = len(derived_capabilities)
        security_capabilities.Reserved = 0
        _update_process_attribute(
            attribute_list,
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(handle_array, ctypes.c_void_p),
            ctypes.sizeof(handle_array),
        )
        _update_process_attribute(
            attribute_list,
            _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
            ctypes.cast(ctypes.byref(security_capabilities), ctypes.c_void_p),
            ctypes.sizeof(security_capabilities),
        )
        _update_process_attribute(
            attribute_list,
            _PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY,
            ctypes.cast(
                ctypes.byref(all_application_packages_policy),
                ctypes.c_void_p,
            ),
            ctypes.sizeof(all_application_packages_policy),
        )

        startup = _StartupInfoExW()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.lpAttributeList = attribute_list.value
        created = _advapi32.CreateProcessAsUserW(
            None,
            normalized_command[0],
            command_buffer,
            None,
            None,
            True,
            (
                _CREATE_SUSPENDED
                | _CREATE_UNICODE_ENVIRONMENT
                | _EXTENDED_STARTUPINFO_PRESENT
                | _CREATE_NO_WINDOW
            ),
            ctypes.cast(environment_buffer, ctypes.c_void_p),
            cwd_text,
            ctypes.byref(startup),
            ctypes.byref(process_info),
        )
        create_error = ctypes.get_last_error()
        _clear_handle_inheritance(inherited_handles)
        if not created:
            raise ctypes.WinError(create_error)

        profile = _query_lpac_token_profile(int(process_info.hProcess))
        _verify_expected_lpac_token_profile(
            profile,
            expected_appcontainer_sid=expected_sid,
            expected_capability_sids=expected_capability_sids,
        )
        _verify_lpac_access_semantics(
            int(process_info.hProcess),
            package_sid=profile.appcontainer_sid,
        )
        profile = LpacTokenProfile(
            is_app_container=profile.is_app_container,
            is_less_privileged_app_container=True,
            capability_count=profile.capability_count,
            capability_sids=profile.capability_sids,
            appcontainer_sid=profile.appcontainer_sid,
        )
        process = LpacWindowsProcess(
            command=normalized_command,
            process_handle=int(process_info.hProcess),
            thread_handle=int(process_info.hThread),
            pid=int(process_info.dwProcessId),
            token_profile=profile,
        )
        transferred = True
        return process
    finally:
        _kernel32.DeleteProcThreadAttributeList(attribute_list)
        if not transferred:
            _discard_suspended_process(process_info)
        for item in derived_capabilities:
            item.close()


def _require_windows() -> None:
    if sys.platform != "win32":
        raise OSError("LPAC launching is only available on Windows")


def _update_process_attribute(
    attribute_list: ctypes.c_void_p,
    attribute: int,
    value: ctypes.c_void_p,
    size: int,
) -> None:
    if not _kernel32.UpdateProcThreadAttribute(
        attribute_list,
        0,
        attribute,
        value,
        size,
        None,
        None,
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def _validate_command(command: tuple[str, ...]) -> tuple[str, ...]:
    if not command:
        raise ValueError("command cannot be empty")
    if any(not isinstance(part, str) or "\0" in part for part in command):
        raise ValueError("command entries must be strings without NUL")
    return tuple(command)


def _validate_inherited_handles(handles: tuple[int, ...]) -> tuple[int, ...]:
    if not handles:
        raise ValueError("at least one inherited handle is required")
    normalized = tuple(int(handle) for handle in handles)
    if len(set(normalized)) != len(normalized):
        raise ValueError("inherited handles must be unique")
    for handle in normalized:
        flags = wintypes.DWORD()
        if not _kernel32.GetHandleInformation(handle, ctypes.byref(flags)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not flags.value & _HANDLE_FLAG_INHERIT:
            raise ValueError("every inherited handle must be marked inheritable")
    return normalized


def _validate_capability_names(names: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(names, tuple) or names != _ALLOWED_CAPABILITY_NAMES:
        raise ValueError("LPAC capabilities must be exactly ('registryRead',)")
    return names


def _derive_capability_sids(
    names: tuple[str, ...],
) -> tuple[_NativeCapabilitySid, ...]:
    _require_windows()
    derived: list[_NativeCapabilitySid] = []
    try:
        for name in names:
            derived.append(_derive_capability_sid(name))
        sid_strings = tuple(sorted(item.sid_string for item in derived))
        if len(sid_strings) != len(set(sid_strings)):
            raise PermissionError("derived LPAC capability SIDs were not unique")
        return tuple(derived)
    except BaseException:
        for item in derived:
            item.close()
        raise


def _derive_capability_sid(name: str) -> _NativeCapabilitySid:
    group_sids = ctypes.POINTER(ctypes.c_void_p)()
    group_sid_count = wintypes.DWORD()
    capability_sids = ctypes.POINTER(ctypes.c_void_p)()
    capability_sid_count = wintypes.DWORD()
    if not _kernelbase.DeriveCapabilitySidsFromName(
        name,
        ctypes.byref(group_sids),
        ctypes.byref(group_sid_count),
        ctypes.byref(capability_sids),
        ctypes.byref(capability_sid_count),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if capability_sid_count.value != 1 or not capability_sids:
            raise PermissionError(
                "registryRead did not derive exactly one capability SID"
            )
        sid = int(capability_sids[0])
        if sid <= 0:
            raise PermissionError("registryRead derived a null capability SID")
        return _NativeCapabilitySid(
            sid=sid,
            sid_string=_sid_to_string(sid),
            _group_sids=group_sids,
            _group_sid_count=int(group_sid_count.value),
            _capability_sids=capability_sids,
            _capability_sid_count=int(capability_sid_count.value),
        )
    except BaseException:
        _free_derived_sid_array(group_sids, int(group_sid_count.value))
        _free_derived_sid_array(capability_sids, int(capability_sid_count.value))
        raise


def _free_derived_sid_array(
    sid_array: ctypes.POINTER(ctypes.c_void_p),
    count: int,
) -> None:
    if not sid_array:
        return
    for index in range(count):
        sid = sid_array[index]
        if sid:
            _kernel32.LocalFree(ctypes.c_void_p(sid))
    _kernel32.LocalFree(ctypes.cast(sid_array, wintypes.HLOCAL))


def _environment_block(environment: Mapping[str, str]) -> ctypes.Array:
    entries: list[tuple[str, str]] = []
    folded_keys: set[str] = set()
    for key, value in environment.items():
        if not isinstance(key, str) or not key or "\0" in key or "=" in key:
            raise ValueError("environment names must be non-empty without NUL or '='")
        if not isinstance(value, str) or "\0" in value:
            raise ValueError("environment values must be strings without NUL")
        folded = key.casefold()
        if folded in folded_keys:
            raise ValueError("environment names must be unique ignoring case")
        folded_keys.add(folded)
        entries.append((key, value))
    entries.sort(key=lambda item: item[0].casefold())
    block = "\0".join(f"{key}={value}" for key, value in entries) + "\0\0"
    return ctypes.create_unicode_buffer(block, len(block))


def _query_lpac_token_profile(process_handle: int) -> LpacTokenProfile:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        process_handle,
        _TOKEN_QUERY,
        ctypes.byref(token),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        capability_sids = _token_capability_sids(token)
        return LpacTokenProfile(
            is_app_container=bool(_token_dword(token, _TOKEN_IS_APP_CONTAINER)),
            is_less_privileged_app_container=False,
            capability_count=len(capability_sids),
            capability_sids=capability_sids,
            appcontainer_sid=_token_appcontainer_sid(token),
        )
    finally:
        _kernel32.CloseHandle(token)


def _verify_expected_lpac_token_profile(
    profile: LpacTokenProfile,
    *,
    expected_appcontainer_sid: str,
    expected_capability_sids: tuple[str, ...],
) -> None:
    if not profile.is_app_container:
        raise PermissionError("child token is not an AppContainer")
    if profile.appcontainer_sid != expected_appcontainer_sid:
        raise PermissionError("child token AppContainer SID did not match")
    actual_capability_sids = tuple(sorted(profile.capability_sids))
    if (
        profile.capability_count != len(profile.capability_sids)
        or len(actual_capability_sids) != len(set(actual_capability_sids))
        or actual_capability_sids != expected_capability_sids
    ):
        raise PermissionError("child token capabilities did not exactly match")


def _verify_lpac_access_semantics(
    process_handle: int,
    *,
    package_sid: str,
) -> None:
    """Verify LPAC behavior with two in-memory, owner-complete descriptors."""

    client_token = _duplicate_impersonation_token(process_handle)
    try:
        user_sid = _current_user_sid()
        if _access_check_read(
            client_token,
            owner_sid=user_sid,
            granted_sid=_ALL_APPLICATION_PACKAGES_SID,
        ):
            raise PermissionError(
                "child token retained ALL_APPLICATION_PACKAGES access"
            )
        if not _access_check_read(
            client_token,
            owner_sid=user_sid,
            granted_sid=package_sid,
        ):
            raise PermissionError(
                "child token did not retain explicit AppContainer access"
            )
    finally:
        _kernel32.CloseHandle(client_token)


def _duplicate_impersonation_token(process_handle: int) -> int:
    primary_token = wintypes.HANDLE()
    impersonation_token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        process_handle,
        _TOKEN_DUPLICATE | _TOKEN_QUERY,
        ctypes.byref(primary_token),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not _advapi32.DuplicateTokenEx(
            primary_token,
            _TOKEN_QUERY,
            None,
            _SECURITY_IMPERSONATION,
            _TOKEN_IMPERSONATION,
            ctypes.byref(impersonation_token),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(impersonation_token.value)
    finally:
        _kernel32.CloseHandle(primary_token)


def _current_user_sid() -> str:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(),
        _TOKEN_QUERY,
        ctypes.byref(token),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buffer = _token_information(token, _TOKEN_USER)
        user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        if not user.User.Sid:
            raise OSError("current process token has no user SID")
        return _sid_to_string(int(user.User.Sid))
    finally:
        _kernel32.CloseHandle(token)


def _access_check_read(
    client_token: int,
    *,
    owner_sid: str,
    granted_sid: str,
) -> bool:
    descriptor = ctypes.c_void_p()
    descriptor_size = wintypes.DWORD()
    sddl = (
        f"O:{owner_sid}G:{owner_sid}D:P"
        f"(A;;0x{_FILE_GENERIC_READ:08X};;;{owner_sid})"
        f"(A;;0x{_FILE_GENERIC_READ:08X};;;{granted_sid})"
    )
    if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        _SDDL_REVISION_1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        absolute_descriptor, keepalive = _make_absolute_security_descriptor(
            descriptor
        )
        return _access_check(
            absolute_descriptor,
            client_token,
            keepalive=keepalive,
        )
    finally:
        _kernel32.LocalFree(descriptor)


def _make_absolute_security_descriptor(
    self_relative_descriptor: ctypes.c_void_p,
) -> tuple[ctypes.Array, tuple[ctypes.Array | None, ...]]:
    sizes = [wintypes.DWORD() for _ in range(5)]
    if _advapi32.MakeAbsoluteSD(
        self_relative_descriptor,
        None,
        ctypes.byref(sizes[0]),
        None,
        ctypes.byref(sizes[1]),
        None,
        ctypes.byref(sizes[2]),
        None,
        ctypes.byref(sizes[3]),
        None,
        ctypes.byref(sizes[4]),
    ):
        raise OSError("MakeAbsoluteSD unexpectedly completed without buffers")
    error = ctypes.get_last_error()
    if error != _ERROR_INSUFFICIENT_BUFFER:
        raise ctypes.WinError(error)
    buffers = tuple(
        ctypes.create_string_buffer(size.value) if size.value else None
        for size in sizes
    )
    absolute_descriptor = buffers[0]
    if absolute_descriptor is None:
        raise OSError("MakeAbsoluteSD returned no absolute descriptor size")
    if not _advapi32.MakeAbsoluteSD(
        self_relative_descriptor,
        absolute_descriptor,
        ctypes.byref(sizes[0]),
        buffers[1],
        ctypes.byref(sizes[1]),
        buffers[2],
        ctypes.byref(sizes[2]),
        buffers[3],
        ctypes.byref(sizes[3]),
        buffers[4],
        ctypes.byref(sizes[4]),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return absolute_descriptor, buffers


def _access_check(
    absolute_descriptor: ctypes.Array,
    client_token: int,
    *,
    keepalive: tuple[ctypes.Array | None, ...],
) -> bool:
    # The absolute descriptor contains pointers into these buffers.
    _ = keepalive
    mapping = _GenericMapping(
        _FILE_GENERIC_READ,
        _FILE_GENERIC_WRITE,
        _FILE_GENERIC_EXECUTE,
        _FILE_ALL_ACCESS,
    )
    desired_access = wintypes.DWORD(_GENERIC_READ)
    _advapi32.MapGenericMask(
        ctypes.byref(desired_access),
        ctypes.byref(mapping),
    )
    privilege_size = wintypes.DWORD()
    granted_access = wintypes.DWORD()
    access_status = wintypes.BOOL()
    if _advapi32.AccessCheck(
        absolute_descriptor,
        client_token,
        desired_access,
        ctypes.byref(mapping),
        None,
        ctypes.byref(privilege_size),
        ctypes.byref(granted_access),
        ctypes.byref(access_status),
    ):
        return bool(access_status.value)
    error = ctypes.get_last_error()
    if error != _ERROR_INSUFFICIENT_BUFFER:
        raise ctypes.WinError(error)
    privilege_set = ctypes.create_string_buffer(privilege_size.value)
    if not _advapi32.AccessCheck(
        absolute_descriptor,
        client_token,
        desired_access,
        ctypes.byref(mapping),
        privilege_set,
        ctypes.byref(privilege_size),
        ctypes.byref(granted_access),
        ctypes.byref(access_status),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(access_status.value)


def _token_dword(token: wintypes.HANDLE, information_class: int) -> int:
    buffer = _token_information(token, information_class)
    return int(ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value)


def _token_capability_sids(token: wintypes.HANDLE) -> tuple[str, ...]:
    buffer = _token_information(token, _TOKEN_CAPABILITIES)
    groups = ctypes.cast(buffer, ctypes.POINTER(_TokenGroups)).contents
    count = int(groups.GroupCount)
    required_size = _TokenGroups.Groups.offset + count * ctypes.sizeof(
        _SidAndAttributes
    )
    if ctypes.sizeof(buffer) < required_size:
        raise OSError("TokenCapabilities buffer was truncated")
    group_array = ctypes.cast(
        ctypes.c_void_p(ctypes.addressof(buffer) + _TokenGroups.Groups.offset),
        ctypes.POINTER(_SidAndAttributes),
    )
    capability_sids: list[str] = []
    for index in range(count):
        capability = group_array[index]
        sid = capability.Sid
        if not sid:
            raise PermissionError("child token included a null capability SID")
        if not capability.Attributes & _SE_GROUP_ENABLED:
            raise PermissionError("child token capability SID was not enabled")
        capability_sids.append(_sid_to_string(int(sid)))
    return tuple(sorted(capability_sids))


def _token_appcontainer_sid(token: wintypes.HANDLE) -> str:
    buffer = _token_information(token, _TOKEN_APP_CONTAINER_SID)
    information = ctypes.cast(
        buffer,
        ctypes.POINTER(_TokenAppContainerInformation),
    ).contents
    if not information.TokenAppContainer:
        raise PermissionError("child token has no AppContainer SID")
    return _sid_to_string(int(information.TokenAppContainer))


def _token_information(
    token: wintypes.HANDLE,
    information_class: int,
) -> ctypes.Array:
    required = wintypes.DWORD()
    if _advapi32.GetTokenInformation(
        token,
        information_class,
        None,
        0,
        ctypes.byref(required),
    ):
        raise OSError("token information unexpectedly required no buffer")
    error = ctypes.get_last_error()
    if error != _ERROR_INSUFFICIENT_BUFFER:
        raise ctypes.WinError(error)
    buffer = ctypes.create_string_buffer(required.value)
    if not _advapi32.GetTokenInformation(
        token,
        information_class,
        buffer,
        required.value,
        ctypes.byref(required),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer


def _sid_to_string(sid: int) -> str:
    rendered = wintypes.LPWSTR()
    if not _advapi32.ConvertSidToStringSidW(
        ctypes.c_void_p(sid),
        ctypes.byref(rendered),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if rendered.value is None:
            raise OSError("ConvertSidToStringSidW returned an empty SID")
        return rendered.value
    finally:
        _kernel32.LocalFree(ctypes.cast(rendered, wintypes.HLOCAL))


def _clear_handle_inheritance(handles: tuple[int, ...]) -> None:
    for handle in handles:
        if not _kernel32.SetHandleInformation(handle, _HANDLE_FLAG_INHERIT, 0):
            raise ctypes.WinError(ctypes.get_last_error())


def _best_effort_clear_handle_inheritance(handles: tuple[int, ...]) -> None:
    for handle in handles:
        _kernel32.SetHandleInformation(handle, _HANDLE_FLAG_INHERIT, 0)


def _discard_suspended_process(process_info: _ProcessInformation) -> None:
    if process_info.hProcess:
        _kernel32.TerminateProcess(process_info.hProcess, 1)
        _kernel32.WaitForSingleObject(process_info.hProcess, 5_000)
    if process_info.hThread:
        _kernel32.CloseHandle(process_info.hThread)
    if process_info.hProcess:
        _kernel32.CloseHandle(process_info.hProcess)


def _exit_code(process_handle: int) -> int:
    code = wintypes.DWORD()
    if not _kernel32.GetExitCodeProcess(process_handle, ctypes.byref(code)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(code.value)


def _wait_timeout_ms(timeout: float | None) -> int:
    if timeout is None:
        return _INFINITE
    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    return min(math.ceil(timeout * 1_000), _INFINITE - 1)
