from __future__ import annotations

from collections.abc import Mapping
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import math
from pathlib import Path
import subprocess


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_TOKEN_ASSIGN_PRIMARY = 0x0001
_TOKEN_DUPLICATE = 0x0002
_TOKEN_QUERY = 0x0008
_TOKEN_ADJUST_DEFAULT = 0x0080
_TOKEN_USER = 1
_TOKEN_GROUPS = 2
_TOKEN_PRIVILEGES = 3
_TOKEN_INTEGRITY_LEVEL = 25
_DISABLE_MAX_PRIVILEGE = 0x0001
_SE_GROUP_ENABLED = 0x00000004
_SE_GROUP_USE_FOR_DENY_ONLY = 0x00000010
_SE_GROUP_INTEGRITY = 0x00000020
_HANDLE_FLAG_INHERIT = 0x00000001
_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_NO_WINDOW = 0x08000000
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_ERROR_INSUFFICIENT_BUFFER = 122
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF
_LOW_INTEGRITY_RID = 4096


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [
        ("Sid", ctypes.c_void_p),
        ("Attributes", wintypes.DWORD),
    ]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


class _TokenGroups(ctypes.Structure):
    _fields_ = [
        ("GroupCount", wintypes.DWORD),
        ("Groups", _SidAndAttributes * 1),
    ]


class _TokenMandatoryLabel(ctypes.Structure):
    _fields_ = [("Label", _SidAndAttributes)]


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


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
_kernel32.CreateJobObjectW.restype = wintypes.HANDLE
_kernel32.SetInformationJobObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
_kernel32.SetInformationJobObject.restype = wintypes.BOOL
_kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
_kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.GetCurrentProcess.argtypes = []
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetHandleInformation.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.GetHandleInformation.restype = wintypes.BOOL
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
_kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
_kernel32.LocalFree.restype = wintypes.HLOCAL
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
_advapi32.CreateRestrictedToken.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(_SidAndAttributes),
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_SidAndAttributes),
    ctypes.POINTER(wintypes.HANDLE),
]
_advapi32.CreateRestrictedToken.restype = wintypes.BOOL
_advapi32.SetTokenInformation.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
_advapi32.SetTokenInformation.restype = wintypes.BOOL
_advapi32.IsTokenRestricted.argtypes = [wintypes.HANDLE]
_advapi32.IsTokenRestricted.restype = wintypes.BOOL
_advapi32.ConvertStringSidToSidW.argtypes = [
    wintypes.LPCWSTR,
    ctypes.POINTER(ctypes.c_void_p),
]
_advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
_advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
_advapi32.GetLengthSid.restype = wintypes.DWORD
_advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
_advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(wintypes.BYTE)
_advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
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
class RestrictedTokenProfile:
    is_restricted: bool
    integrity_rid: int
    privilege_count: int


class RestrictedWindowsProcess:
    """Own a restricted Windows child created in the suspended state."""

    def __init__(
        self,
        *,
        command: tuple[str, ...],
        process_handle: int,
        thread_handle: int,
        pid: int,
        token_profile: RestrictedTokenProfile,
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
            raise OSError("restricted Windows process is closed")
        return self._process_handle

    def resume(self) -> int:
        thread_handle = self._thread_handle
        if thread_handle is None:
            raise RuntimeError("restricted Windows process was already resumed")
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
            raise OSError(
                "restricted Windows process did not have exactly one suspend count"
            )
        return int(previous_count)

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        handle = self._process_handle
        if handle is None:
            raise OSError("restricted Windows process is closed")
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
            raise OSError("restricted Windows process is closed")
        timeout_ms = _wait_timeout_ms(timeout)
        result = _kernel32.WaitForSingleObject(handle, timeout_ms)
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


class KillOnCloseJob:
    """Own one anonymous Windows Job Object that kills its process tree."""

    def __init__(self) -> None:
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle: int | None = handle
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not _kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign_handle(self, process_handle: int) -> None:
        if self._handle is None:
            raise OSError("Windows Job Object is closed")
        if not _kernel32.AssignProcessToJobObject(
            self._handle,
            process_handle,
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            _kernel32.CloseHandle(handle)


def spawn_restricted_suspended_process(
    command: tuple[str, ...],
    *,
    inherited_handles: tuple[int, ...],
    cwd: Path,
    environment: Mapping[str, str],
) -> RestrictedWindowsProcess:
    """Create one Low-IL restricted child with only explicit inherited handles."""

    normalized_handles = _validate_inherited_handles(inherited_handles)
    try:
        return _spawn_restricted_suspended_process(
            command,
            inherited_handles=normalized_handles,
            cwd=cwd,
            environment=environment,
        )
    finally:
        _best_effort_clear_handle_inheritance(normalized_handles)


def _spawn_restricted_suspended_process(
    command: tuple[str, ...],
    *,
    inherited_handles: tuple[int, ...],
    cwd: Path,
    environment: Mapping[str, str],
) -> RestrictedWindowsProcess:
    normalized_command = _validate_command(command)
    cwd_text = str(cwd)
    if "\0" in cwd_text:
        raise ValueError("working directory contains NUL")
    environment_buffer = _environment_block(environment)
    command_buffer = ctypes.create_unicode_buffer(
        subprocess.list2cmdline(normalized_command)
    )
    handle_array = (wintypes.HANDLE * len(inherited_handles))(
        *inherited_handles
    )
    attribute_size = ctypes.c_size_t()
    _kernel32.InitializeProcThreadAttributeList(
        None,
        1,
        0,
        ctypes.byref(attribute_size),
    )
    if not attribute_size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
    attribute_list = ctypes.cast(attribute_buffer, ctypes.c_void_p)
    if not _kernel32.InitializeProcThreadAttributeList(
        attribute_list,
        1,
        0,
        ctypes.byref(attribute_size),
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    process_info = _ProcessInformation()
    restricted_token: int | None = None
    transferred = False
    try:
        if not _kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(handle_array, ctypes.c_void_p),
            ctypes.sizeof(handle_array),
            None,
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        restricted_token = _create_low_restricted_token()
        startup = _StartupInfoExW()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.lpAttributeList = attribute_list.value
        created = _advapi32.CreateProcessAsUserW(
            restricted_token,
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

        profile = _query_restricted_token_profile(int(process_info.hProcess))
        if (
            not profile.is_restricted
            or profile.integrity_rid != _LOW_INTEGRITY_RID
            or profile.privilege_count > 1
        ):
            raise PermissionError(
                "child token did not satisfy the restricted Low-IL contract"
            )
        process = RestrictedWindowsProcess(
            command=normalized_command,
            process_handle=int(process_info.hProcess),
            thread_handle=int(process_info.hThread),
            pid=int(process_info.dwProcessId),
            token_profile=profile,
        )
        transferred = True
        return process
    finally:
        if restricted_token is not None:
            _kernel32.CloseHandle(restricted_token)
        _kernel32.DeleteProcThreadAttributeList(attribute_list)
        if not transferred:
            _discard_suspended_process(process_info)


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


def _create_low_restricted_token() -> int:
    source_token = wintypes.HANDLE()
    restricted_token = wintypes.HANDLE()
    desired_access = (
        _TOKEN_ASSIGN_PRIMARY
        | _TOKEN_DUPLICATE
        | _TOKEN_QUERY
        | _TOKEN_ADJUST_DEFAULT
    )
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(),
        desired_access,
        ctypes.byref(source_token),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        user_buffer = _token_information(source_token, _TOKEN_USER)
        groups_buffer = _token_information(source_token, _TOKEN_GROUPS)
        user = ctypes.cast(user_buffer, ctypes.POINTER(_TokenUser)).contents
        groups = ctypes.cast(groups_buffer, ctypes.POINTER(_TokenGroups)).contents
        group_count = int(groups.GroupCount)
        group_array = ctypes.cast(
            ctypes.addressof(groups_buffer) + _TokenGroups.Groups.offset,
            ctypes.POINTER(_SidAndAttributes * group_count),
        ).contents
        restricting_entries: list[_SidAndAttributes] = []
        user_entry = _SidAndAttributes()
        user_entry.Sid = user.User.Sid
        restricting_entries.append(user_entry)
        for group in group_array:
            if (
                group.Attributes & _SE_GROUP_ENABLED
                and not group.Attributes & _SE_GROUP_USE_FOR_DENY_ONLY
            ):
                entry = _SidAndAttributes()
                entry.Sid = group.Sid
                restricting_entries.append(entry)
        restricting_array = (_SidAndAttributes * len(restricting_entries))(
            *restricting_entries
        )
        if not _advapi32.CreateRestrictedToken(
            source_token,
            _DISABLE_MAX_PRIVILEGE,
            0,
            None,
            0,
            None,
            len(restricting_entries),
            restricting_array,
            ctypes.byref(restricted_token),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            _set_low_integrity(restricted_token)
            return int(restricted_token.value)
        except BaseException:
            _kernel32.CloseHandle(restricted_token)
            raise
    finally:
        _kernel32.CloseHandle(source_token)


def _set_low_integrity(token: int) -> None:
    low_sid = ctypes.c_void_p()
    if not _advapi32.ConvertStringSidToSidW(
        "S-1-16-4096",
        ctypes.byref(low_sid),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        label = _TokenMandatoryLabel()
        label.Label.Sid = low_sid.value
        label.Label.Attributes = _SE_GROUP_INTEGRITY
        information_size = ctypes.sizeof(label) + _advapi32.GetLengthSid(low_sid)
        if not _advapi32.SetTokenInformation(
            token,
            _TOKEN_INTEGRITY_LEVEL,
            ctypes.byref(label),
            information_size,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _kernel32.LocalFree(low_sid)


def _query_restricted_token_profile(process_handle: int) -> RestrictedTokenProfile:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        process_handle,
        _TOKEN_QUERY,
        ctypes.byref(token),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        integrity_buffer = _token_information(token, _TOKEN_INTEGRITY_LEVEL)
        label = ctypes.cast(
            integrity_buffer,
            ctypes.POINTER(_TokenMandatoryLabel),
        ).contents
        count_pointer = _advapi32.GetSidSubAuthorityCount(label.Label.Sid)
        if not count_pointer or not count_pointer[0]:
            raise OSError("child token has no integrity RID")
        integrity_rid = int(
            _advapi32.GetSidSubAuthority(
                label.Label.Sid,
                int(count_pointer[0]) - 1,
            )[0]
        )
        privileges_buffer = _token_information(token, _TOKEN_PRIVILEGES)
        privilege_count = int(
            ctypes.cast(
                privileges_buffer,
                ctypes.POINTER(wintypes.DWORD),
            ).contents.value
        )
        return RestrictedTokenProfile(
            is_restricted=bool(_advapi32.IsTokenRestricted(token)),
            integrity_rid=integrity_rid,
            privilege_count=privilege_count,
        )
    finally:
        _kernel32.CloseHandle(token)


def _token_information(token: int, information_class: int) -> ctypes.Array:
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


def _clear_handle_inheritance(handles: tuple[int, ...]) -> None:
    for handle in handles:
        if not _kernel32.SetHandleInformation(
            handle,
            _HANDLE_FLAG_INHERIT,
            0,
        ):
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
