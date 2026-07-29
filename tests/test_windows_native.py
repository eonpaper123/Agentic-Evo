from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from ctypes import wintypes


@unittest.skipUnless(sys.platform == "win32", "Windows native contract")
class WindowsJobObjectTests(unittest.TestCase):
    def test_kill_on_close_job_terminates_the_assigned_process_tree(self) -> None:
        from agentic_evo.windows_native import KillOnCloseJob

        job = KillOnCloseJob()
        parent = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import subprocess,sys,time;"
                    "sys.stdin.buffer.read(1);"
                    "child=subprocess.Popen("
                    "[sys.executable,'-c','import time;time.sleep(60)']);"
                    "print(child.pid,flush=True);"
                    "time.sleep(60)"
                ),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
        )
        child_pid: int | None = None
        try:
            job.assign_handle(int(parent._handle))
            assert parent.stdin is not None
            assert parent.stdout is not None
            parent.stdin.write(b"1")
            parent.stdin.flush()
            child_pid = int(parent.stdout.readline())
            self.assertFalse(_wait_for_process_exit(parent.pid, timeout_ms=50))
            self.assertFalse(_wait_for_process_exit(child_pid, timeout_ms=50))

            job.close()

            parent.wait(timeout=5.0)
            self.assertTrue(_wait_for_process_exit(child_pid, timeout_ms=5_000))
        finally:
            job.close()
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5.0)
            if parent.stdin is not None:
                parent.stdin.close()
            if parent.stdout is not None:
                parent.stdout.close()

    def test_restricted_process_is_suspended_and_inherits_only_listed_handles(
        self,
    ) -> None:
        import _winapi
        import msvcrt

        from agentic_evo.windows_native import (
            KillOnCloseJob,
            spawn_restricted_suspended_process,
        )

        read_fd, write_fd = os.pipe()
        read_stream = os.fdopen(read_fd, "rb", buffering=0)
        child_write_handle = msvcrt.get_osfhandle(write_fd)
        os.set_handle_inheritable(child_write_handle, True)

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.SetHandleInformation.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        kernel32.SetHandleInformation.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        included_event = kernel32.CreateEventW(None, True, False, None)
        self.assertTrue(included_event)
        self.assertTrue(kernel32.SetHandleInformation(included_event, 1, 1))
        decoy = kernel32.CreateEventW(None, True, False, None)
        self.assertTrue(decoy)
        self.assertTrue(kernel32.SetHandleInformation(decoy, 1, 1))

        helper = (
            "import ctypes,json,msvcrt,os,sys;"
            "from ctypes import wintypes;"
            "k=ctypes.WinDLL('kernel32',use_last_error=True);"
            "k.SetEvent.argtypes=[wintypes.HANDLE];"
            "k.SetEvent.restype=wintypes.BOOL;"
            "included_set=bool(k.SetEvent(int(sys.argv[2])));"
            "decoy_set=bool(k.SetEvent(int(sys.argv[3])));"
            "stream=os.fdopen(msvcrt.open_osfhandle(int(sys.argv[1]),"
            "os.O_WRONLY),'wb',buffering=0);"
            "stream.write((json.dumps({'included_set':included_set,"
            "'decoy_set':decoy_set})+'\\n').encode());"
            "stream.close()"
        )
        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR")
            if key in os.environ
        }
        process = None
        job = KillOnCloseJob()
        write_closed = False
        try:
            process = spawn_restricted_suspended_process(
                (
                    sys.executable,
                    "-P",
                    "-c",
                    helper,
                    str(child_write_handle),
                    str(included_event),
                    str(decoy),
                ),
                inherited_handles=(child_write_handle, int(included_event)),
                cwd=Path(sys.executable).resolve().parent,
                environment=environment,
            )

            self.assertTrue(process.token_profile.is_restricted)
            self.assertEqual(process.token_profile.integrity_rid, 4096)
            self.assertLessEqual(process.token_profile.privilege_count, 1)
            actual_token = _query_process_token_contract(process.process_handle)
            self.assertTrue(actual_token[0])
            self.assertEqual(actual_token[1], 4096)
            self.assertEqual(
                process.token_profile.privilege_count,
                len(actual_token[2]),
            )
            self.assertLessEqual(
                actual_token[2],
                {_lookup_privilege_luid("SeChangeNotifyPrivilege")},
            )
            self.assertIsNone(process.poll())
            self.assertFalse(os.get_handle_inheritable(child_write_handle))
            self.assertFalse(os.get_handle_inheritable(int(included_event)))
            self.assertTrue(os.get_handle_inheritable(int(decoy)))
            self.assertEqual(
                _winapi.PeekNamedPipe(
                    msvcrt.get_osfhandle(read_stream.fileno())
                )[0],
                0,
            )

            job.assign_handle(process.process_handle)
            os.close(write_fd)
            write_closed = True
            self.assertEqual(process.resume(), 1)

            self.assertEqual(process.wait(timeout=5.0), 0)
            report = json.loads(read_stream.readline())
            self.assertTrue(report["included_set"])
            self.assertEqual(kernel32.WaitForSingleObject(included_event, 0), 0)
            self.assertEqual(kernel32.WaitForSingleObject(decoy, 0), 258)
        finally:
            job.close()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5.0)
                process.close()
            if not write_closed:
                os.close(write_fd)
            read_stream.close()
            kernel32.CloseHandle(included_event)
            kernel32.CloseHandle(decoy)

    def test_launcher_failure_seals_but_does_not_close_caller_handle(self) -> None:
        from agentic_evo.windows_native import (
            spawn_restricted_suspended_process,
        )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        caller_handle = kernel32.CreateEventW(None, True, False, None)
        self.assertTrue(caller_handle)
        os.set_handle_inheritable(int(caller_handle), True)
        try:
            with self.assertRaises(ValueError):
                spawn_restricted_suspended_process(
                    (sys.executable, "-P", "-c", "pass"),
                    inherited_handles=(int(caller_handle),),
                    cwd=Path(sys.executable).resolve().parent,
                    environment={"INVALID": "contains\0nul"},
                )

            self.assertFalse(os.get_handle_inheritable(int(caller_handle)))
            self.assertTrue(kernel32.SetEvent(caller_handle))
            self.assertEqual(kernel32.WaitForSingleObject(caller_handle, 0), 0)
        finally:
            kernel32.CloseHandle(caller_handle)

    def test_close_reaps_a_restricted_process_that_was_never_resumed(self) -> None:
        from agentic_evo.windows_native import (
            spawn_restricted_suspended_process,
        )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        caller_handle = kernel32.CreateEventW(None, True, False, None)
        self.assertTrue(caller_handle)
        os.set_handle_inheritable(int(caller_handle), True)
        process = None
        try:
            process = spawn_restricted_suspended_process(
                (sys.executable, "-P", "-c", "import time;time.sleep(60)"),
                inherited_handles=(int(caller_handle),),
                cwd=Path(sys.executable).resolve().parent,
                environment={
                    key: os.environ[key]
                    for key in ("SystemRoot", "WINDIR")
                    if key in os.environ
                },
            )
            pid = process.pid
            process.close()
            process.close()
            self.assertTrue(_wait_for_process_exit(pid, timeout_ms=5_000))
        finally:
            if process is not None:
                process.close()
            kernel32.CloseHandle(caller_handle)

    def test_restricted_child_receives_only_the_explicit_environment(self) -> None:
        import msvcrt

        from agentic_evo.windows_native import (
            KillOnCloseJob,
            spawn_restricted_suspended_process,
        )

        read_fd, write_fd = os.pipe()
        read_stream = os.fdopen(read_fd, "rb", buffering=0)
        child_write_handle = msvcrt.get_osfhandle(write_fd)
        os.set_handle_inheritable(child_write_handle, True)
        marker_name = "AGENTIC_EVO_EXPLICIT_CHILD_MARKER"
        omitted_name = "AGENTIC_EVO_PARENT_ONLY_MARKER"
        previous_omitted = os.environ.get(omitted_name)
        os.environ[omitted_name] = "must-not-cross"
        helper = (
            "import json,msvcrt,os,sys;"
            "stream=os.fdopen(msvcrt.open_osfhandle(int(sys.argv[1]),"
            "os.O_WRONLY),'wb',buffering=0);"
            f"report={{'marker':os.getenv('{marker_name}'),"
            f"'omitted':os.getenv('{omitted_name}')}};"
            "stream.write((json.dumps(report)+'\\n').encode());"
            "stream.close()"
        )
        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR")
            if key in os.environ
        }
        environment[marker_name] = "explicit"
        process = None
        job = KillOnCloseJob()
        write_closed = False
        try:
            process = spawn_restricted_suspended_process(
                (
                    sys.executable,
                    "-P",
                    "-c",
                    helper,
                    str(child_write_handle),
                ),
                inherited_handles=(child_write_handle,),
                cwd=Path(sys.executable).resolve().parent,
                environment=environment,
            )
            job.assign_handle(process.process_handle)
            os.close(write_fd)
            write_closed = True
            self.assertEqual(process.resume(), 1)
            self.assertEqual(process.wait(timeout=5.0), 0)
            report = json.loads(read_stream.readline())
            self.assertEqual(report["marker"], "explicit")
            self.assertIsNone(report["omitted"])
        finally:
            job.close()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5.0)
                process.close()
            if not write_closed:
                os.close(write_fd)
            read_stream.close()
            if previous_omitted is None:
                os.environ.pop(omitted_name, None)
            else:
                os.environ[omitted_name] = previous_omitted


def _wait_for_process_exit(pid: int, *, timeout_ms: int) -> bool:
    synchronize = 0x00100000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
            return True
        raise ctypes.WinError(error)
    try:
        return kernel32.WaitForSingleObject(handle, timeout_ms) == 0
    finally:
        kernel32.CloseHandle(handle)


def _query_process_token_contract(
    process_handle: int,
) -> tuple[bool, int, set[tuple[int, int]]]:
    class _Luid(ctypes.Structure):
        _fields_ = [
            ("LowPart", wintypes.DWORD),
            ("HighPart", wintypes.LONG),
        ]

    class _LuidAndAttributes(ctypes.Structure):
        _fields_ = [
            ("Luid", _Luid),
            ("Attributes", wintypes.DWORD),
        ]

    class _TokenPrivileges(ctypes.Structure):
        _fields_ = [
            ("PrivilegeCount", wintypes.DWORD),
            ("Privileges", _LuidAndAttributes * 1),
        ]

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [
            ("Sid", ctypes.c_void_p),
            ("Attributes", wintypes.DWORD),
        ]

    class _TokenMandatoryLabel(ctypes.Structure):
        _fields_ = [("Label", _SidAndAttributes)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.IsTokenRestricted.argtypes = [wintypes.HANDLE]
    advapi32.IsTokenRestricted.restype = wintypes.BOOL
    advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(wintypes.BYTE)
    advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    def token_information(token: int, information_class: int) -> ctypes.Array:
        required = wintypes.DWORD()
        if advapi32.GetTokenInformation(
            token,
            information_class,
            None,
            0,
            ctypes.byref(required),
        ):
            raise AssertionError("token information unexpectedly required no buffer")
        if ctypes.get_last_error() != 122:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token,
            information_class,
            buffer,
            required,
            ctypes.byref(required),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process_handle, 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        integrity_buffer = token_information(token, 25)
        label = ctypes.cast(
            integrity_buffer,
            ctypes.POINTER(_TokenMandatoryLabel),
        ).contents
        count_pointer = advapi32.GetSidSubAuthorityCount(label.Label.Sid)
        if not count_pointer or not count_pointer[0]:
            raise AssertionError("child token has no integrity RID")
        integrity_rid = int(
            advapi32.GetSidSubAuthority(
                label.Label.Sid,
                int(count_pointer[0]) - 1,
            )[0]
        )

        privileges_buffer = token_information(token, 3)
        privileges = ctypes.cast(
            privileges_buffer,
            ctypes.POINTER(_TokenPrivileges),
        ).contents
        privilege_array = ctypes.cast(
            ctypes.addressof(privileges_buffer) + _TokenPrivileges.Privileges.offset,
            ctypes.POINTER(_LuidAndAttributes * privileges.PrivilegeCount),
        ).contents
        privilege_luids = {
            (int(item.Luid.LowPart), int(item.Luid.HighPart))
            for item in privilege_array
        }
        return (
            bool(advapi32.IsTokenRestricted(token)),
            integrity_rid,
            privilege_luids,
        )
    finally:
        kernel32.CloseHandle(token)


def _lookup_privilege_luid(name: str) -> tuple[int, int]:
    class _Luid(ctypes.Structure):
        _fields_ = [
            ("LowPart", wintypes.DWORD),
            ("HighPart", wintypes.LONG),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.LookupPrivilegeValueW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.POINTER(_Luid),
    ]
    advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
    luid = _Luid()
    if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(luid.LowPart), int(luid.HighPart)
