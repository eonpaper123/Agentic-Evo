from __future__ import annotations

import ctypes
import json
import msvcrt
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
            process.resume()

            report = json.loads(read_stream.readline())
            self.assertTrue(report["included_set"])
            self.assertFalse(report["decoy_set"])
            self.assertEqual(kernel32.WaitForSingleObject(included_event, 0), 0)
            self.assertEqual(kernel32.WaitForSingleObject(decoy, 0), 258)
            self.assertEqual(process.wait(timeout=5.0), 0)
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
