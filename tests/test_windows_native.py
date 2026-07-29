from __future__ import annotations

import ctypes
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
