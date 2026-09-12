from __future__ import annotations

from dataclasses import replace
import io
import json
import os
from pathlib import Path
from queue import Queue
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import sys

from agentic_evo import body_process as body_process_module
from agentic_evo._util import canonical_json_bytes, sha256_hex
from agentic_evo.body_process import (
    BODY_BOOT_PROTOCOL,
    BODY_LINEAGE_PROTOCOL,
    BodyBootError,
    BodyProcessSupervisor,
    BootEnvelope,
    SpawnedBodyProcess,
    _development_result_from_frame,
    _body_worker_environment,
    _lpac_body_worker_environment,
    _require_lpac_token_contract,
    read_private_frame,
    validate_ready_echo,
    write_private_frame,
)
from agentic_evo.development_executor import (
    CurrentBodyLineageFacts,
    DevelopmentOpportunity,
)
from agentic_evo.organ_broker import OrganResponse, RecallTrace
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
            initial_body={
                "entrypoint.md": "Body zero",
                "develop.py": "raise AssertionError('a suffix is not an entrypoint')",
            },
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
        self.witness = WitnessCore(self.runtime, lease_seconds=60.0)
        self.supervisor = BodyProcessSupervisor(
            self.runtime,
            self.witness,
            ready_timeout_seconds=5.0,
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

    def _lineage_facts(self, head: str) -> CurrentBodyLineageFacts:
        manifest = self.runtime.body_store.read_manifest(head)
        return CurrentBodyLineageFacts(
            manifest_head=manifest.commitment,
            manifest_generation=manifest.generation,
            manifest_parent_head=manifest.parent_head,
            manifest_created_at=manifest.created_at,
            manifest_activation_kind=manifest.activation_kind,
            manifest_activation_artifact=manifest.activation_artifact,
            manifest_development_kind=manifest.development_kind,
            manifest_development_artifact=manifest.development_artifact,
            head_advanced_event_ref=None,
            head_advanced_event_status="no_recorded_head_advanced",
            latest_recorded_resolution_action="no_recorded_resolution",
            latest_recorded_resolution_record_ref=None,
        )

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
        self.assertIsNone(body.boot.development_kind)
        self.assertIsNone(body.boot.development_artifact)
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

    def test_development_offer_preserves_host_only_organ_metadata(self) -> None:
        status = self.runtime.status()
        opportunity = DevelopmentOpportunity(
            id="opportunity-1",
            reason="task_end",
            root=status.root,
            current_body_ref=status.head,
            after_sequence=0,
            organ_argv=("codex",),
            working_dir=str(self.home),
            lineage_facts=self._lineage_facts(status.head),
        )
        trace = RecallTrace(
            before_sequence=None,
            limit=1,
            returned_event_refs=(),
            has_more=False,
        )

        class StaticBroker:
            def __init__(self) -> None:
                self.requests = []
                self.cancelled = []

            def invoke(self, request, cancel_event):
                self.requests.append(request)
                return OrganResponse(
                    final_text='{"action":"no_change"}',
                    organ_call_ref="thread-1",
                    recall_trace=(trace,),
                )

            def cancel(self, opportunity_id):
                self.cancelled.append(opportunity_id)

        broker = StaticBroker()
        body = BodyProcessSupervisor(
            self.runtime,
            self.witness,
            ready_timeout_seconds=5.0,
            request_timeout_seconds=5.0,
            organ_broker=broker,
        ).spawn_current()
        self.bodies.append(body)

        result = body.offer_development(opportunity=opportunity)

        self.assertEqual(result.action, "no_change")
        self.assertEqual(result.organ_call_ref, "thread-1")
        self.assertEqual(result.recall_trace, (trace,))
        self.assertEqual(len(broker.requests), 1)
        self.assertIn("develop.py", broker.requests[0].prompt)

    def test_private_development_recall_records_bound_trace_for_final_result(
        self,
    ) -> None:
        status = self.runtime.status()
        opportunity = DevelopmentOpportunity(
            id="opportunity-direct-recall",
            reason="task_end",
            root=status.root,
            current_body_ref=status.head,
            after_sequence=0,
            organ_argv=("codex",),
            working_dir=str(self.home),
            lineage_facts=self._lineage_facts(status.head),
        )
        boot = BootEnvelope(
            protocol=BODY_BOOT_PROTOCOL,
            boot_session="boot-direct-recall",
            challenge="challenge-direct-recall",
            root=status.root,
            head=status.head,
            generation=status.generation,
            activation_kind="surface-context-utf8-v1",
            activation_artifact="entrypoint.md",
            activation_digest="a" * 64,
            body_package={},
        )
        body = object.__new__(SpawnedBodyProcess)
        body.boot = boot
        body._organ_guard = threading.Lock()
        body._active_organ_opportunity = opportunity
        body._organ_cancel_event = threading.Event()
        body._organ_revoked = False
        body._pending_rehearsal = ("offer_development", 1)
        body._active_development_deadline = time.monotonic() + 5.0
        broker_trace = RecallTrace(
            before_sequence=None,
            limit=1,
            returned_event_refs=("broker-page",),
            has_more=False,
        )
        body._organ_response = OrganResponse(
            final_text='{"action":"no_change"}',
            organ_call_ref="thread-1",
            recall_trace=(broker_trace,),
        )
        body._development_direct_recall_trace = []
        body.assert_bound = MagicMock()
        body._write_frame = MagicMock()
        observed: list[tuple[int | None, int]] = []

        def recall_provider(*, opportunity, before_sequence, limit, cancel_event):
            self.assertIs(opportunity, body._active_organ_opportunity)
            self.assertIs(cancel_event, body._organ_cancel_event)
            observed.append((before_sequence, limit))
            event_id = "direct-latest" if before_sequence is None else "direct-earlier"
            return ([{"event_id": event_id}], before_sequence is None)

        body._development_recall = recall_provider
        for before_sequence in (None, 7):
            body._handle_development_recall(
                {
                    "protocol": BODY_LINEAGE_PROTOCOL,
                    "kind": "development_recall",
                    "operation": "recall_experiences",
                    "boot_session": boot.boot_session,
                    "root": boot.root,
                    "opportunity_id": opportunity.id,
                    "bound_head": boot.head,
                    "sequence": 1,
                    "before_sequence": before_sequence,
                    "limit": 1,
                }
            )

        self.assertEqual(observed, [(None, 1), (7, 1)])
        self.assertEqual(len(body._development_direct_recall_trace), 2)
        result = _development_result_from_frame(
            {
                "protocol": BODY_LINEAGE_PROTOCOL,
                "kind": "development_result",
                "operation": "offer_development",
                "sequence": 1,
                "action": "no_change",
                "opportunity_id": opportunity.id,
                "current_body_ref": opportunity.current_body_ref,
                "organ_call_ref": "thread-1",
                "recall_trace": [broker_trace.to_mapping()],
            },
            opportunity=opportunity,
            organ_response=body._organ_response,
            organ_result_invalidated=False,
            direct_recall_trace=tuple(body._development_direct_recall_trace),
        )

        self.assertEqual(
            result.recall_trace,
            (
                RecallTrace(
                    before_sequence=None,
                    limit=1,
                    returned_event_refs=("direct-latest",),
                    has_more=True,
                ),
                RecallTrace(
                    before_sequence=7,
                    limit=1,
                    returned_event_refs=("direct-earlier",),
                    has_more=False,
                ),
                broker_trace,
            ),
        )

    def test_development_submit_successor_returns_advanced_action(self) -> None:
        status = self.runtime.status()
        opportunity = DevelopmentOpportunity(
            id="opportunity-submit",
            reason="task_end",
            root=status.root,
            current_body_ref=status.head,
            after_sequence=0,
            organ_argv=("codex",),
            working_dir=str(self.home),
            lineage_facts=self._lineage_facts(status.head),
        )
        trace = RecallTrace(
            before_sequence=None,
            limit=1,
            returned_event_refs=(),
            has_more=False,
        )

        class StaticBroker:
            def invoke(self, request, cancel_event):
                return OrganResponse(
                    final_text=json.dumps(
                        {
                            "action": "submit_successor",
                            "files": {"entrypoint.md": "Body successor"},
                            "activation_kind": "surface-context-utf8-v1",
                            "activation_artifact": "entrypoint.md",
                            "causation_ref": "experience-1",
                        },
                        separators=(",", ":"),
                    ),
                    organ_call_ref="thread-submit",
                    recall_trace=(trace,),
                )

            def cancel(self, opportunity_id):
                return None

        body = BodyProcessSupervisor(
            self.runtime,
            self.witness,
            ready_timeout_seconds=5.0,
            request_timeout_seconds=5.0,
            organ_broker=StaticBroker(),
        ).spawn_current()
        self.bodies.append(body)

        result = body.offer_development(opportunity=opportunity)

        self.assertEqual(result.action, "candidate_submitted")
        self.assertEqual(result.generation, 1)
        self.assertEqual(result.organ_call_ref, "thread-submit")
        self.assertEqual(result.recall_trace, (trace,))
        self.assertEqual(self.runtime.status().head, result.candidate_head)

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

        body = BodyProcessSupervisor(
            self.runtime,
            self.witness,
            # Keep a bounded deadline while allowing cold Windows process
            # startup and endpoint scanning of the 8+ MiB encoded package.
            ready_timeout_seconds=30.0,
        ).spawn_current()
        self.bodies.append(body)
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
            files={"entrypoint.md": "Body one"},
            causation_ref="",
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
        prepared = next(
            record
            for record in lineage_records
            if record.event_kind == "body_candidate_prepared"
        )
        self.assertEqual(prepared.causation_ref, "")

    def test_private_lineage_candidate_preserves_declared_causation(
        self,
    ) -> None:
        baseline = self.runtime.status()
        baseline_records = self.runtime.evidence.records()
        cause = self.runtime.observe(
            event_kind="tool_result",
            payload={"outcome": "initial-result"},
            execution_surface="codex",
            session_id="cause-session",
            project_environment="project-a",
        )
        body = self._spawn()
        candidate = body.rehearse_prepare_successor(
            files={"entrypoint.md": "Body caused by prior evidence"},
            causation_ref=cause.event_id,
        )
        advanced = body.rehearse_advance_head(candidate_head=candidate)
        self.assertTrue(body.wait_closed(timeout_seconds=5.0))
        reloaded = DevelopmentalRuntime.load(self.home)
        records = reloaded.evidence.records()
        self.assertEqual(advanced["head"], candidate)
        self.assertEqual(advanced["generation"], baseline.generation + 1)
        self.assertEqual(reloaded.status().root, baseline.root)
        self.assertEqual(reloaded.status().head, candidate)
        self.assertEqual(
            reloaded.status().generation,
            baseline.generation + 1,
        )
        self.assertTrue(reloaded.evidence.verify())
        self.assertEqual(len(records), len(baseline_records) + 3)
        cause_record, prepared, advanced_record = records[-3:]
        self.assertEqual(
            [record.event_kind for record in records[-3:]],
            ["tool_result", "body_candidate_prepared", "head_advanced"],
        )
        self.assertEqual(
            [record.sequence for record in records[-3:]],
            list(range(len(baseline_records) + 1, len(baseline_records) + 4)),
        )
        if baseline_records:
            self.assertEqual(
                cause_record.previous_integrity_hash,
                baseline_records[-1].integrity_hash,
            )
        self.assertEqual(
            prepared.previous_integrity_hash,
            cause_record.integrity_hash,
        )
        self.assertEqual(
            advanced_record.previous_integrity_hash,
            prepared.integrity_hash,
        )
        self.assertEqual(cause_record.event_id, cause.event_id)
        self.assertEqual(cause_record.root_commitment, baseline.root)
        self.assertEqual(cause_record.head_before, baseline.head)
        self.assertEqual(cause_record.head_after, baseline.head)
        self.assertEqual(cause_record.source_kind, "execution_surface")
        self.assertEqual(cause_record.author_kind, "surface_unverified")
        self.assertEqual(cause_record.payload, {"outcome": "initial-result"})
        self.assertEqual(prepared.root_commitment, baseline.root)
        self.assertEqual(prepared.head_before, baseline.head)
        self.assertEqual(prepared.head_after, baseline.head)
        self.assertEqual(prepared.source_kind, "body")
        self.assertEqual(prepared.author_kind, "in_process_rehearsal")
        self.assertEqual(prepared.causation_ref, cause.event_id)
        self.assertIsNone(prepared.correlation_ref)
        self.assertIsNone(prepared.parent_ref)
        self.assertIsNone(prepared.human_intervention_kind)
        self.assertEqual(
            prepared.payload,
            {
                "candidate_head": candidate,
                "expected_parent": baseline.head,
                "ingress_path": "in_process_rehearsal",
                "operation": "prepare_successor",
                "affected_domain": "body_lineage",
                "development_kind": None,
                "development_artifact": None,
            },
        )
        self.assertEqual(advanced_record.root_commitment, baseline.root)
        self.assertEqual(advanced_record.head_before, baseline.head)
        self.assertEqual(advanced_record.head_after, candidate)
        self.assertEqual(advanced_record.source_kind, "body")
        self.assertEqual(advanced_record.author_kind, "in_process_rehearsal")
        self.assertIsNone(advanced_record.causation_ref)
        self.assertIsNone(advanced_record.human_intervention_kind)
        self.assertEqual(
            advanced_record.payload,
            {
                "body_generation": baseline.generation + 1,
                "parent_head": baseline.head,
                "ingress_path": "in_process_rehearsal",
                "operation": "advance_head",
                "affected_domain": "body_lineage",
            },
        )
        manifest = reloaded.body_store.read_manifest(candidate)
        self.assertEqual(manifest.parent_head, baseline.head)
        self.assertEqual(manifest.generation, baseline.generation + 1)
        self.assertEqual(manifest.author_kind, "in_process_rehearsal")
        self.assertNotIn(
            "agent_self_authored",
            {prepared.author_kind, advanced_record.author_kind},
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
        def mutation_snapshot():
            return (
                self.runtime.status(),
                self.runtime.evidence.records(),
                tuple(
                    sorted(
                        path.name
                        for path in self.runtime.body_store.manifest_path.glob(
                            "*.json"
                        )
                    )
                ),
                tuple(
                    sorted(
                        path.name
                        for path in self.runtime.body_store.blob_path.iterdir()
                    )
                ),
            )

        body = self._spawn()
        before = mutation_snapshot()
        for value, message in (
            (False, "causation_ref must be a string or null"),
            (
                "é" * 513,
                "causation_ref exceeds the private lineage text byte bound",
            ),
        ):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(ValueError, f"^{message}$"):
                    body.rehearse_prepare_successor(
                        files={"entrypoint.md": "must not exist"},
                        causation_ref=value,
                    )
                self.assertEqual(mutation_snapshot(), before)
                self.assertTrue(body.is_alive())

        request = {
            "protocol": "agentic-evo-private-lineage-v1",
            "kind": "lineage_request",
            "boot_session": body.boot.boot_session,
            "sequence": True,
            "operation": "prepare_successor",
            "files": {"entrypoint.md": "malformed candidate"},
            "activation_kind": None,
            "activation_artifact": None,
            "causation_ref": None,
        }

        with self.assertRaises(BodyBootError):
            body._handle_lineage_request(request)

        request["sequence"] = 1
        for value in (False, "é" * 513):
            request["causation_ref"] = value
            with self.assertRaisesRegex(
                BodyBootError,
                "^private prepare request has invalid causation_ref$",
            ):
                body._handle_lineage_request(request)
            self.assertEqual(mutation_snapshot(), before)
            self.assertTrue(self.runtime.evidence.verify())
            request["sequence"] += 1

        request["causation_ref"] = None
        request["author_kind"] = "agent_self_authored"
        with self.assertRaises(BodyBootError):
            body._handle_lineage_request(request)
        self.assertEqual(mutation_snapshot(), before)
        self.assertTrue(self.runtime.evidence.verify())

    def test_private_prepare_preserves_an_explicit_development_descriptor(self) -> None:
        body = self._spawn()

        candidate = body.rehearse_prepare_successor(
            files={
                "entrypoint.md": "descriptor-aware successor",
                "develop.py": "def develop(context):\n    return {'action': 'no_change'}\n",
            },
            development_kind="python-development-v1",
            development_artifact="develop.py",
        )

        manifest = self.runtime.body_store.read_manifest(candidate)
        self.assertEqual(manifest.development_kind, "python-development-v1")
        self.assertEqual(manifest.development_artifact, "develop.py")

    def test_descriptor_body_never_falls_back_to_the_low_integrity_launcher(self) -> None:
        status = self.runtime.status()
        lease = self.witness.open_current_body_session(expected_head=status.head)
        candidate = lease.prepare_successor(
            files={
                "entrypoint.md": "descriptor-aware successor",
                "develop.py": "def develop(context):\n    return {'action': 'no_change'}\n",
            },
            development_kind="python-development-v1",
            development_artifact="develop.py",
        )
        with patch(
            "agentic_evo.body_lpac.is_lpac_body_runtime_available",
            return_value=True,
        ):
            lease.advance_head(candidate_head=candidate)
        lease.close()

        with (
            patch(
                "agentic_evo.body_lpac.is_lpac_body_runtime_available",
                return_value=False,
            ),
            patch(
                "agentic_evo.body_process.spawn_restricted_suspended_process"
            ) as low_integrity_spawn,
            self.assertRaisesRegex(BodyBootError, "Windows LPAC launch path"),
        ):
            BodyProcessSupervisor(self.runtime, self.witness).spawn_current()

        low_integrity_spawn.assert_not_called()

    def test_lpac_token_contract_requires_exact_registry_read_sid_set(self) -> None:
        expected_sid = "S-1-15-2-100"
        expected_capabilities = ("S-1-15-3-200",)
        valid_process = SimpleNamespace(
            token_profile=SimpleNamespace(
                is_app_container=True,
                is_less_privileged_app_container=True,
                appcontainer_sid=expected_sid,
                capability_count=1,
                capability_sids=expected_capabilities,
            )
        )

        _require_lpac_token_contract(
            valid_process,
            expected_appcontainer_sid=expected_sid,
            expected_capability_sids=expected_capabilities,
        )

        with self.assertRaisesRegex(
            BodyBootError,
            "fixed registryRead contract",
        ):
            _require_lpac_token_contract(
                valid_process,
                expected_appcontainer_sid=expected_sid,
                expected_capability_sids=(
                    expected_capabilities[0],
                    "S-1-15-3-extra",
                ),
            )

        for actual_capabilities in ((), ("S-1-15-3-unexpected",)):
            with self.subTest(actual_capabilities=actual_capabilities):
                invalid_process = SimpleNamespace(
                    token_profile=SimpleNamespace(
                        is_app_container=True,
                        is_less_privileged_app_container=True,
                        appcontainer_sid=expected_sid,
                        capability_count=len(actual_capabilities),
                        capability_sids=actual_capabilities,
                    )
                )
                with self.assertRaisesRegex(
                    BodyBootError,
                    "fixed registryRead contract",
                ):
                    _require_lpac_token_contract(
                        invalid_process,
                        expected_appcontainer_sid=expected_sid,
                        expected_capability_sids=expected_capabilities,
                    )

    @unittest.skipUnless(sys.platform == "win32", "Windows LPAC contract")
    def test_descriptor_body_rejects_wrong_lpac_capability_before_resume(self) -> None:
        status = self.runtime.status()
        lease = self.witness.open_current_body_session(expected_head=status.head)
        candidate = lease.prepare_successor(
            files={
                "entrypoint.md": "descriptor-aware successor",
                "develop.py": "def develop(context):\n    return {'action': 'no_change'}\n",
            },
            development_kind="python-development-v1",
            development_artifact="develop.py",
        )
        fake_profile = SimpleNamespace(
            sid=123,
            sid_string="S-1-15-2-123",
            close=MagicMock(),
        )
        fake_stage = SimpleNamespace(
            python_executable=Path("D:/rawle/test-lpac/python.exe"),
            payload_root=Path("D:/rawle/test-lpac/payload"),
            scratch_path=Path("D:/rawle/test-lpac/scratch"),
            close=MagicMock(),
        )
        fake_process = MagicMock()
        fake_process.process_handle = 456
        fake_process.poll.return_value = None
        fake_process.wait.return_value = 0
        fake_process.token_profile = SimpleNamespace(
            is_app_container=True,
            is_less_privileged_app_container=True,
            appcontainer_sid=fake_profile.sid_string,
            capability_count=1,
            capability_sids=("S-1-15-3-unexpected",),
        )
        fake_spawn = MagicMock(return_value=fake_process)
        fake_lpac = SimpleNamespace(
            BODY_LPAC_CAPABILITY_NAMES=("registryRead",),
            is_lpac_body_runtime_available=lambda: True,
            create_ephemeral_lpac_profile=MagicMock(return_value=fake_profile),
            lpac_body_capability_sids=lambda: ("S-1-15-3-expected",),
            stage_lpac_body_payload=MagicMock(return_value=fake_stage),
        )
        fake_launcher = SimpleNamespace(
            spawn_lpac_suspended_process=fake_spawn,
        )

        with patch.dict(
            sys.modules,
            {
                "agentic_evo.body_lpac": fake_lpac,
                "agentic_evo.windows_appcontainer": fake_launcher,
            },
        ):
            lease.advance_head(candidate_head=candidate)
            lease.close()
            with (
                patch("agentic_evo.body_process.KillOnCloseJob") as job_type,
                patch(
                    "agentic_evo.body_process.spawn_restricted_suspended_process"
                ) as low_integrity_spawn,
                self.assertRaisesRegex(
                    BodyBootError,
                    "fixed registryRead contract",
                ),
            ):
                BodyProcessSupervisor(self.runtime, self.witness).spawn_current()

        low_integrity_spawn.assert_not_called()
        fake_spawn.assert_called_once()
        self.assertEqual(
            fake_spawn.call_args.kwargs["capability_names"],
            ("registryRead",),
        )
        self.assertEqual(
            fake_spawn.call_args.kwargs["environment"]["LOCALAPPDATA"],
            str(fake_stage.scratch_path),
        )
        self.assertIn("-S", fake_spawn.call_args.args[0])
        fake_process.resume.assert_not_called()
        job_type.return_value.assign_handle.assert_not_called()
        job_type.return_value.close.assert_called_once()
        fake_stage.close.assert_called_once()

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
        finished = threading.Event()
        outcome: Queue[object] = Queue(maxsize=1)
        spawned_processes: list[object] = []

        def stalled_write(*args, **kwargs) -> None:
            entered.set()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if spawned_processes:
                    try:
                        if spawned_processes[0].poll() is not None:
                            raise BrokenPipeError("Body was killed")
                    except OSError:
                        raise BrokenPipeError("Body handle was closed")
                time.sleep(0.005)
            raise RuntimeError("test Body was never aborted")

        def spawn() -> None:
            try:
                outcome.put(supervisor.spawn_current())
            except BaseException as exc:
                outcome.put(exc)
            finally:
                finished.set()

        if sys.platform == "win32":
            spawn_name = (
                "agentic_evo.body_process.spawn_restricted_suspended_process"
            )
            native_spawn = (
                body_process_module.spawn_restricted_suspended_process
            )
        else:
            spawn_name = "agentic_evo.body_process.subprocess.Popen"
            native_spawn = body_process_module.subprocess.Popen

        def recording_spawn(*args, **kwargs):
            process = native_spawn(*args, **kwargs)
            spawned_processes.append(process)
            return process

        worker = threading.Thread(target=spawn, daemon=True)
        try:
            with (
                patch(spawn_name, side_effect=recording_spawn),
                patch(
                    "agentic_evo.body_process.write_private_frame",
                    side_effect=stalled_write,
                ),
            ):
                worker.start()
                self.assertTrue(entered.wait(0.5))
                self.assertTrue(
                    finished.wait(0.3),
                    "boot write ignored the configured ready deadline",
                )
        finally:
            for process in spawned_processes:
                try:
                    if process.poll() is None:
                        process.kill()
                except OSError:
                    pass
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
        finished = threading.Event()
        outcome: Queue[BaseException | None] = Queue(maxsize=1)

        def stalled_write(*args, **kwargs) -> None:
            entered.set()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    if body._process.poll() is not None:
                        raise BrokenPipeError("Body was killed")
                except OSError:
                    raise BrokenPipeError("Body handle was closed")
                time.sleep(0.005)
            raise RuntimeError("test Body was never aborted")

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
            try:
                if body._process.poll() is None:
                    body._process.kill()
            except OSError:
                pass
            worker.join(timeout=1.0)

        self.assertIsInstance(
            outcome.get_nowait(),
            BodyLineageOutcomeUnknown,
        )
        self.assertTrue(body.wait_closed(timeout_seconds=2.0))
        self.assertTrue(body._rehearsal_guard.acquire(timeout=0.1))
        body._rehearsal_guard.release()
        self.assertTrue(body._write_guard.acquire(timeout=0.1))
        body._write_guard.release()

    def test_deadline_writer_aborts_and_joins_its_blocked_writer(self) -> None:
        entered = threading.Event()
        aborted = threading.Event()
        writer_finished = threading.Event()
        process = MagicMock()
        process.poll.return_value = None
        process.kill.side_effect = aborted.set

        class KillAwareStream:
            def write(self, value) -> int:
                entered.set()
                aborted.wait(1.0)
                writer_finished.set()
                raise BrokenPipeError("reader was killed")

            def flush(self) -> None:
                pass

        with self.assertRaises(BodyBootError):
            body_process_module._write_private_frame_with_timeout(
                KillAwareStream(),
                {"value": "blocked"},
                timeout_seconds=0.05,
                process=process,
            )

        self.assertTrue(entered.is_set())
        process.kill.assert_called_once_with()
        self.assertTrue(writer_finished.is_set())

    def test_private_frame_writer_completes_short_writes(self) -> None:
        expected = canonical_json_bytes({"value": "short writes"}) + b"\n"

        class ShortWriter:
            def __init__(self) -> None:
                self.value = bytearray()
                self.flushed = False

            def write(self, value) -> int:
                count = min(3, len(value))
                self.value.extend(value[:count])
                return count

            def flush(self) -> None:
                self.flushed = True

        writer = ShortWriter()
        write_private_frame(writer, {"value": "short writes"})

        self.assertEqual(bytes(writer.value), expected)
        self.assertTrue(writer.flushed)

    def test_cleanup_tolerates_a_process_that_was_already_aborted(self) -> None:
        process = MagicMock()
        process.poll.return_value = None
        process.terminate.side_effect = PermissionError("already terminating")
        process.wait.return_value = 1

        body_process_module._terminate_process(process)

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=1.0)
        process.close.assert_called_once_with()

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
                body._organ_guard = threading.Lock()
                body._active_organ_opportunity = None
                body._organ_cancel_event = None
                body._organ_broker = None
                body._organ_response = None
                body._organ_invocation_seen = False
                body._organ_revoked = False
                body._organ_result_invalidated = False
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

    def test_lpac_worker_environment_uses_only_its_payload_and_scratch(self) -> None:
        payload_root = self.home / "lpac-payload"
        scratch_path = self.home / "lpac-scratch"
        with patch.dict(
            os.environ,
            {"AGENTIC_EVO_TEST_SECRET": "must-not-cross"},
        ):
            environment = _lpac_body_worker_environment(
                payload_root=payload_root,
                scratch_path=scratch_path,
            )

        self.assertEqual(environment["PYTHONPATH"], str(payload_root))
        self.assertEqual(environment["LOCALAPPDATA"], str(scratch_path))
        self.assertEqual(environment["TEMP"], str(scratch_path))
        self.assertEqual(environment["TMP"], str(scratch_path))
        self.assertNotIn("AGENTIC_EVO_TEST_SECRET", environment)
        self.assertLessEqual(
            set(environment),
            {
                "PYTHONPATH",
                "PYTHONUTF8",
                "PYTHONNOUSERSITE",
                "PYTHONDONTWRITEBYTECODE",
                "LOCALAPPDATA",
                "TEMP",
                "TMP",
                "SystemRoot",
                "WINDIR",
            },
        )

    def test_lpac_staging_is_released_when_the_body_retires(self) -> None:
        body = object.__new__(SpawnedBodyProcess)
        body._guard = threading.Lock()
        body._closed = threading.Event()
        body._private_writer = MagicMock()
        body._private_reader = MagicMock()
        body._process_fence = MagicMock()
        body._process = MagicMock()
        body._session = MagicMock()
        body._lpac_staging = MagicMock()

        body._retire()

        body._lpac_staging.close.assert_called_once()
        body._session.close.assert_called_once()
        body._process_fence.close.assert_called_once()
        body._process.close.assert_called_once()
        self.assertTrue(body._closed.is_set())


if __name__ == "__main__":
    unittest.main()
