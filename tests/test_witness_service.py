from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from agentic_evo.ipc import (
    MAX_PUBLIC_FRAME_BYTES,
    ServiceRejectedError,
    ServiceUnavailableError,
    SurfaceClient,
    open_public_connection,
    receive_public_message,
    send_public_message,
    service_endpoint,
)
from agentic_evo.runtime import DevelopmentalRuntime


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


class WitnessServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "runtime"
        self.runtime = DevelopmentalRuntime.genesis(
            self.home,
            host_binding="test-host-binding",
            purpose_anchor="Improve the future of the one bound host.",
            initial_body={"entrypoint.md": "Body zero"},
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
        self.processes: list[subprocess.Popen[str]] = []

    def tearDown(self) -> None:
        for process in reversed(self.processes):
            self._terminate(process)
        self.tempdir.cleanup()

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        prior = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            str(SOURCE_ROOT)
            if not prior
            else os.pathsep.join((str(SOURCE_ROOT), prior))
        )
        return environment

    def _spawn(self, home: Path | None = None) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agentic_evo.service",
                "--dev-home",
                str(home or self.home),
            ],
            cwd=REPOSITORY_ROOT,
            env=self._environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.processes.append(process)
        return process

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _wait_until_ready(
        self,
        process: subprocess.Popen[str],
        *,
        home: Path | None = None,
    ) -> SurfaceClient:
        client = SurfaceClient(home or self.home)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                _, stderr = process.communicate(timeout=1)
                self.fail(
                    f"Witness service exited before ready "
                    f"(code={process.returncode}): {stderr}"
                )
            try:
                client.status()
                return client
            except ServiceUnavailableError:
                time.sleep(0.02)
        self.fail("Witness service did not become ready")

    def test_one_home_has_one_live_service_and_no_public_lineage_api(
        self,
    ) -> None:
        first = self._spawn()
        client = self._wait_until_ready(first)
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()

        second = self._spawn()
        self.assertEqual(second.wait(timeout=5), 4)
        _, second_stderr = second.communicate(timeout=1)
        self.assertIn("service_already_running", second_stderr)

        for forbidden in (
            "prepare_successor",
            "advance_head",
            "turn_on",
            "turn_off",
            "genesis",
        ):
            with self.subTest(operation=forbidden):
                with self.assertRaises(ServiceRejectedError) as caught:
                    client._request(forbidden, {})
                self.assertEqual(caught.exception.code, "operation_not_public")

        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)

    def test_public_lifecycle_derives_surface_provenance(self) -> None:
        process = self._spawn()
        client = self._wait_until_ready(process)

        wake = client.wake(
            execution_surface="codex",
            session_id="surface-session-1",
            project_environment="project-a",
            model="test-model",
        )
        self.assertEqual(wake["head"], self.runtime.status().head)
        receipt = client.observe(
            event_kind="tool_result",
            payload={"outcome": "tests passed"},
            execution_surface="codex",
            session_id="surface-session-1",
            project_environment="project-a",
        )
        client.sleep(session_id="surface-session-1")

        record = self.runtime.evidence.records()[receipt["sequence"] - 1]
        self.assertEqual(record.event_id, receipt["event_id"])
        self.assertEqual(record.source_kind, "execution_surface")
        self.assertEqual(record.author_kind, "surface_unverified")
        self.assertEqual(record.execution_surface, "codex")

        before = self.runtime.evidence.records()
        with self.assertRaises(ServiceRejectedError) as caught:
            client._request(
                "observe",
                {
                    "event_kind": "forged",
                    "payload": {},
                    "author_kind": "agent_self_authored",
                },
            )
        self.assertEqual(caught.exception.code, "invalid_parameters")
        self.assertEqual(self.runtime.evidence.records(), before)

    def test_service_never_performs_genesis_for_an_empty_home(self) -> None:
        empty_home = Path(self.tempdir.name) / "empty"
        process = self._spawn(empty_home)

        self.assertEqual(process.wait(timeout=5), 3)
        _, stderr = process.communicate(timeout=1)
        self.assertIn("not_initialized", stderr)
        self.assertFalse((empty_home / "trusted" / "state.sqlite3").exists())
        self.assertFalse((empty_home / "body").exists())

    def test_endpoint_is_deterministic_platform_specific_and_bounded(
        self,
    ) -> None:
        windows = service_endpoint(self.home, platform="win32")
        windows_again = service_endpoint(self.home, platform="win32")
        other_windows = service_endpoint(
            self.home.with_name("other-runtime"),
            platform="win32",
        )
        macos = service_endpoint(self.home, platform="darwin")
        linux = service_endpoint(self.home, platform="linux")

        self.assertEqual(windows, windows_again)
        self.assertNotEqual(windows, other_windows)
        self.assertEqual(windows.family, "AF_PIPE")
        self.assertTrue(windows.address.startswith("\\\\.\\pipe\\agentic-evo-dev-"))
        self.assertEqual(macos.family, "AF_UNIX")
        self.assertEqual(linux.family, "AF_UNIX")
        self.assertLess(len(os.fsencode(macos.address)), 104)
        self.assertLess(len(os.fsencode(linux.address)), 108)

    def test_malformed_and_oversize_frames_fail_closed(self) -> None:
        process = self._spawn()
        client = self._wait_until_ready(process)
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        endpoint = service_endpoint(self.home)

        with open_public_connection(endpoint) as connection:
            connection.send_bytes(b"{not-json")
            response = receive_public_message(connection)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "invalid_frame")

        with open_public_connection(endpoint) as connection:
            connection.send_bytes(b"x" * (MAX_PUBLIC_FRAME_BYTES + 1))

        self.assertEqual(client.status()["head"], before_status.head)
        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)

        with open_public_connection(endpoint) as connection:
            send_public_message(
                connection,
                {
                    "protocol": "agentic-evo-public-v1",
                    "request_id": "still-alive",
                    "operation": "status",
                    "params": {},
                },
            )
            response = receive_public_message(connection)
        self.assertTrue(response["ok"])


if __name__ == "__main__":
    unittest.main()
