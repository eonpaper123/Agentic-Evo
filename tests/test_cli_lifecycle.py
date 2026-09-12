from __future__ import annotations

import json
from multiprocessing.connection import Client
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from agentic_evo._util import canonical_json_bytes
from agentic_evo.cli import MAX_SURFACE_STDIO_TEXT_BYTES
from agentic_evo.ipc import (
    MAX_PUBLIC_FRAME_BYTES,
    PUBLIC_IO_TIMEOUT_SECONDS,
    ServiceUnavailableError,
    SurfaceClient,
    build_public_request,
    service_endpoint,
)
from agentic_evo.runtime import DevelopmentalRuntime


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
EXPERIMENT_HYPOTHESIS_REFS = tuple(f"H001-{letter}" for letter in "ABCDEF")
EXPERIMENT_CONTROL_REFS = tuple(f"C{number}" for number in range(1, 9))


class CLILifecycleTests(unittest.TestCase):
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

    def _spawn_service(self) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.cli",
                "serve",
                "--dev-home",
                str(self.home),
            ],
            cwd=REPOSITORY_ROOT,
            env=self._environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="strict",
            text=True,
        )
        self.processes.append(process)
        return process

    def _spawn_surface_stdio(
        self,
        execution_surface: str,
    ) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.cli",
                "surface-stdio",
                "--execution-surface",
                execution_surface,
                "--dev-home",
                str(self.home),
            ],
            cwd=REPOSITORY_ROOT,
            env=self._environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="strict",
            text=True,
        )
        self.processes.append(process)
        return process

    def _write_jsonl(
        self,
        process: subprocess.Popen[str],
        value: object,
    ) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(value) + "\n")
        process.stdin.flush()

    def _read_jsonl(
        self,
        process: subprocess.Popen[str],
        *,
        timeout_seconds: float = PUBLIC_IO_TIMEOUT_SECONDS + 1.0,
    ) -> dict[str, object]:
        assert process.stdout is not None
        lines: queue.Queue[str | BaseException] = queue.Queue()

        def read_line() -> None:
            try:
                lines.put(process.stdout.readline())
            except BaseException as exc:
                lines.put(exc)

        threading.Thread(target=read_line, daemon=True).start()
        try:
            line = lines.get(timeout=timeout_seconds)
        except queue.Empty:
            if process.poll() is not None:
                assert process.stderr is not None
                stderr = process.stderr.read().strip()
                self.fail(
                    "surface-stdio exited before responding "
                    f"(code={process.returncode}): {stderr}"
                )
            self.fail(f"surface-stdio did not respond within {timeout_seconds:.1f}s")
        if isinstance(line, BaseException):
            raise line
        if line == "":
            process.wait(timeout=timeout_seconds)
            assert process.stderr is not None
            stderr = process.stderr.read().strip()
            self.fail(
                "surface-stdio exited before responding "
                f"(code={process.returncode}): {stderr}"
            )
        value = json.loads(line)
        self.assertIsInstance(value, dict)
        return value

    def _assert_surface_response(
        self,
        response: dict[str, object],
        *,
        request_id: str,
        ok: bool,
    ) -> dict[str, object]:
        expected_keys = {"schema", "id", "ok", "result"} if ok else {
            "schema",
            "id",
            "ok",
            "error",
        }
        self.assertEqual(set(response), expected_keys)
        self.assertEqual(response["schema"], "agentic-evo.surface-stdio.v1")
        self.assertEqual(response["id"], request_id)
        self.assertIs(response["ok"], ok)
        if ok:
            result = response["result"]
            self.assertIsInstance(result, dict)
            return result
        error = response["error"]
        self.assertIsInstance(error, dict)
        self.assertEqual(set(error), {"code", "message"})
        self.assertIsInstance(error["code"], str)
        self.assertIsInstance(error["message"], str)
        self.assertTrue(error["message"])
        return error

    def _assert_surface_error(
        self,
        response: dict[str, object],
        *,
        request_id: str,
        code: str,
    ) -> None:
        error = self._assert_surface_response(
            response,
            request_id=request_id,
            ok=False,
        )
        self.assertEqual(error["code"], code)

    def _assert_observe_receipt_shape(self, result: dict[str, object]) -> None:
        self.assertEqual(
            set(result),
            {"event_id", "sequence", "integrity_hash"},
        )
        self.assertIsInstance(result["event_id"], str)
        self.assertTrue(result["event_id"])
        self.assertIsInstance(result["sequence"], int)
        self.assertGreater(result["sequence"], 0)
        self.assertIsInstance(result["integrity_hash"], str)
        self.assertTrue(result["integrity_hash"])

    def _run_cli(
        self,
        *arguments: str,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.cli",
                *arguments,
                "--dev-home",
                str(self.home),
            ],
            cwd=REPOSITORY_ROOT,
            env=self._environment(),
            input=input_text,
            capture_output=True,
            encoding="utf-8",
            errors="strict",
            text=True,
            timeout=10,
            check=False,
        )

    def _run_detached_cli(
        self,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.cli",
                *arguments,
            ],
            cwd=REPOSITORY_ROOT,
            env=self._environment(),
            capture_output=True,
            encoding="utf-8",
            errors="strict",
            text=True,
            timeout=10,
            check=False,
        )

    def _export_experiment_prereg(self) -> dict[str, object]:
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        arguments = ["export-experiment-prereg"]
        for ref in EXPERIMENT_HYPOTHESIS_REFS:
            arguments.extend(("--hypothesis-ref", ref))
        for ref in EXPERIMENT_CONTROL_REFS:
            arguments.extend(("--control-ref", ref))
        try:
            result = self._run_cli(*arguments)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                result.stdout.encode("utf-8"),
                canonical_json_bytes({"ok": True, "result": payload["result"]}) + b"\n",
            )
            self.assertEqual(set(payload), {"ok", "result"})
            self.assertIs(payload["ok"], True)
            self.assertIsInstance(payload["result"], dict)
            return payload["result"]
        finally:
            self.assertEqual(self.runtime.status(), before_status)
            self.assertEqual(self.runtime.evidence.records(), before_records)

    def _prepare_experiment_post_prereg_fixture(self) -> tuple[Path, int]:
        self.runtime.observe(
            event_kind="prereg_measurement",
            payload={"measurement": "prereg"},
            execution_surface="test-surface",
            session_id="test-session",
            project_environment="test-project",
        )
        prereg_path = Path(self.tempdir.name) / "experiment-prereg.json"
        prereg_path.write_bytes(canonical_json_bytes(self._export_experiment_prereg()))

        self.runtime.observe(
            event_kind="post_prereg_measurement",
            payload={"measurement": "post-prereg"},
            execution_surface="test-surface",
            session_id="test-session",
            project_environment="test-project",
        )
        status = self.runtime.status()
        candidate = self.runtime._prepare_successor(
            expected_parent=status.head,
            files={"entrypoint.md": "Body one"},
            author_kind="research_instrument",
            ingress_path="test_instrument",
            expected_authority_epoch=self.runtime.trusted.authority_epoch(),
        )
        self.runtime._advance_head(
            expected_head=status.head,
            candidate_head=candidate,
            author_kind="research_instrument",
            ingress_path="test_instrument",
            expected_authority_epoch=self.runtime.trusted.authority_epoch(),
        )
        self.runtime.observe(
            event_kind="post_head_measurement",
            payload={"measurement": "post-head"},
            execution_surface="test-surface",
            session_id="test-session",
            project_environment="test-project",
        )
        tail_sequence = self.runtime.evidence.records()[-1].sequence
        return prereg_path, tail_sequence

    def _export_experiment_pack(
        self,
        prereg_path: Path,
        *,
        end_sequence: int,
    ) -> dict[str, object]:
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        try:
            result = self._run_cli(
                "export-experiment-pack",
                "--prereg",
                str(prereg_path),
                "--end-sequence",
                str(end_sequence),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                result.stdout.encode("utf-8"),
                canonical_json_bytes({"ok": True, "result": payload["result"]}) + b"\n",
            )
            self.assertEqual(set(payload), {"ok", "result"})
            self.assertIs(payload["ok"], True)
            self.assertIsInstance(payload["result"], dict)
            return payload["result"]
        finally:
            self.assertEqual(self.runtime.status(), before_status)
            self.assertEqual(self.runtime.evidence.records(), before_records)

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    def _wait_until_ready(
        self,
        process: subprocess.Popen[str],
    ) -> SurfaceClient:
        client = SurfaceClient(self.home)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                _, stderr = process.communicate(timeout=1)
                self.fail(
                    f"CLI service exited before ready "
                    f"(code={process.returncode}): {stderr}"
                )
            try:
                client.status()
                return client
            except ServiceUnavailableError:
                time.sleep(0.02)
        self.fail("CLI service did not become ready")

    def test_export_experiment_prereg_command_prints_canonical_json_without_runtime_mutation(
        self,
    ) -> None:
        self.runtime.observe(
            event_kind="prereg_measurement",
            payload={"measurement": "prereg"},
            execution_surface="test-surface",
            session_id="test-session",
            project_environment="test-project",
        )
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        arguments = ["export-experiment-prereg"]
        for ref in EXPERIMENT_HYPOTHESIS_REFS:
            arguments.extend(("--hypothesis-ref", ref))
        for ref in EXPERIMENT_CONTROL_REFS:
            arguments.extend(("--control-ref", ref))
        try:
            result = self._run_cli(*arguments)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                result.stdout.encode("utf-8"),
                canonical_json_bytes({"ok": True, "result": payload["result"]}) + b"\n",
            )
            self.assertEqual(set(payload), {"ok", "result"})
            self.assertIs(payload["ok"], True)
            self.assertIsInstance(payload["result"], dict)
        finally:
            self.assertEqual(self.runtime.status(), before_status)
            self.assertEqual(self.runtime.evidence.records(), before_records)

    def test_export_experiment_pack_command_prints_canonical_json_without_runtime_mutation(
        self,
    ) -> None:
        prereg_path, tail_sequence = self._prepare_experiment_post_prereg_fixture()

        self._export_experiment_pack(prereg_path, end_sequence=tail_sequence)

    def test_verify_experiment_artifact_command_returns_zero_for_exported_pack(self) -> None:
        prereg_path, tail_sequence = self._prepare_experiment_post_prereg_fixture()
        artifact = Path(self.tempdir.name) / "experiment-pack.json"
        artifact.write_bytes(
            canonical_json_bytes(
                self._export_experiment_pack(prereg_path, end_sequence=tail_sequence)
            )
        )

        result = self._run_detached_cli(
            "verify-experiment-artifact",
            "--artifact",
            str(artifact),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload), {"ok", "result"})
        self.assertIs(payload["ok"], True)
        self.assertIsInstance(payload["result"], dict)

    def test_verify_experiment_artifact_command_returns_six_for_invalid_artifact_json(
        self,
    ) -> None:
        artifact = Path(self.tempdir.name) / "invalid-experiment-artifact.json"
        artifact.write_text("{not-json", encoding="utf-8")

        result = self._run_detached_cli(
            "verify-experiment-artifact",
            "--artifact",
            str(artifact),
        )

        self.assertEqual(result.returncode, 6)
        payload = json.loads(result.stderr)
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["error"]["code"], "experiment_artifact_error")

    def test_verify_experiment_artifact_command_returns_seven_for_tampered_pack(
        self,
    ) -> None:
        prereg_path, tail_sequence = self._prepare_experiment_post_prereg_fixture()
        pack = self._export_experiment_pack(prereg_path, end_sequence=tail_sequence)
        claim_ceiling = pack.get("claim_ceiling")
        self.assertIsInstance(claim_ceiling, dict)
        tampered_pack = dict(pack)
        tampered_claim_ceiling = dict(claim_ceiling)
        tampered_claim_ceiling["future_capability_gain"] = "established"
        tampered_pack["claim_ceiling"] = tampered_claim_ceiling
        artifact = Path(self.tempdir.name) / "tampered-experiment-pack.json"
        artifact.write_bytes(canonical_json_bytes(tampered_pack))

        result = self._run_detached_cli(
            "verify-experiment-artifact",
            "--artifact",
            str(artifact),
        )

        self.assertEqual(result.returncode, 7)
        payload = json.loads(result.stderr)
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["result"]["code"], "claim_ceiling_changed")

    def test_export_experiment_pack_command_returns_six_for_nonforward_window(
        self,
    ) -> None:
        self.runtime.observe(
            event_kind="prereg_measurement",
            payload={"measurement": "prereg"},
            execution_surface="test-surface",
            session_id="test-session",
            project_environment="test-project",
        )
        prereg_path = Path(self.tempdir.name) / "experiment-prereg.json"
        prereg_path.write_bytes(canonical_json_bytes(self._export_experiment_prereg()))
        start_sequence = self.runtime.evidence.records()[-1].sequence

        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()

        result = self._run_cli(
            "export-experiment-pack",
            "--prereg",
            str(prereg_path),
            "--end-sequence",
            str(start_sequence),
        )

        self.assertEqual(result.returncode, 6)
        payload = json.loads(result.stderr)
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["error"]["code"], "experiment_artifact_error")
        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)

    def test_export_experiment_pack_command_requires_end_sequence(self) -> None:
        prereg_path = Path(self.tempdir.name) / "experiment-prereg.json"
        prereg_path.write_bytes(canonical_json_bytes(self._export_experiment_prereg()))

        result = self._run_cli(
            "export-experiment-pack",
            "--prereg",
            str(prereg_path),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--end-sequence", result.stderr)

    def test_export_experiment_pack_command_returns_six_for_end_sequence_beyond_tail_without_clamp(
        self,
    ) -> None:
        prereg_path, tail_sequence = self._prepare_experiment_post_prereg_fixture()
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()

        result = self._run_cli(
            "export-experiment-pack",
            "--prereg",
            str(prereg_path),
            "--end-sequence",
            str(tail_sequence + 1),
        )

        self.assertEqual(result.returncode, 6)
        payload = json.loads(result.stderr)
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["error"]["code"], "experiment_artifact_error")
        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)

    def test_surface_stdio_status_uses_external_process_boundary(self) -> None:
        service = self._spawn_service()
        client = self._wait_until_ready(service)
        before_records = self.runtime.evidence.records()
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "status-1",
                "op": "status",
                "args": {},
            },
        )
        response = self._read_jsonl(process)
        direct = client.status()

        result = self._assert_surface_response(
            response,
            request_id="status-1",
            ok=True,
        )
        self.assertEqual(result, direct)
        self.assertEqual(self.runtime.evidence.records(), before_records)

    def test_surface_stdio_wake_and_sleep_bind_fixed_execution_surface(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "wake-1",
                "op": "wake",
                "args": {"session_id": "s1", "project_environment": "proj-a"},
            },
        )
        wake = self._assert_surface_response(
            self._read_jsonl(process),
            request_id="wake-1",
            ok=True,
        )
        self.assertEqual(
            set(wake),
            {
                "root",
                "head",
                "generation",
                "body_files",
                "activation_kind",
                "activation_artifact",
                "activation_digest",
                "activation_context",
                "body_file_count",
                "body_files_truncated",
                "activation_context_char_count",
                "activation_context_truncated",
            },
        )
        session_start = self.runtime.evidence.records()[-1]
        self.assertEqual(session_start.event_kind, "session_start")
        self.assertEqual(session_start.execution_surface, "generic-stdio")
        self.assertEqual(session_start.session_id, "s1")
        self.assertEqual(session_start.author_kind, "surface_unverified")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "sleep-1",
                "op": "sleep",
                "args": {"session_id": "s1"},
            },
        )
        sleep = self._assert_surface_response(
            self._read_jsonl(process),
            request_id="sleep-1",
            ok=True,
        )
        self.assertEqual(
            set(sleep),
            {
                "root",
                "head",
                "generation",
                "authority",
                "lifecycle_state",
                "active_sessions",
                "active_session_count",
                "active_sessions_truncated",
                "service_version",
                "instrument_version",
                "instrument_version_char_count",
                "instrument_version_truncated",
                "protocol_version",
                "protocol_version_char_count",
                "protocol_version_truncated",
            },
        )
        session_end = self.runtime.evidence.records()[-1]
        self.assertEqual(session_end.event_kind, "session_end")
        self.assertEqual(session_end.execution_surface, "generic-stdio")
        self.assertEqual(session_end.session_id, "s1")
        self.assertFalse(
            any(
                session.execution_surface == "generic-stdio"
                for session in self.runtime.status().active_sessions
            )
        )

    def test_surface_stdio_same_raw_session_id_coexists_with_codex_surface(self) -> None:
        service = self._spawn_service()
        client = self._wait_until_ready(service)
        client.wake(
            execution_surface="codex",
            session_id="shared",
            project_environment="proj-a",
        )
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "wake-shared",
                "op": "wake",
                "args": {"session_id": "shared", "project_environment": "proj-a"},
            },
        )
        self._assert_surface_response(
            self._read_jsonl(process),
            request_id="wake-shared",
            ok=True,
        )
        self.assertEqual(
            {
                (session["execution_surface"], session["session_id"])
                for session in client.status()["active_sessions"]
            },
            {("codex", "shared"), ("generic-stdio", "shared")},
        )

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "sleep-shared",
                "op": "sleep",
                "args": {"session_id": "shared"},
            },
        )
        self._assert_surface_response(
            self._read_jsonl(process),
            request_id="sleep-shared",
            ok=True,
        )
        self.assertEqual(
            client.status()["active_sessions"],
            [{"execution_surface": "codex", "session_id": "shared"}],
        )

    def test_surface_stdio_observe_allows_omitted_session_id(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "observe-1",
                "op": "observe",
                "args": {"event_kind": "tool_result", "payload": {"outcome": "ok"}},
            },
        )
        receipt = self._assert_surface_response(
            self._read_jsonl(process),
            request_id="observe-1",
            ok=True,
        )
        self._assert_observe_receipt_shape(receipt)
        record = self.runtime.evidence.records()[-1]
        self.assertEqual(record.execution_surface, "generic-stdio")
        self.assertIsNone(record.session_id)

    def test_surface_stdio_allows_empty_optional_text_fields(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "wake-empty-optional",
                "op": "wake",
                "args": {
                    "session_id": "session-empty-optional",
                    "project_environment": "project-empty-optional",
                    "model": "",
                },
            },
        )
        self._assert_surface_response(
            self._read_jsonl(process),
            request_id="wake-empty-optional",
            ok=True,
        )
        self.assertEqual(self.runtime.evidence.records()[-1].payload["model_ref"], "")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "observe-empty-optional",
                "op": "observe",
                "args": {
                    "event_kind": "tool_result",
                    "payload": {"outcome": "ok"},
                    "occurred_at": "",
                    "session_id": "",
                    "turn_id": "",
                    "tool_call_id": "",
                    "project_environment": "",
                    "correlation_ref": "",
                    "causation_ref": "",
                    "parent_ref": "",
                    "coverage_gap": "",
                },
            },
        )
        receipt = self._assert_surface_response(
            self._read_jsonl(process),
            request_id="observe-empty-optional",
            ok=True,
        )
        self._assert_observe_receipt_shape(receipt)
        record = self.runtime.evidence.records()[receipt["sequence"] - 1]
        self.assertEqual(record.occurred_at, "")
        self.assertEqual(record.session_id, "")
        self.assertEqual(record.turn_id, "")
        self.assertEqual(record.tool_call_id, "")
        self.assertEqual(record.project_environment, "")
        self.assertEqual(record.correlation_ref, "")
        self.assertEqual(record.causation_ref, "")
        self.assertEqual(record.parent_ref, "")
        self.assertEqual(record.coverage_gap, "")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "observe-null-optional",
                "op": "observe",
                "args": {
                    "event_kind": "tool_result",
                    "payload": {"outcome": "null"},
                    "occurred_at": None,
                    "correlation_ref": None,
                    "causation_ref": None,
                    "parent_ref": None,
                },
            },
        )
        null_receipt = self._assert_surface_response(
            self._read_jsonl(process),
            request_id="observe-null-optional",
            ok=True,
        )
        null_record = self.runtime.evidence.records()[null_receipt["sequence"] - 1]
        self.assertIsInstance(null_record.occurred_at, str)
        self.assertIsNone(null_record.correlation_ref)
        self.assertIsNone(null_record.causation_ref)
        self.assertIsNone(null_record.parent_ref)

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "observe-omitted-optional",
                "op": "observe",
                "args": {
                    "event_kind": "tool_result",
                    "payload": {"outcome": "omitted"},
                },
            },
        )
        omitted_receipt = self._assert_surface_response(
            self._read_jsonl(process),
            request_id="observe-omitted-optional",
            ok=True,
        )
        omitted_record = self.runtime.evidence.records()[omitted_receipt["sequence"] - 1]
        self.assertIsInstance(omitted_record.occurred_at, str)
        self.assertIsNone(omitted_record.correlation_ref)
        self.assertIsNone(omitted_record.causation_ref)
        self.assertIsNone(omitted_record.parent_ref)

    def test_surface_stdio_rejects_public_frame_oversize_locally(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        execution_surface = "s" * 1024
        text = "t" * 1024
        payload = {
            "payload": "x"
            * (60 * 1024 - len(canonical_json_bytes({"payload": ""})))
        }
        self.assertEqual(len(canonical_json_bytes(payload)), 60 * 1024)
        self.assertGreater(
            len(
                canonical_json_bytes(
                    build_public_request(
                        "observe",
                        {
                            "event_kind": text,
                            "payload": payload,
                            "execution_surface": execution_surface,
                            "session_id": text,
                            "turn_id": text,
                            "tool_call_id": text,
                            "project_environment": text,
                            "coverage_gap": text,
                        },
                        request_id="observe-public-frame-oversize",
                    )
                )
            ),
            MAX_PUBLIC_FRAME_BYTES,
        )
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        process = self._spawn_surface_stdio(execution_surface)

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "observe-public-frame-oversize",
                "op": "observe",
                "args": {
                    "event_kind": text,
                    "payload": payload,
                    "session_id": text,
                    "turn_id": text,
                    "tool_call_id": text,
                    "project_environment": text,
                    "coverage_gap": text,
                },
            },
        )
        error = self._assert_surface_response(
            self._read_jsonl(process),
            request_id="observe-public-frame-oversize",
            ok=False,
        )
        self.assertEqual(error["code"], "invalid_input")
        self.assertEqual(
            error["message"],
            "operation arguments exceed the public frame byte bound",
        )
        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)
        self.assertIsNone(process.poll())

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "status-after-public-frame-oversize",
                "op": "status",
                "args": {},
            },
        )
        self._assert_surface_response(
            self._read_jsonl(process),
            request_id="status-after-public-frame-oversize",
            ok=True,
        )

    def test_surface_stdio_preflight_uses_internal_public_request_id(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        request_id = "i" * MAX_SURFACE_STDIO_TEXT_BYTES
        text = "t" * MAX_SURFACE_STDIO_TEXT_BYTES
        execution_surface = "s" * MAX_SURFACE_STDIO_TEXT_BYTES
        params = {
            "event_kind": text,
            "payload": {"payload": ""},
            "execution_surface": execution_surface,
            "session_id": text,
            "turn_id": text,
            "tool_call_id": text,
            "project_environment": text,
            "coverage_gap": text,
        }
        payload = {
            "payload": "x"
            * (
                MAX_PUBLIC_FRAME_BYTES
                - len(
                    canonical_json_bytes(
                        build_public_request(
                            "observe",
                            params,
                            request_id="r" * 32,
                        )
                    )
                )
            )
        }
        params["payload"] = payload
        self.assertEqual(
            len(
                canonical_json_bytes(
                    build_public_request("observe", params, request_id="r" * 32)
                )
            ),
            MAX_PUBLIC_FRAME_BYTES,
        )
        self.assertLessEqual(
            len(canonical_json_bytes(payload)),
            60 * 1024,
        )
        self.assertGreater(
            len(
                canonical_json_bytes(
                    build_public_request("observe", params, request_id=request_id)
                )
            ),
            MAX_PUBLIC_FRAME_BYTES,
        )
        before_records = self.runtime.evidence.records()
        process = self._spawn_surface_stdio(execution_surface)

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": request_id,
                "op": "observe",
                "args": {
                    "event_kind": text,
                    "payload": payload,
                    "session_id": text,
                    "turn_id": text,
                    "tool_call_id": text,
                    "project_environment": text,
                    "coverage_gap": text,
                },
            },
        )
        response = self._read_jsonl(process)
        error = self._assert_surface_response(
            response,
            request_id=request_id,
            ok=False,
        )
        self.assertEqual(error["code"], "operation_failed")
        self.assertEqual(error["message"], "trusted runtime rejected the operation")
        self.assertEqual(self.runtime.evidence.records(), before_records)
        self.assertIsNone(process.poll())

        next_request_id = "j" * MAX_SURFACE_STDIO_TEXT_BYTES
        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": next_request_id,
                "op": "observe",
                "args": {
                    "event_kind": "tool_result",
                    "payload": {"outcome": "ok"},
                },
            },
        )
        receipt = self._assert_surface_response(
            self._read_jsonl(process),
            request_id=next_request_id,
            ok=True,
        )
        self._assert_observe_receipt_shape(receipt)
        self.assertEqual(len(self.runtime.evidence.records()), len(before_records) + 1)
        self.assertIsNone(process.poll())

    def test_surface_stdio_links_delayed_outcome_across_service_restart_and_surface(
        self,
    ) -> None:
        baseline = self.runtime.status()
        baseline_records = self.runtime.evidence.records()
        first = self._spawn_service()
        first_client = self._wait_until_ready(first)
        first_client.wake(
            execution_surface="codex",
            session_id="cause-session",
            project_environment="project-a",
        )
        cause_receipt = first_client.observe(
            event_kind="tool_result",
            payload={"outcome": "initial-result"},
            execution_surface="codex",
            session_id="cause-session",
            project_environment="project-a",
        )
        first_client.sleep(
            execution_surface="codex",
            session_id="cause-session",
        )
        self._terminate(first)

        second = self._spawn_service()
        self._wait_until_ready(second)
        process = self._spawn_surface_stdio("generic-stdio")
        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "wake-outcome",
                "op": "wake",
                "args": {
                    "session_id": "outcome-session",
                    "project_environment": "project-b",
                },
            },
        )
        self._assert_surface_response(
            self._read_jsonl(process),
            request_id="wake-outcome",
            ok=True,
        )
        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "observe-delayed-outcome",
                "op": "observe",
                "args": {
                    "event_kind": "delayed_outcome_observed",
                    "payload": {"outcome": "accepted"},
                    "occurred_at": "2026-08-04T12:34:56Z",
                    "session_id": "outcome-session",
                    "turn_id": "outcome-turn",
                    "tool_call_id": None,
                    "project_environment": "project-b",
                    "correlation_ref": "experiment-001-run-a",
                    "causation_ref": cause_receipt["event_id"],
                    "parent_ref": cause_receipt["event_id"],
                    "coverage_gap": None,
                },
            },
        )
        receipt = self._assert_surface_response(
            self._read_jsonl(process),
            request_id="observe-delayed-outcome",
            ok=True,
        )
        self._assert_observe_receipt_shape(receipt)
        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "sleep-outcome",
                "op": "sleep",
                "args": {"session_id": "outcome-session"},
            },
        )
        self._assert_surface_response(
            self._read_jsonl(process),
            request_id="sleep-outcome",
            ok=True,
        )

        reloaded = DevelopmentalRuntime.load(self.home)
        records = reloaded.evidence.records()
        self.assertEqual(reloaded.status().root, baseline.root)
        self.assertEqual(reloaded.status().head, baseline.head)
        self.assertEqual(len(records), len(baseline_records) + 6)
        self.assertEqual(reloaded.status().active_sessions, ())
        self.assertTrue(reloaded.evidence.verify())
        tail = records[-6:]
        self.assertEqual(
            [record.event_kind for record in tail],
            [
                "session_start",
                "tool_result",
                "session_end",
                "session_start",
                "delayed_outcome_observed",
                "session_end",
            ],
        )
        self.assertEqual(
            [record.sequence for record in tail],
            list(range(len(baseline_records) + 1, len(baseline_records) + 7)),
        )
        if baseline_records:
            self.assertEqual(tail[0].previous_integrity_hash, baseline_records[-1].integrity_hash)
        for earlier, later in zip(tail, tail[1:]):
            self.assertEqual(later.previous_integrity_hash, earlier.integrity_hash)

        cause = tail[1]
        outcome = tail[4]
        self.assertEqual(cause.event_id, cause_receipt["event_id"])
        self.assertEqual(outcome.event_id, receipt["event_id"])
        self.assertEqual(cause.payload["outcome"], "initial-result")
        self.assertEqual(cause.payload["loaded_body_head"], baseline.head)
        self.assertEqual(outcome.payload["outcome"], "accepted")
        self.assertEqual(outcome.payload["loaded_body_head"], baseline.head)
        self.assertEqual(outcome.occurred_at, "2026-08-04T12:34:56Z")
        self.assertEqual(outcome.correlation_ref, "experiment-001-run-a")
        self.assertEqual(outcome.causation_ref, cause.event_id)
        self.assertEqual(outcome.parent_ref, cause.event_id)
        self.assertEqual(
            [(record.correlation_ref, record.causation_ref, record.parent_ref) for record in tail],
            [
                (None, None, None),
                (None, None, None),
                (None, None, None),
                (None, None, None),
                ("experiment-001-run-a", cause.event_id, cause.event_id),
                (None, None, None),
            ],
        )
        self.assertEqual(
            [record.execution_surface for record in tail],
            ["codex", "codex", "codex", "generic-stdio", "generic-stdio", "generic-stdio"],
        )
        self.assertEqual(
            [record.session_id for record in tail],
            ["cause-session", "cause-session", "cause-session", "outcome-session", "outcome-session", "outcome-session"],
        )
        self.assertEqual(
            [record.project_environment for record in tail],
            ["project-a", "project-a", "project-a", "project-b", "project-b", "project-b"],
        )
        for record in tail:
            self.assertEqual(record.source_kind, "execution_surface")
            self.assertEqual(record.author_kind, "surface_unverified")
            self.assertIsNone(record.human_intervention_kind)
        self.assertEqual(tail[2].payload, {"session_was_active": True})
        self.assertEqual(tail[5].payload, {"session_was_active": True})

    def test_surface_stdio_rejects_invalid_temporal_and_causal_refs_locally_without_mutation(
        self,
    ) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        process = self._spawn_surface_stdio("generic-stdio")

        for field in (
            "occurred_at",
            "correlation_ref",
            "causation_ref",
            "parent_ref",
        ):
            for suffix, value, message in (
                ("false", False, f"{field} must be a string or null"),
                ("long", "x" * 1025, f"{field} exceeds the stdio text byte bound"),
            ):
                with self.subTest(field=field, value=suffix):
                    request_id = f"invalid-{field}-{suffix}"
                    self._write_jsonl(
                        process,
                        {
                            "schema": "agentic-evo.surface-stdio.v1",
                            "id": request_id,
                            "op": "observe",
                            "args": {
                                "event_kind": "tool_result",
                                "payload": {"outcome": "ignored"},
                                field: value,
                            },
                        },
                    )
                    error = self._assert_surface_response(
                        self._read_jsonl(process),
                        request_id=request_id,
                        ok=False,
                    )
                    self.assertEqual(error["code"], "invalid_input")
                    self.assertEqual(error["message"], message)
                    self.assertEqual(self.runtime.status(), before_status)
                    self.assertEqual(self.runtime.evidence.records(), before_records)

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "status-after-invalid-causal-ref",
                "op": "status",
                "args": {},
            },
        )
        self._assert_surface_response(
            self._read_jsonl(process),
            request_id="status-after-invalid-causal-ref",
            ok=True,
        )
        self.assertIsNone(process.poll())

    def test_surface_stdio_rejects_witness_owned_evidence_fields_locally(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        process = self._spawn_surface_stdio("generic-stdio")

        for field, value in {
            "source_kind": "body",
            "author_kind": "agent_self_authored",
            "human_intervention_kind": "none",
            "observed_at": "2026-08-04T12:34:56Z",
            "root_commitment": "forged-root",
            "head_before": "forged-head",
            "head_after": "forged-head",
        }.items():
            with self.subTest(field=field):
                request_id = f"forged-{field}"
                self._write_jsonl(
                    process,
                    {
                        "schema": "agentic-evo.surface-stdio.v1",
                        "id": request_id,
                        "op": "observe",
                        "args": {
                            "event_kind": "tool_result",
                            "payload": {"outcome": "ignored"},
                            field: value,
                        },
                    },
                )
                error = self._assert_surface_response(
                    self._read_jsonl(process),
                    request_id=request_id,
                    ok=False,
                )
                self.assertEqual(error["code"], "invalid_input")
                self.assertEqual(
                    error["message"],
                    "operation arguments do not match the stdio contract",
                )
                self.assertEqual(self.runtime.status(), before_status)
                self.assertEqual(self.runtime.evidence.records(), before_records)

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "status-after-forged-evidence-field",
                "op": "status",
                "args": {},
            },
        )
        self._assert_surface_response(
            self._read_jsonl(process),
            request_id="status-after-forged-evidence-field",
            ok=True,
        )
        self.assertIsNone(process.poll())

    def test_surface_stdio_invalid_line_returns_invalid_input_without_mutation(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        secret = "surface-stdio-secret-probe"
        process = self._spawn_surface_stdio("generic-stdio")

        assert process.stdin is not None
        process.stdin.write("{not-json-" + secret + "\n")
        process.stdin.flush()
        response = self._read_jsonl(process)

        self.assertEqual(set(response), {"schema", "id", "ok", "error"})
        self.assertEqual(response["schema"], "agentic-evo.surface-stdio.v1")
        self.assertIsNone(response["id"])
        self.assertIs(response["ok"], False)
        error = response["error"]
        self.assertIsInstance(error, dict)
        self.assertEqual(set(error), {"code", "message"})
        self.assertEqual(error["code"], "invalid_input")
        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)
        assert process.stdin is not None
        process.stdin.close()
        self.assertEqual(process.wait(timeout=5), 0)
        assert process.stderr is not None
        self.assertNotIn(secret, process.stderr.read())

    def test_surface_stdio_rejects_argument_mismatch_before_surface_call(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        process = self._spawn_surface_stdio("generic-stdio")

        for request_id, operation, args in (
            ("sleep-empty", "sleep", {}),
            ("observe-array", "observe", {"event_kind": "tool_result", "payload": []}),
        ):
            self._write_jsonl(
                process,
                {
                    "schema": "agentic-evo.surface-stdio.v1",
                    "id": request_id,
                    "op": operation,
                    "args": args,
                },
            )
            response = self._read_jsonl(process)
            self._assert_surface_error(
                response,
                request_id=request_id,
                code="invalid_input",
            )

        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)

    def test_surface_stdio_witness_unavailable_returns_service_unavailable_without_exit(self) -> None:
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "status-unavailable",
                "op": "status",
                "args": {},
            },
        )
        response = self._read_jsonl(process)

        self._assert_surface_error(
            response,
            request_id="status-unavailable",
            code="service_unavailable",
        )
        self.assertIsNone(process.poll())
        assert process.stdin is not None
        process.stdin.close()
        self.assertEqual(process.wait(timeout=5), 0)

    def test_surface_stdio_wake_requires_live_witness_and_preserves_state(
        self,
    ) -> None:
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "wake-unavailable",
                "op": "wake",
                "args": {"session_id": "s1", "project_environment": "proj-a"},
            },
        )
        response = self._read_jsonl(process)

        self._assert_surface_error(
            response,
            request_id="wake-unavailable",
            code="service_unavailable",
        )
        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)
        self.assertIsNone(process.poll())
        assert process.stdin is not None
        process.stdin.close()
        self.assertEqual(process.wait(timeout=5), 0)

    def test_surface_stdio_observe_requires_live_witness_and_preserves_state(
        self,
    ) -> None:
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "observe-unavailable",
                "op": "observe",
                "args": {
                    "event_kind": "tool_result",
                    "payload": {"outcome": "ok"},
                },
            },
        )
        response = self._read_jsonl(process)

        self._assert_surface_error(
            response,
            request_id="observe-unavailable",
            code="service_unavailable",
        )
        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)
        self.assertIsNone(process.poll())
        assert process.stdin is not None
        process.stdin.close()
        self.assertEqual(process.wait(timeout=5), 0)

    def test_surface_stdio_eof_exits_zero_without_implicit_sleep(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        process = self._spawn_surface_stdio("generic-stdio")

        self._write_jsonl(
            process,
            {
                "schema": "agentic-evo.surface-stdio.v1",
                "id": "wake-eof",
                "op": "wake",
                "args": {"session_id": "eof-session", "project_environment": "proj-a"},
            },
        )
        self._assert_surface_response(
            self._read_jsonl(process),
            request_id="wake-eof",
            ok=True,
        )
        assert process.stdin is not None
        process.stdin.close()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertIn(
            ("generic-stdio", "eof-session"),
            {
                (session.execution_surface, session.session_id)
                for session in self.runtime.status().active_sessions
            },
        )

    def test_cli_hook_uses_surface_and_status_reports_process_rehearsal(self) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)

        status_result = self._run_cli("status")
        self.assertEqual(status_result.returncode, 0, status_result.stderr)
        status = json.loads(status_result.stdout)
        self.assertTrue(status["ok"])
        self.assertEqual(status["result"]["body_rehearsal"]["state"], "ready")

        hook_result = self._run_cli(
            "hook",
            input_text=json.dumps(
                {
                    "session_id": "cli-session",
                    "cwd": "C:/work/project-a",
                    "hook_event_name": "SessionStart",
                    "model": "model-a",
                    "source": "startup",
                }
            ),
        )
        self.assertEqual(hook_result.returncode, 0, hook_result.stderr)
        hook_output = json.loads(hook_result.stdout)
        self.assertIn(
            "Body zero",
            hook_output["hookSpecificOutput"]["additionalContext"],
        )
        last = self.runtime.evidence.records()[-1]
        self.assertEqual(last.event_kind, "session_start")
        self.assertEqual(last.author_kind, "surface_unverified")

    @unittest.skipUnless(sys.platform == "win32", "Windows native contract")
    def test_windows_public_endpoint_rejects_generic_pipe_clients(self) -> None:
        service = self._spawn_service()
        client = self._wait_until_ready(service)
        endpoint = service_endpoint(self.home)
        generic = None
        try:
            with self.assertRaises(OSError) as denied:
                generic = Client(
                    endpoint.address,
                    family=endpoint.family,
                    authkey=None,
                )
            self.assertEqual(denied.exception.winerror, 5)
        finally:
            if generic is not None:
                generic.close()

        self.assertEqual(client.status()["root"], self.runtime.status().root)

    def test_rehearsal_off_survives_service_crash_and_restart(
        self,
    ) -> None:
        first = self._spawn_service()
        client = self._wait_until_ready(first)
        before = client.status()
        client.wake(
            execution_surface="codex",
            session_id="session-before-off",
            project_environment="project-a",
        )

        off_result = self._run_cli("off")
        self.assertEqual(off_result.returncode, 0, off_result.stderr)
        off_payload = json.loads(off_result.stdout)["result"]
        self.assertEqual(off_payload["authority"], "off")
        self.assertEqual(off_payload["body_rehearsal"]["state"], "absent")
        off_record = self.runtime.evidence.records()[-1]
        self.assertEqual(off_record.event_kind, "control_rehearsal_off")
        self.assertEqual(off_record.source_kind, "host_control_rehearsal")
        self.assertEqual(off_record.author_kind, "control_unverified")
        after_off = client.status()
        self.assertEqual(after_off["body_rehearsal"]["state"], "absent")
        self.assertEqual(after_off["active_sessions"], [])
        self.assertEqual(after_off["root"], before["root"])
        self.assertEqual(after_off["head"], before["head"])

        first.kill()
        first.wait(timeout=5)
        replacement = self._spawn_service()
        replacement_client = self._wait_until_ready(replacement)
        persisted = replacement_client.status()
        self.assertEqual(persisted["authority"], "off")
        self.assertEqual(persisted["body_rehearsal"]["state"], "absent")
        self.assertEqual(persisted["root"], before["root"])
        self.assertEqual(persisted["head"], before["head"])

    def test_off_requires_live_control_service_and_never_falls_back_to_sqlite(
        self,
    ) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        self._terminate(service)
        before = self.runtime.status()
        before_records = self.runtime.evidence.records()

        result = self._run_cli("off")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.runtime.status(), before)
        self.assertEqual(self.runtime.evidence.records(), before_records)

    def test_malformed_and_oversize_hook_input_fails_open_without_echo(
        self,
    ) -> None:
        service = self._spawn_service()
        self._wait_until_ready(service)
        before = self.runtime.evidence.records()
        secret = "private-hook-content-that-must-not-be-echoed"

        malformed = self._run_cli("hook", input_text="{not-json-" + secret)
        oversized = self._run_cli(
            "hook",
            input_text=secret + ("x" * (2 * 1024 * 1024)),
        )
        deeply_nested = self._run_cli(
            "hook",
            input_text=("[" * 100_000) + "0" + ("]" * 100_000),
        )

        for result in (malformed, oversized, deeply_nested):
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertNotIn(secret, result.stderr)
        self.assertEqual(self.runtime.evidence.records(), before)


if __name__ == "__main__":
    unittest.main()
