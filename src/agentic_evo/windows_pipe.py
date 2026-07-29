from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from multiprocessing import connection as mp_connection
import time

import _winapi


WINDOWS_PUBLIC_PIPE_CLIENT_ACCESS = 0x00100183

_AUTHENTICATION_PREFACE = b"\x00"
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_INVALID_PARAMETER = 87
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_PIPE_ACCESS_DUPLEX = 0x00000003
_PIPE_READMODE_MESSAGE = 0x00000002
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_PIPE_TYPE_MESSAGE = 0x00000004
_PIPE_UNLIMITED_INSTANCES = 255
_SDDL_REVISION_1 = 1
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

_kernel32.CreateNamedPipeW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(_SecurityAttributes),
]
_kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetCurrentThread.restype = wintypes.HANDLE
_kernel32.GetNamedPipeClientProcessId.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.ULONG),
]
_kernel32.GetNamedPipeClientProcessId.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_advapi32.ConvertSidToStringSidW.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.LPWSTR),
]
_advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
_advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wintypes.ULONG),
]
_advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
    wintypes.BOOL
)
_advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
_advapi32.GetTokenInformation.restype = wintypes.BOOL
_advapi32.ImpersonateNamedPipeClient.argtypes = [wintypes.HANDLE]
_advapi32.ImpersonateNamedPipeClient.restype = wintypes.BOOL
_advapi32.OpenProcessToken.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
]
_advapi32.OpenProcessToken.restype = wintypes.BOOL
_advapi32.OpenThreadToken.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.BOOL,
    ctypes.POINTER(wintypes.HANDLE),
]
_advapi32.OpenThreadToken.restype = wintypes.BOOL
_advapi32.RevertToSelf.restype = wintypes.BOOL


@dataclass(frozen=True)
class AcceptedWindowsPipe:
    connection: mp_connection.Connection
    client_pid: int
    client_sid: str


class WindowsPublicPipeListener:
    def __init__(self, address: str, *, expected_sid: str) -> None:
        self.address = address
        self.expected_sid = expected_sid
        self._security_descriptor = _security_descriptor(expected_sid)
        self._security_attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes),
            self._security_descriptor,
            False,
        )
        self._pending = self._new_handle()
        self._closed = False

    def accept(self) -> AcceptedWindowsPipe:
        if self._closed:
            raise OSError("Windows public pipe listener is closed")
        if self._pending is None:
            self._pending = self._new_handle()
        handle = self._pending
        self._pending = None
        try:
            _connect_named_pipe(handle)
            _read_authentication_preface(handle)
            client_pid = _client_pid(handle)
            client_sid = _impersonated_client_sid(handle)
            if client_sid != self.expected_sid:
                raise PermissionError("Windows public pipe peer SID is not bound")
            connection = mp_connection.PipeConnection(handle)
        except Exception:
            _winapi.CloseHandle(handle)
            raise
        return AcceptedWindowsPipe(connection, client_pid, client_sid)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pending is not None:
            _winapi.CloseHandle(self._pending)
            self._pending = None
        _kernel32.LocalFree(self._security_descriptor)
        self._security_descriptor = None

    def _new_handle(self) -> int:
        open_mode = (
            _PIPE_ACCESS_DUPLEX
            | _winapi.FILE_FLAG_OVERLAPPED
            | _FILE_FLAG_FIRST_PIPE_INSTANCE
        )
        pipe_mode = (
            _PIPE_TYPE_MESSAGE
            | _PIPE_READMODE_MESSAGE
            | _PIPE_REJECT_REMOTE_CLIENTS
        )
        handle = _kernel32.CreateNamedPipeW(
            self.address,
            open_mode,
            pipe_mode,
            _PIPE_UNLIMITED_INSTANCES,
            64 * 1024,
            64 * 1024,
            0,
            ctypes.byref(self._security_attributes),
        )
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle


def connect_windows_public_pipe(
    address: str,
    *,
    timeout_seconds: float = 5.0,
) -> mp_connection.Connection:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            _winapi.WaitNamedPipe(address, 200)
            handle = _winapi.CreateFile(
                address,
                WINDOWS_PUBLIC_PIPE_CLIENT_ACCESS,
                0,
                _winapi.NULL,
                _winapi.OPEN_EXISTING,
                _winapi.FILE_FLAG_OVERLAPPED,
                _winapi.NULL,
            )
        except OSError as exc:
            if (
                exc.winerror
                not in (_winapi.ERROR_PIPE_BUSY, _winapi.ERROR_SEM_TIMEOUT)
                or time.monotonic() >= deadline
            ):
                raise
            continue
        try:
            _winapi.SetNamedPipeHandleState(
                handle,
                _winapi.PIPE_READMODE_MESSAGE,
                None,
                None,
            )
            _write_authentication_preface(handle)
            return mp_connection.PipeConnection(handle)
        except Exception:
            _winapi.CloseHandle(handle)
            raise


def current_process_sid() -> str:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(),
        _TOKEN_QUERY,
        ctypes.byref(token),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _token_sid(token)
    finally:
        _kernel32.CloseHandle(token)


def _client_pid(pipe_handle: int) -> int:
    pid = wintypes.ULONG()
    if not _kernel32.GetNamedPipeClientProcessId(pipe_handle, ctypes.byref(pid)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(pid.value)


def _connect_named_pipe(handle: int) -> None:
    try:
        overlapped = _winapi.ConnectNamedPipe(handle, overlapped=True)
    except OSError as exc:
        if exc.winerror not in (
            _winapi.ERROR_NO_DATA,
            getattr(_winapi, "ERROR_PIPE_CONNECTED", 535),
        ):
            raise
        return
    _, error = overlapped.GetOverlappedResult(True)
    if error:
        raise ctypes.WinError(error)


def _read_authentication_preface(handle: int) -> None:
    overlapped, _ = _winapi.ReadFile(
        handle,
        len(_AUTHENTICATION_PREFACE),
        overlapped=True,
    )
    read, error = overlapped.GetOverlappedResult(True)
    if error:
        raise ctypes.WinError(error)
    if read != len(_AUTHENTICATION_PREFACE):
        raise PermissionError("Windows public pipe authentication preface is incomplete")
    if bytes(overlapped.getbuffer()) != _AUTHENTICATION_PREFACE:
        raise PermissionError("Windows public pipe authentication preface is invalid")


def _write_authentication_preface(handle: int) -> None:
    overlapped, _ = _winapi.WriteFile(
        handle,
        _AUTHENTICATION_PREFACE,
        overlapped=True,
    )
    written, error = overlapped.GetOverlappedResult(True)
    if error:
        raise ctypes.WinError(error)
    if written != len(_AUTHENTICATION_PREFACE):
        raise OSError("cannot write Windows public pipe authentication preface")


def _impersonated_client_sid(pipe_handle: int) -> str:
    if not _advapi32.ImpersonateNamedPipeClient(pipe_handle):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        token = wintypes.HANDLE()
        if not _advapi32.OpenThreadToken(
            _kernel32.GetCurrentThread(),
            _TOKEN_QUERY,
            True,
            ctypes.byref(token),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return _token_sid(token)
        finally:
            _kernel32.CloseHandle(token)
    finally:
        if not _advapi32.RevertToSelf():
            raise ctypes.WinError(ctypes.get_last_error())


def _security_descriptor(expected_sid: str) -> int:
    descriptor = ctypes.c_void_p()
    sddl = (
        "D:P"
        f"(A;;0x{WINDOWS_PUBLIC_PIPE_CLIENT_ACCESS:08x};;;SY)"
        f"(A;;0x{WINDOWS_PUBLIC_PIPE_CLIENT_ACCESS:08x};;;{expected_sid})"
    )
    if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        _SDDL_REVISION_1,
        ctypes.byref(descriptor),
        None,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return descriptor.value


def _token_sid(token: int) -> str:
    size = wintypes.DWORD()
    _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
    error = ctypes.get_last_error()
    if error != _ERROR_INSUFFICIENT_BUFFER:
        raise ctypes.WinError(error)
    buffer = ctypes.create_string_buffer(size.value)
    if not _advapi32.GetTokenInformation(
        token,
        _TOKEN_USER,
        buffer,
        size.value,
        ctypes.byref(size),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    sid = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents.User.Sid
    text = wintypes.LPWSTR()
    if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value
    finally:
        _kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
