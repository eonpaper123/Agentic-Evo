from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agentic_evo._util import canonical_json_bytes
from agentic_evo.ipc import (
    CONTROL_PROTOCOL,
    MAX_PUBLIC_FRAME_BYTES,
    OffRehearsalClient,
    ServiceRejectedError,
    ServiceUnavailableError,
    SurfaceClient,
    control_endpoint,
    open_public_connection,
    receive_public_message,
    send_public_message,
    service_endpoint,
)
from agentic_evo.runtime import DevelopmentalRuntime, RuntimeStatus
from agentic_evo.service import WitnessService


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


class WitnessServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "runtime"
        self.host_binding = "test-host-binding"
        self.runtime = DevelopmentalRuntime.genesis(
            self.home,
            host_binding=self.host_binding,
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
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    def _wait_until_ready(
        self,
        process: subprocess.Popen[str],
        *,
        home: Path | None = None,
        timeout_seconds: float = 5.0,
    ) -> SurfaceClient:
        client = SurfaceClient(home or self.home)
        deadline = time.monotonic() + timeout_seconds
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
        service_status = client.status()
        self.assertEqual(
            service_status["body_rehearsal"]["state"],
            "ready",
        )
        self.assertEqual(
            service_status["body_rehearsal"]["head"],
            self.runtime.status().head,
        )
        self.assertEqual(
            service_status["body_rehearsal"]["provenance"],
            "subprocess_rehearsal",
        )
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
        session_records = [
            item
            for item in self.runtime.evidence.records()
            if item.event_kind in {"session_start", "session_end"}
        ]
        self.assertEqual(
            [item.author_kind for item in session_records],
            ["surface_unverified", "surface_unverified"],
        )

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

    def test_many_file_head_has_a_bounded_public_wake_projection(self) -> None:
        many_home = Path(self.tempdir.name) / "many-file-runtime"
        files = {
            "entrypoint.md": "Body zero",
            **{
                f"skills/{index:04d}-{'x' * 32}.md": "shared"
                for index in range(1200)
            },
        }
        DevelopmentalRuntime.genesis(
            many_home,
            host_binding=self.host_binding,
            purpose_anchor="Improve the future of the one bound host.",
            initial_body=files,
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
        process = self._spawn(many_home)
        client = self._wait_until_ready(
            process,
            home=many_home,
            timeout_seconds=15.0,
        )

        wake = client.wake(
            execution_surface="codex",
            session_id="many-file-session",
            project_environment="project-a",
        )

        self.assertEqual(wake["body_file_count"], len(files))
        self.assertTrue(wake["body_files_truncated"])
        self.assertEqual(wake["body_files"], sorted(files)[:16])

    def test_public_status_bounds_active_session_projection(self) -> None:
        service = WitnessService(self.home)
        self.addCleanup(service.witness.close)
        sessions = tuple(f"session-{index:04d}-" + "\0" * 1000 for index in range(100))
        status = RuntimeStatus(
            root="r" * 64,
            head="h" * 64,
            generation=0,
            authority="on",
            lifecycle_state="awake",
            active_sessions=sessions,
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
        request = {
            "protocol": "agentic-evo-public-v1",
            "request_id": "bounded-status",
            "operation": "status",
            "params": {},
        }

        with (
            patch.object(service, "_ensure_body"),
            patch.object(service.runtime, "status", return_value=status),
        ):
            result = service.dispatch_public(request)

        self.assertEqual(result["active_session_count"], len(sessions))
        self.assertTrue(result["active_sessions_truncated"])
        self.assertLessEqual(len(result["active_sessions"]), 32)
        response = {
            "protocol": "agentic-evo-public-v1",
            "request_id": "bounded-status",
            "ok": True,
            "result": result,
        }
        self.assertLessEqual(
            len(canonical_json_bytes(response)),
            MAX_PUBLIC_FRAME_BYTES,
        )

    def test_escape_heavy_wake_projection_stays_inside_one_frame(self) -> None:
        escaped_home = Path(self.tempdir.name) / "escaped-runtime"
        quote_run = '"' * 500
        files = {
            "entrypoint.md": "\0" * 8000,
            **{
                f"{index:02d}-{quote_run}.md": "shared"
                for index in range(20)
            },
        }
        DevelopmentalRuntime.genesis(
            escaped_home,
            host_binding=self.host_binding,
            purpose_anchor="Improve the future of the one bound host.",
            initial_body=files,
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
        process = self._spawn(escaped_home)
        client = self._wait_until_ready(process, home=escaped_home)

        wake = client.wake(
            execution_surface="codex",
            session_id="escaped-session",
            project_environment="project-a",
        )

        self.assertEqual(wake["body_file_count"], len(files))
        self.assertTrue(wake["body_files_truncated"])
        self.assertTrue(wake["activation_context_truncated"])

    def test_public_string_parameters_have_an_explicit_byte_bound(self) -> None:
        process = self._spawn()
        client = self._wait_until_ready(process)

        with self.assertRaises(ServiceRejectedError) as caught:
            client.wake(
                execution_surface="codex",
                session_id="s" * 1025,
                project_environment="project-a",
            )

        self.assertEqual(caught.exception.code, "invalid_parameters")

    def test_off_client_does_not_hang_on_a_partial_response_frame(self) -> None:
        class PartialResponseConnection:
            def __init__(self) -> None:
                self.closed = threading.Event()

            def send_bytes(self, raw: bytes) -> None:
                pass

            def poll(self, timeout: float) -> bool:
                return True

            def recv_bytes(self, maximum: int) -> bytes:
                self.closed.wait()
                raise EOFError

            def close(self) -> None:
                self.closed.set()

        connection = PartialResponseConnection()

        @contextmanager
        def open_partial_response(endpoint):
            try:
                yield connection
            finally:
                connection.close()

        failures: list[Exception] = []

        def request_off() -> None:
            try:
                OffRehearsalClient(self.home).off()
            except Exception as exc:
                failures.append(exc)

        with (
            patch(
                "agentic_evo.ipc.open_public_connection",
                open_partial_response,
            ),
            patch(
                "agentic_evo.ipc.CONTROL_RESPONSE_TIMEOUT_SECONDS",
                0.05,
            ),
        ):
            worker = threading.Thread(target=request_off, daemon=True)
            worker.start()
            worker.join(timeout=0.5)
            completed_within_deadline = not worker.is_alive()
            connection.close()
            worker.join(timeout=1)

        self.assertTrue(completed_within_deadline)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ServiceUnavailableError)

    def test_service_never_performs_genesis_for_an_empty_home(self) -> None:
        empty_home = Path(self.tempdir.name) / "empty"
        process = self._spawn(empty_home)

        self.assertEqual(process.wait(timeout=5), 3)
        _, stderr = process.communicate(timeout=1)
        self.assertIn("not_initialized", stderr)
        self.assertFalse((empty_home / "trusted" / "state.sqlite3").exists())
        self.assertFalse((empty_home / "body").exists())

    def test_process_crash_releases_singleton_and_preserves_committed_state(
        self,
    ) -> None:
        first = self._spawn()
        client = self._wait_until_ready(first)
        client.observe(
            event_kind="committed_before_service_crash",
            payload={"result": "durable"},
            execution_surface="codex",
        )
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()

        first.kill()
        first.wait(timeout=5)
        with self.assertRaises(ServiceUnavailableError):
            client.status()

        replacement = self._spawn()
        replacement_client = self._wait_until_ready(replacement)
        self.assertEqual(replacement_client.status()["head"], before_status.head)
        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)

    def test_external_off_on_cycle_replaces_the_subprocess_binding(self) -> None:
        process = self._spawn()
        client = self._wait_until_ready(process)
        old_body = client.status()["body_rehearsal"]

        self.runtime.turn_off()
        self.runtime.turn_on(host_binding=self.host_binding)
        new_body = client.status()["body_rehearsal"]

        self.assertEqual(new_body["state"], "ready")
        self.assertEqual(new_body["head"], old_body["head"])
        self.assertNotEqual(new_body["pid"], old_body["pid"])

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
        windows_control = control_endpoint(self.home, platform="win32")
        macos_control = control_endpoint(self.home, platform="darwin")
        linux_control = control_endpoint(self.home, platform="linux")

        self.assertEqual(windows, windows_again)
        self.assertNotEqual(windows, other_windows)
        self.assertNotEqual(windows, windows_control)
        self.assertNotEqual(macos, macos_control)
        self.assertNotEqual(linux, linux_control)
        self.assertEqual(windows.family, "AF_PIPE")
        self.assertTrue(windows.address.startswith("\\\\.\\pipe\\agentic-evo-dev-"))
        self.assertEqual(windows_control.family, "AF_PIPE")
        self.assertEqual(macos.family, "AF_UNIX")
        self.assertEqual(linux.family, "AF_UNIX")
        self.assertLess(len(os.fsencode(macos.address)), 104)
        self.assertLess(len(os.fsencode(linux.address)), 108)
        self.assertLess(len(os.fsencode(macos_control.address)), 104)
        self.assertLess(len(os.fsencode(linux_control.address)), 108)

    def test_off_control_is_separate_bounded_idempotent_and_unverified(
        self,
    ) -> None:
        process = self._spawn()
        public = self._wait_until_ready(process)
        public.wake(
            execution_surface="codex",
            session_id="session-before-control-off",
            project_environment="project-a",
        )
        endpoint = control_endpoint(self.home)
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()

        invalid_requests = (
            {
                "protocol": "wrong-protocol",
                "request_id": "wrong-protocol",
                "operation": "off",
                "params": {},
            },
            {
                "protocol": CONTROL_PROTOCOL,
                "request_id": "turn-on",
                "operation": "on",
                "params": {},
            },
            {
                "protocol": CONTROL_PROTOCOL,
                "request_id": "forged-provenance",
                "operation": "off",
                "params": {"author_kind": "normal_host_interaction"},
            },
        )
        for request in invalid_requests:
            with self.subTest(request_id=request["request_id"]):
                with open_public_connection(endpoint) as connection:
                    send_public_message(connection, request)
                    response = receive_public_message(connection)
                self.assertFalse(response["ok"])
        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)

        with open_public_connection(endpoint) as connection:
            connection.send_bytes(b"{not-json")
            malformed = receive_public_message(connection)
        self.assertFalse(malformed["ok"])
        self.assertEqual(self.runtime.status(), before_status)

        with open_public_connection(endpoint) as connection:
            connection.send_bytes(b"x" * (MAX_PUBLIC_FRAME_BYTES + 1))
        self.assertEqual(self.runtime.status(), before_status)

        control = OffRehearsalClient(self.home)
        result = control.off()
        after_first = self.runtime.evidence.records()
        self.assertEqual(result["authority"], "off")
        self.assertEqual(result["body_rehearsal"]["state"], "absent")
        self.assertEqual(result["root"], before_status.root)
        self.assertEqual(result["head"], before_status.head)
        self.assertEqual(result["active_sessions"], [])
        self.assertEqual(len(after_first), len(before_records) + 1)
        record = after_first[-1]
        self.assertEqual(record.event_kind, "control_rehearsal_off")
        self.assertEqual(record.source_kind, "host_control_rehearsal")
        self.assertEqual(record.author_kind, "control_unverified")

        again = control.off()
        self.assertEqual(again["authority"], "off")
        self.assertEqual(again["body_rehearsal"]["state"], "absent")
        self.assertEqual(self.runtime.evidence.records(), after_first)

    def test_off_control_does_not_skip_the_atomic_off_when_a_writer_races(
        self,
    ) -> None:
        class RacingRuntime:
            def __init__(self) -> None:
                self.authority = "off"
                self.rehearsed = False

            def _snapshot(self) -> RuntimeStatus:
                return RuntimeStatus(
                    root="root",
                    head="head",
                    generation=0,
                    authority=self.authority,
                    lifecycle_state="off" if self.authority == "off" else "waiting",
                    active_sessions=(),
                    instrument_version="instrument-test-v1",
                    protocol_version="protocol-test-v1",
                )

            def status(self) -> RuntimeStatus:
                snapshot = self._snapshot()
                if not self.rehearsed:
                    self.authority = "on"
                return snapshot

            def rehearse_turn_off(self) -> RuntimeStatus:
                self.rehearsed = True
                self.authority = "off"
                return self._snapshot()

        runtime = RacingRuntime()
        service = WitnessService.__new__(WitnessService)
        service.runtime = runtime
        service._body_guard = threading.RLock()
        service._body = None

        result = service._turn_off_rehearsal()

        self.assertTrue(runtime.rehearsed)
        self.assertEqual(result["authority"], runtime.authority)
        self.assertEqual(result["authority"], "off")

    def test_control_receive_deadline_does_not_abort_slow_body_shutdown(
        self,
    ) -> None:
        class FakeConnection:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        connection = FakeConnection()
        observed_during_shutdown: list[bool] = []
        service = WitnessService.__new__(WitnessService)

        def slow_control_work(_: object) -> None:
            time.sleep(0.05)
            observed_during_shutdown.append(connection.closed)

        service._serve_control_connection = slow_control_work
        with patch("agentic_evo.service.PUBLIC_IO_TIMEOUT_SECONDS", 0.01):
            service._serve_control_connection_with_deadline(connection)

        self.assertEqual(observed_during_shutdown, [False])
        self.assertTrue(connection.closed)

    def test_control_partial_frame_cannot_block_the_off_listener_forever(
        self,
    ) -> None:
        class PrefixOnlyConnection:
            def __init__(self) -> None:
                self.closed = threading.Event()
                self.sent: list[bytes] = []

            def poll(self, _: float) -> bool:
                return True

            def recv_bytes(self, _: int) -> bytes:
                self.closed.wait(timeout=0.25)
                if not self.closed.is_set():
                    raise TimeoutError("partial frame remained blocked")
                raise OSError("connection closed by receive deadline")

            def send_bytes(self, value: bytes) -> None:
                self.sent.append(value)

            def close(self) -> None:
                self.closed.set()

        connection = PrefixOnlyConnection()
        service = WitnessService.__new__(WitnessService)
        started = time.monotonic()

        with patch("agentic_evo.service.PUBLIC_IO_TIMEOUT_SECONDS", 0.01):
            service._serve_control_connection(connection)

        self.assertLess(time.monotonic() - started, 0.2)
        self.assertTrue(connection.closed.is_set())
        response = json.loads(connection.sent[-1].decode("utf-8"))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "invalid_frame")

    def test_off_client_waits_for_the_bounded_shutdown_response_window(
        self,
    ) -> None:
        class DelayedControlResponse:
            def __init__(self) -> None:
                self.request: dict[str, object] | None = None
                self.poll_timeout: float | None = None

            def send_bytes(self, raw: bytes) -> None:
                self.request = json.loads(raw.decode("utf-8"))

            def poll(self, timeout: float) -> bool:
                self.poll_timeout = timeout
                return timeout >= 0.05

            def recv_bytes(self, _: int) -> bytes:
                assert self.request is not None
                return json.dumps(
                    {
                        "protocol": CONTROL_PROTOCOL,
                        "request_id": self.request["request_id"],
                        "ok": True,
                        "result": {
                            "authority": "off",
                            "body_rehearsal": {"state": "absent"},
                        },
                    }
                ).encode("utf-8")

            def close(self) -> None:
                pass

        connection = DelayedControlResponse()

        @contextmanager
        def open_fake_connection(_: object) -> object:
            yield connection

        with (
            patch(
                "agentic_evo.ipc.open_public_connection",
                open_fake_connection,
            ),
            patch("agentic_evo.ipc.PUBLIC_IO_TIMEOUT_SECONDS", 0.01),
            patch(
                "agentic_evo.ipc.CONTROL_RESPONSE_TIMEOUT_SECONDS",
                0.1,
                create=True,
            ),
        ):
            result = OffRehearsalClient(self.home).off()

        self.assertEqual(result["authority"], "off")
        self.assertEqual(connection.poll_timeout, 0.1)

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

    def test_silent_connection_cannot_block_other_surface_requests(self) -> None:
        process = self._spawn()
        client = self._wait_until_ready(process)
        endpoint = service_endpoint(self.home)
        context = open_public_connection(endpoint)
        silent_connection = context.__enter__()
        result: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def read_status() -> None:
            try:
                result.append(client.status())
            except BaseException as exc:
                errors.append(exc)

        reader = threading.Thread(target=read_status)
        try:
            reader.start()
            reader.join(timeout=0.75)
            responsive_while_silent = not reader.is_alive()
        finally:
            context.__exit__(None, None, None)
            reader.join(timeout=5)

        self.assertTrue(
            responsive_while_silent,
            "one silent public connection blocked the whole Witness service",
        )
        self.assertFalse(errors)
        self.assertEqual(result[0]["head"], self.runtime.status().head)


if __name__ == "__main__":
    unittest.main()
