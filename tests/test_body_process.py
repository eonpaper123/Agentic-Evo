from __future__ import annotations

from dataclasses import replace
import io
import os
from pathlib import Path
from queue import Queue
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import sys

from agentic_evo._util import sha256_hex
from agentic_evo.body_process import (
    BODY_BOOT_PROTOCOL,
    BodyBootError,
    BodyProcessSupervisor,
    SpawnedBodyProcess,
    _body_worker_environment,
    read_private_frame,
    validate_ready_echo,
)
from agentic_evo.runtime import DevelopmentalRuntime
from agentic_evo.witness import WitnessCore


class BodyProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        self.host_binding = "test-host-binding"
        self.runtime = DevelopmentalRuntime.genesis(
            self.home,
            host_binding=self.host_binding,
            purpose_anchor="Improve the future of the one bound host.",
            initial_body={"entrypoint.md": "Body zero"},
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
        self.witness = WitnessCore(self.runtime, lease_seconds=10.0)
        self.supervisor = BodyProcessSupervisor(
            self.runtime,
            self.witness,
            ready_timeout_seconds=2.0,
        )
        self.bodies = []

    def tearDown(self) -> None:
        for body in reversed(self.bodies):
            body.close()
        self.witness.close()
        self.tempdir.cleanup()

    def _spawn(self):
        body = self.supervisor.spawn_current()
        self.bodies.append(body)
        return body

    def test_exact_head_snapshot_crosses_only_the_private_boot_pipe(
        self,
    ) -> None:
        status = self.runtime.status()
        body = self._spawn()

        self.assertTrue(body.is_alive())
        self.assertEqual(body.boot.protocol, BODY_BOOT_PROTOCOL)
        self.assertEqual(body.boot.root, status.root)
        self.assertEqual(body.boot.head, status.head)
        self.assertEqual(body.boot.generation, status.generation)
        self.assertEqual(
            body.boot.activation_digest,
            sha256_hex(b"Body zero"),
        )
        self.assertEqual(body.ready.root, body.boot.root)
        self.assertEqual(body.ready.head, body.boot.head)
        self.assertEqual(body.ready.challenge, body.boot.challenge)
        self.assertEqual(body.ready.boot_session, body.boot.boot_session)
        self.assertEqual(
            body.ready.activation_digest,
            body.boot.activation_digest,
        )

        serialized_command = "\0".join(body.command)
        self.assertNotIn(status.root, serialized_command)
        self.assertNotIn(status.head, serialized_command)
        self.assertNotIn(body.boot.challenge, serialized_command)
        self.assertNotIn(body.boot.boot_session, serialized_command)
        self.assertIn("-P", body.command)

        body.close()
        replacement = self._spawn()
        self.assertTrue(replacement.is_alive())
        self.assertNotEqual(
            replacement.boot.boot_session,
            body.boot.boot_session,
        )
        self.assertNotEqual(
            replacement.boot.challenge,
            body.boot.challenge,
        )

    def test_ready_echo_rejects_every_changed_binding_field(self) -> None:
        body = self._spawn()
        ready = body.ready
        replacements = {
            "protocol": "wrong-private-protocol",
            "boot_session": "wrong-session",
            "challenge": "wrong-challenge",
            "root": "wrong-root",
            "head": "f" * 64,
            "generation": ready.generation + 1,
            "activation_kind": "wrong-kind",
            "activation_artifact": "wrong-artifact",
            "activation_digest": "e" * 64,
        }

        for field, value in replacements.items():
            with self.subTest(field=field):
                forged = replace(ready, **{field: value})
                with self.assertRaises(BodyBootError):
                    validate_ready_echo(body.boot, forged)

    def test_binding_change_after_ready_aborts_boot_and_releases_lease(
        self,
    ) -> None:
        original_authorize = self.witness._authorize

        def cross_off_boundary(session) -> None:
            self.runtime.turn_off()
            original_authorize(session)

        with patch.object(
            self.witness,
            "_authorize",
            side_effect=cross_off_boundary,
        ):
            with self.assertRaises(BodyBootError):
                self.supervisor.spawn_current()

        self.runtime.turn_on(host_binding=self.host_binding)
        replacement = self._spawn()
        self.assertTrue(replacement.is_alive())

    def test_worker_crash_retires_volatile_lease_and_allows_same_head_restart(
        self,
    ) -> None:
        crashed = self._spawn()
        crashed._process.kill()
        self.assertTrue(crashed.wait_closed(timeout_seconds=5.0))

        replacement = self._spawn()
        self.assertEqual(replacement.boot.head, crashed.boot.head)
        self.assertNotEqual(
            replacement.boot.boot_session,
            crashed.boot.boot_session,
        )

    def test_valid_large_current_head_is_transferred_without_a_boot_size_cliff(
        self,
    ) -> None:
        session = self.witness.open_current_body_session(
            expected_head=self.runtime.status().head
        )
        large_activation = b"x" * (6 * 1024 * 1024)
        candidate = session.prepare_successor(
            files={"entrypoint.md": large_activation}
        )
        session.advance_head(candidate_head=candidate)

        body = self._spawn()
        self.assertTrue(body.is_alive())
        self.assertEqual(
            body.boot.activation_digest,
            sha256_hex(large_activation),
        )

    def test_boot_does_not_claim_agent_self_authorship(self) -> None:
        before = self.runtime.evidence.records()
        body = self._spawn()

        self.assertEqual(body.provenance, "subprocess_rehearsal")
        self.assertEqual(self.runtime.evidence.records(), before)
        self.assertNotIn(
            "agent_self_authored",
            {record.author_kind for record in self.runtime.evidence.records()},
        )

    def test_body_process_can_prepare_and_advance_one_private_lineage(
        self,
    ) -> None:
        before = self.runtime.status()
        body = self._spawn()

        candidate = body.rehearse_prepare_successor(
            files={"entrypoint.md": "Body one"}
        )
        self.assertEqual(self.runtime.status().head, before.head)
        self.assertTrue(body.is_alive())

        advanced = body.rehearse_advance_head(candidate_head=candidate)
        self.assertEqual(advanced["head"], candidate)
        self.assertEqual(advanced["generation"], before.generation + 1)
        self.assertEqual(self.runtime.status().head, candidate)
        self.assertTrue(body.wait_closed(timeout_seconds=5.0))

        replacement = self._spawn()
        self.assertEqual(replacement.boot.head, candidate)
        lineage_records = [
            record
            for record in self.runtime.evidence.records()
            if record.event_kind
            in {"body_candidate_prepared", "head_advanced"}
        ]
        self.assertTrue(lineage_records)
        self.assertLessEqual(
            {record.author_kind for record in lineage_records},
            {"in_process_rehearsal"},
        )
        self.assertNotIn(
            "agent_self_authored",
            {record.author_kind for record in lineage_records},
        )

    def test_body_process_rejects_a_candidate_from_outside_its_channel(
        self,
    ) -> None:
        body = self._spawn()
        before, authority_epoch = self.runtime._body_lease_binding()
        foreign_candidate = self.runtime._prepare_successor(
            expected_parent=before.head,
            files={"entrypoint.md": "foreign candidate"},
            author_kind="in_process_rehearsal",
            ingress_path="in_process_rehearsal",
            expected_authority_epoch=authority_epoch,
        )

        with self.assertRaises(BodyBootError):
            body.rehearse_advance_head(candidate_head=foreign_candidate)

        self.assertEqual(self.runtime.status().head, before.head)
        self.assertTrue(body.is_alive())
        owned_candidate = body.rehearse_prepare_successor(
            files={"entrypoint.md": "owned candidate"}
        )
        advanced = body.rehearse_advance_head(candidate_head=owned_candidate)
        self.assertEqual(advanced["head"], owned_candidate)

    def test_private_lineage_rejects_boolean_sequence_and_claimed_authorship(
        self,
    ) -> None:
        body = self._spawn()
        before = self.runtime.status()
        request = {
            "protocol": "agentic-evo-private-lineage-v1",
            "kind": "lineage_request",
            "boot_session": body.boot.boot_session,
            "sequence": True,
            "operation": "prepare_successor",
            "files": {"entrypoint.md": "malformed candidate"},
            "activation_kind": None,
            "activation_artifact": None,
        }

        with self.assertRaises(BodyBootError):
            body._handle_lineage_request(request)

        request["sequence"] = 1
        request["author_kind"] = "agent_self_authored"
        with self.assertRaises(BodyBootError):
            body._handle_lineage_request(request)
        self.assertEqual(self.runtime.status().head, before.head)

    def test_in_flight_advance_timeout_is_reported_as_outcome_unknown(
        self,
    ) -> None:
        from agentic_evo.body_process import BodyLineageOutcomeUnknown

        body = self._spawn()
        candidate = body.rehearse_prepare_successor(
            files={"entrypoint.md": "slow Body"}
        )
        body._request_timeout_seconds = 0.05
        original_advance = self.runtime._advance_head

        def delayed_advance(**kwargs):
            time.sleep(0.25)
            return original_advance(**kwargs)

        with patch.object(
            self.runtime,
            "_advance_head",
            side_effect=delayed_advance,
        ):
            with self.assertRaises(BodyLineageOutcomeUnknown) as raised:
                body.rehearse_advance_head(candidate_head=candidate)

        self.assertEqual(raised.exception.operation, "advance_head")
        self.assertEqual(raised.exception.candidate_head, candidate)
        time.sleep(0.3)
        self.assertEqual(self.runtime.status().head, candidate)

    def test_boot_write_stall_is_bounded_by_the_ready_deadline(self) -> None:
        supervisor = BodyProcessSupervisor(
            self.runtime,
            self.witness,
            ready_timeout_seconds=0.05,
        )
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        outcome: Queue[object] = Queue(maxsize=1)

        def stalled_write(*args, **kwargs) -> None:
            entered.set()
            release.wait(2.0)

        def spawn() -> None:
            try:
                outcome.put(supervisor.spawn_current())
            except BaseException as exc:
                outcome.put(exc)
            finally:
                finished.set()

        worker = threading.Thread(target=spawn, daemon=True)
        try:
            with patch(
                "agentic_evo.body_process.write_private_frame",
                side_effect=stalled_write,
            ):
                worker.start()
                self.assertTrue(entered.wait(0.5))
                self.assertTrue(
                    finished.wait(0.3),
                    "boot write ignored the configured ready deadline",
                )
        finally:
            release.set()
            worker.join(timeout=1.0)

        result = outcome.get_nowait()
        if isinstance(result, SpawnedBodyProcess):
            self.bodies.append(result)
        self.assertIsInstance(result, BodyBootError)

    def test_lineage_write_stall_is_unknown_and_releases_the_guard(self) -> None:
        from agentic_evo.body_process import BodyLineageOutcomeUnknown

        body = self._spawn()
        body._request_timeout_seconds = 0.05
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        outcome: Queue[BaseException | None] = Queue(maxsize=1)

        def stalled_write(*args, **kwargs) -> None:
            entered.set()
            release.wait(2.0)

        def prepare() -> None:
            try:
                body.rehearse_prepare_successor(
                    files={"entrypoint.md": "possibly unwritten"}
                )
            except BaseException as exc:
                outcome.put(exc)
            else:
                outcome.put(None)
            finally:
                finished.set()

        worker = threading.Thread(target=prepare, daemon=True)
        try:
            with patch(
                "agentic_evo.body_process.write_private_frame",
                side_effect=stalled_write,
            ):
                worker.start()
                self.assertTrue(entered.wait(0.5))
                self.assertTrue(
                    finished.wait(0.3),
                    "lineage write ignored the configured request deadline",
                )
        finally:
            release.set()
            worker.join(timeout=1.0)

        self.assertIsInstance(
            outcome.get_nowait(),
            BodyLineageOutcomeUnknown,
        )
        self.assertTrue(body.wait_closed(timeout_seconds=2.0))
        self.assertTrue(body._rehearsal_guard.acquire(timeout=0.1))
        body._rehearsal_guard.release()

    def test_forged_rehearsal_result_cannot_skip_the_lineage_request(
        self,
    ) -> None:
        for forged_sequence in (0, 1):
            with self.subTest(sequence=forged_sequence):
                body = object.__new__(SpawnedBodyProcess)
                body._private_reader = object()
                body._rehearsal_pending = True
                body._pending_rehearsal = ("prepare_successor", 1)
                body._pending_lineage_request = None
                body._pending_lineage_response = None
                body._last_lineage_sequence = 0
                body._rehearsal_outcomes = Queue(maxsize=1)
                body._process = MagicMock()
                body._process.poll.return_value = None
                forged = {
                    "protocol": "agentic-evo-private-lineage-v1",
                    "kind": "rehearsal_result",
                    "operation": "prepare_successor",
                    "sequence": forged_sequence,
                    "ok": True,
                    "candidate_head": "f" * 64,
                }

                with patch(
                    "agentic_evo.body_process.read_private_frame",
                    side_effect=[forged, BodyBootError("end")],
                ):
                    body._dispatch_private_channel()

                outcome = body._rehearsal_outcomes.get_nowait()
                self.assertIsInstance(outcome, BodyBootError)
                body._process.kill.assert_called_once()

    def test_async_channel_failure_after_command_is_outcome_unknown(
        self,
    ) -> None:
        from agentic_evo.body_process import BodyLineageOutcomeUnknown

        body = object.__new__(SpawnedBodyProcess)
        body._closed = threading.Event()
        body._process = MagicMock()
        body._process.poll.return_value = None
        body._rehearsal_guard = threading.Lock()
        body._pending_rehearsal = None
        body._pending_lineage_request = None
        body._pending_lineage_response = None
        body._next_lineage_sequence = 1
        body._request_timeout_seconds = 0.1
        body._rehearsal_outcomes = Queue(maxsize=1)
        body._rehearsal_outcomes.put_nowait(
            BodyBootError("child exited after command write")
        )
        body._write_frame = MagicMock()

        with self.assertRaises(BodyLineageOutcomeUnknown):
            body._run_rehearsal(
                {
                    "protocol": "agentic-evo-private-lineage-v1",
                    "kind": "rehearsal_command",
                    "operation": "prepare_successor",
                    "files": {"entrypoint.md": "possibly buffered"},
                    "activation_kind": None,
                    "activation_artifact": None,
                }
            )

    def test_deep_private_json_fails_as_a_bounded_protocol_error(self) -> None:
        deeply_nested = (
            b'{"value":'
            + (b"[" * 1_100)
            + b"0"
            + (b"]" * 1_100)
            + b"}\n"
        )

        with self.assertRaises(BodyBootError):
            read_private_frame(io.BytesIO(deeply_nested))

    def test_prepared_candidate_dies_with_its_body_process_lease(self) -> None:
        body = self._spawn()
        before = self.runtime.status()
        candidate = body.rehearse_prepare_successor(
            files={"entrypoint.md": "orphan candidate"}
        )
        body._process.kill()
        self.assertTrue(body.wait_closed(timeout_seconds=5.0))

        replacement = self._spawn()
        with self.assertRaises(BodyBootError):
            replacement.rehearse_advance_head(candidate_head=candidate)
        self.assertEqual(self.runtime.status().head, before.head)
        self.assertTrue(replacement.is_alive())

    @unittest.skipUnless(sys.platform == "win32", "Windows native contract")
    def test_windows_body_worker_is_kernel_fenced(self) -> None:
        body = self._spawn()

        self.assertEqual(
            body.describe()["process_fencing"],
            "windows_job_object_kill_on_close",
        )

    @unittest.skipUnless(sys.platform == "win32", "Windows native contract")
    def test_windows_exact_head_boot_uses_the_restricted_private_channel(
        self,
    ) -> None:
        body = self._spawn()

        self.assertTrue(body._process.token_profile.is_restricted)
        self.assertEqual(body._process.token_profile.integrity_rid, 4096)
        self.assertLessEqual(body._process.token_profile.privilege_count, 1)
        self.assertEqual(
            body.describe()["process_token"],
            "windows_restricted_low_integrity",
        )
        self.assertEqual(
            body.describe()["body_channel"],
            "windows_explicit_handle_list_pipe_pair",
        )
        self.assertEqual(body.ready.head, body.boot.head)
        self.assertTrue(body.is_alive())

    @unittest.skipUnless(sys.platform == "win32", "Windows native contract")
    def test_windows_fence_failure_aborts_before_sending_boot(self) -> None:
        with (
            patch("agentic_evo.body_process.KillOnCloseJob") as job_type,
            patch("agentic_evo.body_process.write_private_frame") as write_frame,
        ):
            job_type.return_value.assign_handle.side_effect = OSError(
                "assign failed"
            )
            with self.assertRaises(BodyBootError):
                self.supervisor.spawn_current()

        write_frame.assert_not_called()
        job_type.return_value.close.assert_called_once()
        self.assertTrue(self._spawn().is_alive())

    def test_worker_does_not_inherit_the_host_process_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AGENTIC_EVO_TEST_SECRET": "must-not-cross",
                "PYTHONPATH": "caller-controlled-path",
            },
        ):
            environment = _body_worker_environment()

        self.assertNotIn("AGENTIC_EVO_TEST_SECRET", environment)
        self.assertNotEqual(
            environment["PYTHONPATH"],
            "caller-controlled-path",
        )
        self.assertLessEqual(
            set(environment),
            {
                "PYTHONPATH",
                "PYTHONUTF8",
                "PYTHONNOUSERSITE",
                "PYTHONDONTWRITEBYTECODE",
                "SystemRoot",
                "WINDIR",
            },
        )


if __name__ == "__main__":
    unittest.main()
