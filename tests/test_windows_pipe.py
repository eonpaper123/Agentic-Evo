from __future__ import annotations

import json
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
import threading
import time
import unittest
from uuid import uuid4

from agentic_evo.ipc import receive_public_message, send_public_message


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


@unittest.skipUnless(sys.platform == "win32", "Windows native contract")
class WindowsPublicPipeTests(unittest.TestCase):
    def test_explicit_dacl_authenticates_an_independent_client(self) -> None:
        import _winapi

        from agentic_evo.windows_pipe import (
            WINDOWS_PUBLIC_PIPE_CLIENT_ACCESS,
            WindowsPublicPipeListener,
            current_process_sid,
        )

        address = rf"\\.\pipe\agentic-evo-test-{uuid4().hex}"
        expected_sid = current_process_sid()
        listener = WindowsPublicPipeListener(address, expected_sid=expected_sid)
        process: subprocess.Popen[str] | None = None
        accepted = None
        try:
            self.assertEqual(
                WINDOWS_PUBLIC_PIPE_CLIENT_ACCESS & 0x00000004,
                0,
            )
            with self.assertRaises(OSError) as denied:
                _winapi.CreateFile(
                    address,
                    _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                    0,
                    _winapi.NULL,
                    _winapi.OPEN_EXISTING,
                    _winapi.FILE_FLAG_OVERLAPPED,
                    _winapi.NULL,
                )
            self.assertEqual(denied.exception.winerror, 5)

            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-P",
                    "-c",
                    (
                        "import json,sys;"
                        "from agentic_evo.ipc import "
                        "receive_public_message,send_public_message;"
                        "from agentic_evo.windows_pipe import "
                        "connect_windows_public_pipe;"
                        "c=connect_windows_public_pipe(sys.argv[1]);"
                        "send_public_message(c,{'kind':'ping'});"
                        "print(json.dumps(receive_public_message(c)));"
                        "c.close()"
                    ),
                    address,
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            accepted = listener.accept()
            self.assertEqual(accepted.client_pid, process.pid)
            self.assertEqual(accepted.client_sid, expected_sid)
            self.assertEqual(
                receive_public_message(accepted.connection),
                {"kind": "ping"},
            )
            send_public_message(accepted.connection, {"ok": True})

            stdout, stderr = process.communicate(timeout=5.0)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(json.loads(stdout), {"ok": True})
        finally:
            if accepted is not None:
                accepted.connection.close()
            listener.close()
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5.0)
            if process is not None and process.stdout is not None:
                process.stdout.close()
            if process is not None and process.stderr is not None:
                process.stderr.close()

    def test_listener_discards_a_client_that_disconnects_before_authentication(
        self,
    ) -> None:
        from agentic_evo.windows_pipe import (
            WindowsPublicPipeListener,
            connect_windows_public_pipe,
            current_process_sid,
        )

        address = rf"\\.\pipe\agentic-evo-test-{uuid4().hex}"
        listener = WindowsPublicPipeListener(
            address,
            expected_sid=current_process_sid(),
        )
        stale = connect_windows_public_pipe(address)
        stale.close()
        result: Queue[object] = Queue()

        def valid_client() -> None:
            try:
                time.sleep(0.1)
                connection = connect_windows_public_pipe(address)
                try:
                    send_public_message(connection, {"kind": "ping"})
                    result.put(receive_public_message(connection))
                finally:
                    connection.close()
            except Exception as exc:
                result.put(exc)

        thread = threading.Thread(target=valid_client, daemon=True)
        thread.start()
        accepted = None
        try:
            accepted = listener.accept()
            self.assertEqual(
                receive_public_message(accepted.connection),
                {"kind": "ping"},
            )
            send_public_message(accepted.connection, {"ok": True})
            thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result.get_nowait(), {"ok": True})
        finally:
            if accepted is not None:
                accepted.connection.close()
            listener.close()


if __name__ == "__main__":
    unittest.main()
