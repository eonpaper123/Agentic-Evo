from __future__ import annotations

from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from time import monotonic
import unittest
from unittest.mock import MagicMock, patch

from agentic_evo.body_process import (
    BODY_BOOT_PROTOCOL,
    BODY_LINEAGE_PROTOCOL,
    BodyBootError,
    BootEnvelope,
    _development_result_from_frame,
)
from agentic_evo.body import DEVELOPMENT_DESCRIPTOR_UNSET
from agentic_evo.body_worker import _run_development_offer
from agentic_evo.development_executor import (
    BodyActionResult,
    BodyDevelopmentOpportunity,
    CurrentBodyLineageFacts,
    DevelopmentExecutor,
    DevelopmentExecutorError,
    DevelopmentOpportunity,
    _parse_model_action,
    build_development_prompt,
)
from agentic_evo.organ_broker import (
    RecallTrace,
    OrganInvocationRequest,
    OrganResponse,
    WitnessOrganBroker,
    _ActiveOrganRun,
    _organ_command,
    _parse_recall_transport,
    _read_only_organ_args,
)
from agentic_evo.runtime import DevelopmentalRuntime
from agentic_evo.witness import WitnessCore


class _FakeBroker:
    def __init__(self, response: OrganResponse | None = None) -> None:
        self.response = response or OrganResponse(final_text='{"action":"no_change"}')
        self.invocations: list[OrganInvocationRequest] = []
        self.cancelled: list[str] = []

    def invoke(
        self,
        request: OrganInvocationRequest,
        cancel_event: threading.Event,
    ) -> OrganResponse:
        self.invocations.append(request)
        return self.response

    def cancel(self, opportunity_id: str) -> None:
        self.cancelled.append(opportunity_id)


class DevelopmentExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "home"
        self.workspace = Path(self.tempdir.name) / "workspace"
        self.workspace.mkdir()
        self.runtime = DevelopmentalRuntime.genesis(
            self.home,
            host_binding="test-host",
            purpose_anchor="Improve the future of the one bound host.",
            initial_body={"entrypoint.md": "Body owns its development method."},
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
        self.witness = WitnessCore(self.runtime, lease_seconds=60.0)

    def tearDown(self) -> None:
        self.witness.close()
        self.tempdir.cleanup()

    def _opportunity(self) -> DevelopmentOpportunity:
        status = self.runtime.status()
        return DevelopmentOpportunity(
            id="opportunity-1",
            reason="task_end",
            root=status.root,
            current_body_ref=status.head,
            after_sequence=0,
            organ_argv=(
                "codex",
                "--model",
                "gpt-test",
                "-c",
                'model_reasoning_effort="xhigh"',
            ),
            working_dir=str(self.workspace),
            lineage_facts=self._lineage_facts(status.head),
        )

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

    def test_body_projection_excludes_host_execution_configuration(self) -> None:
        opportunity = self._opportunity()

        projected = opportunity.to_body_mapping()

        self.assertEqual(
            set(projected),
            {
                "id",
                "reason",
                "root",
                "current_body_ref",
                "after_sequence",
                "lineage_facts",
            },
        )
        self.assertNotIn("organ_argv", projected)
        self.assertNotIn("working_dir", projected)
        self.assertEqual(
            BodyDevelopmentOpportunity.from_mapping(projected).id,
            opportunity.id,
        )

    def test_successor_action_preserves_descriptor_omission_set_and_clear(self) -> None:
        base = {
            "action": "submit_successor",
            "files": {
                "entrypoint.md": "successor activation",
                "develop.py": "def develop(context):\n    return {'action': 'no_change'}\n",
            },
            "activation_kind": "surface-context-utf8-v1",
            "activation_artifact": "entrypoint.md",
            "causation_ref": "experience-1",
        }

        inherited = _parse_model_action(json.dumps(base))
        self.assertIs(inherited.development_kind, DEVELOPMENT_DESCRIPTOR_UNSET)
        self.assertIs(inherited.development_artifact, DEVELOPMENT_DESCRIPTOR_UNSET)

        selected = _parse_model_action(
            json.dumps(
                {
                    **base,
                    "development_kind": "python-development-v1",
                    "development_artifact": "develop.py",
                }
            )
        )
        self.assertEqual(selected.development_kind, "python-development-v1")
        self.assertEqual(selected.development_artifact, "develop.py")

        cleared = _parse_model_action(
            json.dumps(
                {
                    **base,
                    "development_kind": None,
                    "development_artifact": None,
                }
            )
        )
        self.assertIsNone(cleared.development_kind)
        self.assertIsNone(cleared.development_artifact)

        with self.assertRaisesRegex(DevelopmentExecutorError, "organ_result_invalid"):
            _parse_model_action(
                json.dumps({**base, "development_kind": "python-development-v1"})
            )

    def test_lineage_facts_expose_an_advanced_generation_without_resolution(self) -> None:
        status = self.runtime.status()
        facts = CurrentBodyLineageFacts(
            manifest_head=status.head,
            manifest_generation=2,
            manifest_parent_head="a" * 64,
            manifest_created_at="2026-09-12T00:00:00+00:00",
            manifest_activation_kind="surface-context-utf8-v1",
            manifest_activation_artifact="entrypoint.md",
            manifest_development_kind="python-development-v1",
            manifest_development_artifact="develop.py",
            head_advanced_event_ref="head-advanced-record-2",
            head_advanced_event_status="recorded",
            latest_recorded_resolution_action="no_recorded_resolution",
            latest_recorded_resolution_record_ref=None,
        )
        opportunity = DevelopmentOpportunity(
            id="opportunity-lineage",
            reason="task_end",
            root=status.root,
            current_body_ref=status.head,
            after_sequence=0,
            organ_argv=("codex",),
            working_dir=str(self.workspace),
            lineage_facts=facts,
        )

        projected = opportunity.to_body_mapping()

        self.assertEqual(
            projected["lineage_facts"]["current_manifest"]["generation"],
            2,
        )
        self.assertEqual(
            projected["lineage_facts"]["head_advanced"],
            {"event_ref": "head-advanced-record-2", "status": "recorded"},
        )
        self.assertEqual(
            projected["lineage_facts"]["current_manifest"]["development_kind"],
            "python-development-v1",
        )
        self.assertEqual(
            projected["lineage_facts"]["current_manifest"]["development_artifact"],
            "develop.py",
        )
        self.assertEqual(
            projected["lineage_facts"]["latest_recorded_resolution"],
            {"action": "no_recorded_resolution", "record_ref": None},
        )
        prompt = build_development_prompt(
            opportunity=BodyDevelopmentOpportunity.from_mapping(projected),
            activation_context="Body activation",
            body_files={"entrypoint.md": "Body activation"},
        )
        self.assertIn("head-advanced-record-2", prompt)
        self.assertIn("no_recorded_resolution", prompt)

    def test_optional_python_descriptor_prompt_exposes_the_real_context_contract(self) -> None:
        prompt = build_development_prompt(
            opportunity=BodyDevelopmentOpportunity.from_mapping(
                self._opportunity().to_body_mapping()
            ),
            activation_context="Body activation",
            body_files={"entrypoint.md": "Body activation"},
        )

        for required in (
            "OPTIONAL PYTHON DEVELOPMENT DESCRIPTOR CONTRACT",
            "def develop(context)",
            "JSON-serializable dict using the same action-object mapping",
            "context.opportunity and context.body_files are deep read-only mappings",
            "context.recall(*, before_sequence: int | None = None, limit: int = 12)",
            "{'experiences':[host-recorded same-Root events], 'has_more':bool}",
            "context.invoke_organ(prompt: str)",
            "exactly one of final_text or failure",
            "single absolute host deadline",
            "established LPAC Body boundary",
            "a filename or .py suffix never enables execution",
            '"development_kind":null,"development_artifact":null',
        ):
            with self.subTest(required=required):
                self.assertIn(required, prompt)

    def test_lineage_facts_do_not_treat_no_change_as_resolution(self) -> None:
        status = self.runtime.status()

        with self.assertRaisesRegex(
            DevelopmentExecutorError,
            "invalid_recorded_resolution_action",
        ):
            CurrentBodyLineageFacts(
                manifest_head=status.head,
                manifest_generation=0,
                manifest_parent_head=None,
                manifest_created_at="2026-09-12T00:00:00+00:00",
                manifest_activation_kind="surface-context-utf8-v1",
                manifest_activation_artifact="entrypoint.md",
                head_advanced_event_ref=None,
                head_advanced_event_status="no_recorded_head_advanced",
                latest_recorded_resolution_action="no_change",
                latest_recorded_resolution_record_ref=None,
            )

    def test_executor_rejects_lineage_facts_not_matching_current_manifest(self) -> None:
        opportunity = self._opportunity()
        manifest = self.runtime.body_store.read_manifest(opportunity.current_body_ref)
        facts = CurrentBodyLineageFacts(
            manifest_head=manifest.commitment,
            manifest_generation=manifest.generation + 1,
            manifest_parent_head=manifest.parent_head,
            manifest_created_at=manifest.created_at,
            manifest_activation_kind=manifest.activation_kind,
            manifest_activation_artifact=manifest.activation_artifact,
            head_advanced_event_ref=None,
            head_advanced_event_status="no_recorded_head_advanced",
            latest_recorded_resolution_action="no_recorded_resolution",
            latest_recorded_resolution_record_ref=None,
        )
        opportunity = DevelopmentOpportunity(
            id=opportunity.id,
            reason=opportunity.reason,
            root=opportunity.root,
            current_body_ref=opportunity.current_body_ref,
            after_sequence=opportunity.after_sequence,
            organ_argv=opportunity.organ_argv,
            working_dir=opportunity.working_dir,
            lineage_facts=facts,
        )

        with (
            patch("agentic_evo.body_process.BodyProcessSupervisor") as supervisor,
            self.assertRaisesRegex(
                DevelopmentExecutorError,
                "opportunity_lineage_facts_mismatch",
            ),
        ):
            DevelopmentExecutor(
                self.runtime,
                self.witness,
                organ_broker=_FakeBroker(),
            ).offer_development(opportunity)

        supervisor.assert_not_called()

    def test_private_worker_rejects_lineage_facts_not_matching_boot(self) -> None:
        status = self.runtime.status()
        facts = CurrentBodyLineageFacts(
            manifest_head=status.head,
            manifest_generation=1,
            manifest_parent_head=None,
            manifest_created_at="2026-09-12T00:00:00+00:00",
            manifest_activation_kind="surface-context-utf8-v1",
            manifest_activation_artifact="entrypoint.md",
            head_advanced_event_ref=None,
            head_advanced_event_status="no_recorded_head_advanced",
            latest_recorded_resolution_action="no_recorded_resolution",
            latest_recorded_resolution_record_ref=None,
        )
        opportunity = DevelopmentOpportunity(
            id="opportunity-boot-mismatch",
            reason="task_end",
            root=status.root,
            current_body_ref=status.head,
            after_sequence=0,
            organ_argv=("codex",),
            working_dir=str(self.workspace),
            lineage_facts=facts,
        )
        boot = BootEnvelope(
            protocol=BODY_BOOT_PROTOCOL,
            boot_session="boot-lineage",
            challenge="challenge-lineage",
            root=status.root,
            head=status.head,
            generation=0,
            activation_kind="surface-context-utf8-v1",
            activation_artifact="entrypoint.md",
            activation_digest="a" * 64,
            body_package={},
        )

        result = _run_development_offer(
            {
                "protocol": BODY_LINEAGE_PROTOCOL,
                "kind": "development_offer",
                "operation": "offer_development",
                "opportunity": opportunity.to_body_mapping(),
            },
            sequence=1,
            boot=boot,
            reader=io.BytesIO(),
            writer=io.BytesIO(),
        )

        self.assertEqual(result["action"], "failed")
        self.assertEqual(result["failure"], "opportunity_current_body_mismatch")

    def test_python_development_descriptor_uses_bound_ports_and_private_cas(
        self,
    ) -> None:
        status = self.runtime.status()
        opportunity = DevelopmentOpportunity(
            id="opportunity-python-development",
            reason="task_end",
            root=status.root,
            current_body_ref=status.head,
            after_sequence=0,
            organ_argv=("codex",),
            working_dir=str(self.workspace),
            lineage_facts=replace(
                self._lineage_facts(status.head),
                manifest_development_kind="python-development-v1",
                manifest_development_artifact="develop",
            ),
        )
        boot = BootEnvelope(
            protocol=BODY_BOOT_PROTOCOL,
            boot_session="boot-python-development",
            challenge="challenge-python-development",
            root=status.root,
            head=status.head,
            generation=0,
            activation_kind="surface-context-utf8-v1",
            activation_artifact="entrypoint.md",
            activation_digest="a" * 64,
            development_kind="python-development-v1",
            development_artifact="develop",
            body_package={},
        )
        source = """\
def develop(context):
    latest = context.recall(before_sequence=None, limit=1)
    first = context.invoke_organ("first bounded turn")
    earlier = context.recall(before_sequence=7, limit=1)
    second = context.invoke_organ("second bounded turn:" + first["final_text"])
    if latest["experiences"][0]["event_id"] != "event-latest":
        raise RuntimeError("latest recall was not bound")
    if earlier["experiences"][0]["event_id"] != "event-earlier":
        raise RuntimeError("earlier recall was not bound")
    if first["organ_call_ref"] != second["organ_call_ref"]:
        raise RuntimeError("organ thread changed")
    return {
        "action": "submit_successor",
        "files": {
            "entrypoint.md": "successor activation",
            "develop": "def develop(context):\\n    return {'action': 'no_change'}\\n",
        },
        "activation_kind": "surface-context-utf8-v1",
        "activation_artifact": "entrypoint.md",
        "causation_ref": "event-latest",
        "development_kind": "python-development-v1",
        "development_artifact": "develop",
    }
"""
        candidate = "b" * 64
        trace_one = {
            "before_sequence": None,
            "limit": 1,
            "returned_event_refs": ["broker-latest"],
            "has_more": True,
        }
        trace_two = {
            "before_sequence": 9,
            "limit": 1,
            "returned_event_refs": ["broker-earlier"],
            "has_more": False,
        }
        private_responses = b"".join(
            json.dumps(frame, separators=(",", ":")).encode("utf-8") + b"\n"
            for frame in (
                {
                    "protocol": BODY_LINEAGE_PROTOCOL,
                    "kind": "development_recall_result",
                    "operation": "recall_experiences",
                    "boot_session": boot.boot_session,
                    "root": boot.root,
                    "opportunity_id": opportunity.id,
                    "bound_head": boot.head,
                    "sequence": 1,
                    "before_sequence": None,
                    "limit": 1,
                    "experiences": [{"event_id": "event-latest"}],
                    "has_more": True,
                },
                {
                    "protocol": BODY_LINEAGE_PROTOCOL,
                    "kind": "organ_response",
                    "operation": "invoke_organ",
                    "boot_session": boot.boot_session,
                    "opportunity_id": opportunity.id,
                    "bound_head": boot.head,
                    "sequence": 1,
                    "final_text": "first raw organ text",
                    "organ_call_ref": "thread-1",
                    "recall_trace": [trace_one],
                },
                {
                    "protocol": BODY_LINEAGE_PROTOCOL,
                    "kind": "development_recall_result",
                    "operation": "recall_experiences",
                    "boot_session": boot.boot_session,
                    "root": boot.root,
                    "opportunity_id": opportunity.id,
                    "bound_head": boot.head,
                    "sequence": 1,
                    "before_sequence": 7,
                    "limit": 1,
                    "experiences": [{"event_id": "event-earlier"}],
                    "has_more": False,
                },
                {
                    "protocol": BODY_LINEAGE_PROTOCOL,
                    "kind": "organ_response",
                    "operation": "invoke_organ",
                    "boot_session": boot.boot_session,
                    "opportunity_id": opportunity.id,
                    "bound_head": boot.head,
                    "sequence": 1,
                    "final_text": "second raw organ text",
                    "organ_call_ref": "thread-1",
                    "recall_trace": [trace_one, trace_two],
                },
                {
                    "protocol": BODY_LINEAGE_PROTOCOL,
                    "kind": "lineage_response",
                    "boot_session": boot.boot_session,
                    "sequence": 1,
                    "operation": "prepare_successor",
                    "ok": True,
                    "candidate_head": candidate,
                },
                {
                    "protocol": BODY_LINEAGE_PROTOCOL,
                    "kind": "lineage_response",
                    "boot_session": boot.boot_session,
                    "sequence": 2,
                    "operation": "advance_head",
                    "ok": True,
                    "head": candidate,
                    "generation": 1,
                    "authority": "on",
                },
            )
        )
        writer = io.BytesIO()

        with patch(
            "agentic_evo.body_worker.body_text_files_from_package",
            return_value={"entrypoint.md": "Body", "develop": source},
        ):
            result = _run_development_offer(
                {
                    "protocol": BODY_LINEAGE_PROTOCOL,
                    "kind": "development_offer",
                    "operation": "offer_development",
                    "opportunity": opportunity.to_body_mapping(),
                },
                sequence=1,
                boot=boot,
                reader=io.BytesIO(private_responses),
                writer=writer,
            )

        self.assertEqual(result["action"], "candidate_submitted")
        self.assertEqual(result["candidate_head"], candidate)
        self.assertEqual(result["organ_call_ref"], "thread-1")
        self.assertEqual(result["recall_trace"], [trace_one, trace_two])
        requests = [json.loads(line) for line in writer.getvalue().splitlines()]
        self.assertEqual(
            [request["kind"] for request in requests],
            [
                "development_recall",
                "invoke_organ",
                "development_recall",
                "invoke_organ",
                "lineage_request",
                "lineage_request",
            ],
        )
        self.assertEqual(
            [request["before_sequence"] for request in requests[:3:2]],
            [None, 7],
        )
        self.assertEqual(
            [request["sequence"] for request in requests[:4]],
            [1, 1, 1, 1],
        )
        self.assertEqual(
            requests[3]["prompt"],
            "second bounded turn:first raw organ text",
        )
        self.assertEqual(
            [request["sequence"] for request in requests[4:]],
            [1, 2],
        )

    def test_broker_uses_bound_request_and_same_root_readonly_context(self) -> None:
        opportunity = self._opportunity()
        self.runtime.wake(
            execution_surface="agentic-evo-body",
            session_id=opportunity.id,
            project_environment=opportunity.working_dir,
        )
        broker = WitnessOrganBroker(
            runtime=self.runtime,
            opportunity_id=opportunity.id,
            root=opportunity.root,
            current_body_ref=opportunity.current_body_ref,
            organ_argv=opportunity.organ_argv,
            working_dir=opportunity.working_dir,
            timeout_seconds=30.0,
        )
        request = OrganInvocationRequest(
            boot_session="boot-1",
            opportunity_id=opportunity.id,
            bound_head=opportunity.current_body_ref,
            sequence=1,
            prompt="Current Body decides its own next action.",
        )
        observed: dict[str, object] = {}

        def invoke_host(*, request, wrapped_prompt, active):
            observed["request"] = request
            observed["prompt"] = wrapped_prompt
            return OrganResponse(final_text='{"action":"no_change"}')

        try:
            with patch.object(broker, "_invoke_host_organ", side_effect=invoke_host):
                response = broker.invoke(request, threading.Event())
        finally:
            self.runtime.sleep(
                execution_surface="agentic-evo-body",
                session_id=opportunity.id,
            )

        self.assertEqual(response.final_text, '{"action":"no_change"}')
        self.assertIs(observed["request"], request)
        self.assertIn("SAME-ROOT READ-ONLY EXPERIENCES", str(observed["prompt"]))
        self.assertNotIn("SurfaceClient", str(observed["prompt"]))
        self.assertEqual(
            set(request.to_mapping()),
            {"boot_session", "opportunity_id", "bound_head", "sequence", "prompt"},
        )

    def test_broker_reuses_one_call_ref_and_deadline_for_serial_invocations(
        self,
    ) -> None:
        opportunity = self._opportunity()
        self.runtime.wake(
            execution_surface="agentic-evo-body",
            session_id=opportunity.id,
            project_environment=opportunity.working_dir,
        )
        deadline = monotonic() + 30.0
        broker = WitnessOrganBroker(
            runtime=self.runtime,
            opportunity_id=opportunity.id,
            root=opportunity.root,
            current_body_ref=opportunity.current_body_ref,
            organ_argv=opportunity.organ_argv,
            working_dir=opportunity.working_dir,
            timeout_seconds=30.0,
            deadline=deadline,
        )
        first_request = OrganInvocationRequest(
            boot_session="boot-1",
            opportunity_id=opportunity.id,
            bound_head=opportunity.current_body_ref,
            sequence=1,
            prompt="first bounded turn",
        )
        second_request = OrganInvocationRequest(
            boot_session="boot-1",
            opportunity_id=opportunity.id,
            bound_head=opportunity.current_body_ref,
            sequence=1,
            prompt="second bounded turn",
        )
        trace = RecallTrace(
            before_sequence=None,
            limit=1,
            returned_event_refs=("initial-page",),
            has_more=False,
        )
        calls: list[dict[str, object]] = []
        cancel_event = threading.Event()

        def invoke_host(**kwargs):
            calls.append(kwargs)
            return OrganResponse(
                final_text='{"action":"no_change"}',
                organ_call_ref="thread-1",
            )

        try:
            with (
                patch.object(
                    broker,
                    "_read_recall_page",
                    return_value=([], False, trace),
                ),
                patch.object(
                    broker,
                    "_invoke_host_organ",
                    side_effect=invoke_host,
                ),
            ):
                first = broker.invoke(first_request, cancel_event)
                second = broker.invoke(second_request, cancel_event)
        finally:
            self.runtime.sleep(
                execution_surface="agentic-evo-body",
                session_id=opportunity.id,
            )

        self.assertEqual(first.organ_call_ref, "thread-1")
        self.assertEqual(second.organ_call_ref, "thread-1")
        self.assertEqual(first.recall_trace, (trace,))
        self.assertEqual(second.recall_trace, (trace,))
        self.assertEqual(
            [call.get("resume_thread_id") for call in calls],
            [None, "thread-1"],
        )
        self.assertIs(calls[0]["active"], calls[1]["active"])
        self.assertEqual(calls[0]["active"].deadline, deadline)
        self.assertEqual(calls[1]["active"].deadline, deadline)

    def test_broker_rejects_a_result_that_arrives_after_revocation(self) -> None:
        opportunity = self._opportunity()
        self.runtime.wake(
            execution_surface="agentic-evo-body",
            session_id=opportunity.id,
            project_environment=opportunity.working_dir,
        )
        broker = WitnessOrganBroker(
            runtime=self.runtime,
            opportunity_id=opportunity.id,
            root=opportunity.root,
            current_body_ref=opportunity.current_body_ref,
            organ_argv=opportunity.organ_argv,
            working_dir=opportunity.working_dir,
            timeout_seconds=30.0,
        )
        request = OrganInvocationRequest(
            boot_session="boot-1",
            opportunity_id=opportunity.id,
            bound_head=opportunity.current_body_ref,
            sequence=1,
            prompt="Return no_change.",
        )

        def late_result(*, request, wrapped_prompt, active):
            broker.cancel(request.opportunity_id)
            return OrganResponse(final_text='{"action":"no_change"}')

        try:
            with patch.object(broker, "_invoke_host_organ", side_effect=late_result):
                response = broker.invoke(request, threading.Event())
        finally:
            self.runtime.sleep(
                execution_surface="agentic-evo-body",
                session_id=opportunity.id,
            )

        self.assertEqual(response.failure, "organ_result_revoked")

    def test_broker_allows_body_selected_pages_without_a_fixed_total(self) -> None:
        """The resource bound is wire bytes/deadline, not a learning page count."""

        opportunity = self._opportunity()
        broker = WitnessOrganBroker(
            runtime=self.runtime,
            opportunity_id=opportunity.id,
            root=opportunity.root,
            current_body_ref=opportunity.current_body_ref,
            organ_argv=opportunity.organ_argv,
            working_dir=opportunity.working_dir,
            timeout_seconds=30.0,
        )
        request = OrganInvocationRequest(
            boot_session="boot-1",
            opportunity_id=opportunity.id,
            bound_head=opportunity.current_body_ref,
            sequence=1,
            prompt="Use only the provided transport.",
        )
        active = _ActiveOrganRun(
            cancel_event=threading.Event(),
            deadline=monotonic() + 30.0,
            recall_trace=[
                RecallTrace(
                    before_sequence=None,
                    limit=1,
                    returned_event_refs=(),
                    has_more=True,
                )
            ],
        )
        page_count = 12
        pages = iter(
            (
                [],
                True,
                RecallTrace(
                    before_sequence=index,
                    limit=1,
                    returned_event_refs=(),
                    has_more=True,
                ),
            )
            for index in range(1, page_count + 1)
        )
        turns = [
            OrganResponse(
                final_text=(
                    '{"transport":"recall","before_sequence":1,"limit":1}'
                ),
                organ_call_ref="thread-1",
            )
        ]
        turns.extend(
            OrganResponse(
                final_text=(
                    '{"transport":"recall","before_sequence":'
                    f"{index},\"limit\":1}}"
                ),
                organ_call_ref="thread-1",
            )
            for index in range(2, page_count + 1)
        )
        turns.append(
            OrganResponse(
                final_text='{"action":"no_change"}',
                organ_call_ref="thread-1",
            )
        )

        with (
            patch.object(broker, "_read_recall_page", side_effect=pages),
            patch.object(broker, "_run_host_turn", side_effect=turns),
        ):
            response = broker._invoke_host_organ(
                request=request,
                wrapped_prompt="host context",
                active=active,
            )

        self.assertEqual(response.final_text, '{"action":"no_change"}')
        self.assertEqual(len(response.recall_trace), page_count + 1)

    def test_broker_releases_each_completed_turn_fence(self) -> None:
        opportunity = self._opportunity()
        broker = WitnessOrganBroker(
            runtime=self.runtime,
            opportunity_id=opportunity.id,
            root=opportunity.root,
            current_body_ref=opportunity.current_body_ref,
            organ_argv=opportunity.organ_argv,
            working_dir=opportunity.working_dir,
            timeout_seconds=30.0,
        )
        request = OrganInvocationRequest(
            boot_session="boot-1",
            opportunity_id=opportunity.id,
            bound_head=opportunity.current_body_ref,
            sequence=1,
            prompt="Return no_change.",
        )
        active = _ActiveOrganRun(
            cancel_event=threading.Event(),
            deadline=monotonic() + 30.0,
        )
        process = MagicMock()
        process.stdin = MagicMock()
        fence = MagicMock()

        with (
            patch.object(broker, "_start_process", return_value=(process, fence)),
            patch.object(
                broker,
                "_collect_result",
                return_value=OrganResponse(
                    final_text='{"action":"no_change"}',
                    organ_call_ref="thread-1",
                ),
            ),
        ):
            response = broker._run_host_turn(
                request=request,
                wrapped_prompt="host context",
                active=active,
                resume_thread_id=None,
            )

        self.assertEqual(response.organ_call_ref, "thread-1")
        fence.close.assert_called_once_with()
        self.assertIsNone(active.process_fence)
        self.assertIsNone(active.process)
        self.assertFalse(active.cancel_event.is_set())

    def test_broker_command_uses_fixed_read_only_resume_constraints(self) -> None:
        executable, args = _read_only_organ_args(self._opportunity().organ_argv)

        initial = _organ_command(
            executable=executable,
            args=args,
            resume_thread_id=None,
        )
        resumed = _organ_command(
            executable=executable,
            args=args,
            resume_thread_id="thread-1",
        )

        self.assertEqual(
            initial[:5],
            ["codex", "exec", "--sandbox", "read-only", "--json"],
        )
        self.assertIn("--ignore-user-config", initial)
        self.assertEqual(
            resumed[:6],
            [
                "codex",
                "exec",
                "--sandbox",
                "read-only",
                "resume",
                "--json",
            ],
        )
        self.assertEqual(resumed[-2:], ["thread-1", "-"])
        self.assertNotIn("--last", resumed)

    def test_broker_recall_transport_accepts_a_latest_page_request(self) -> None:
        recall = _parse_recall_transport(
            '{"transport":"recall","before_sequence":null,"limit":1}'
        )

        self.assertIsNotNone(recall)
        assert recall is not None
        self.assertIsNone(recall.before_sequence)
        self.assertEqual(recall.limit, 1)

    def test_private_result_cannot_override_host_call_metadata(self) -> None:
        opportunity = self._opportunity()
        trace = RecallTrace(
            before_sequence=None,
            limit=1,
            returned_event_refs=(),
            has_more=False,
        )
        frame = {
            "protocol": BODY_LINEAGE_PROTOCOL,
            "kind": "development_result",
            "operation": "offer_development",
            "sequence": 1,
            "action": "no_change",
            "opportunity_id": opportunity.id,
            "current_body_ref": opportunity.current_body_ref,
            "organ_call_ref": "forged-thread",
            "recall_trace": [trace.to_mapping()],
        }
        host_response = OrganResponse(
            final_text='{"action":"no_change"}',
            organ_call_ref="host-thread",
            recall_trace=(trace,),
        )

        with self.assertRaises(BodyBootError):
            _development_result_from_frame(
                frame,
                opportunity=opportunity,
                organ_response=host_response,
                organ_result_invalidated=False,
            )

        frame["organ_call_ref"] = "host-thread"
        result = _development_result_from_frame(
            frame,
            opportunity=opportunity,
            organ_response=host_response,
            organ_result_invalidated=False,
        )

        self.assertEqual(result.organ_call_ref, "host-thread")
        self.assertEqual(result.recall_trace, (trace,))

    @unittest.skipUnless(sys.platform == "win32", "Windows Job path")
    def test_broker_kills_a_host_process_if_job_assignment_fails(self) -> None:
        opportunity = self._opportunity()
        broker = WitnessOrganBroker(
            runtime=self.runtime,
            opportunity_id=opportunity.id,
            root=opportunity.root,
            current_body_ref=opportunity.current_body_ref,
            organ_argv=opportunity.organ_argv,
            working_dir=opportunity.working_dir,
            timeout_seconds=30.0,
        )
        process = MagicMock()
        process.poll.return_value = None
        process._handle = 1
        job = MagicMock()
        job.assign_handle.side_effect = OSError("cannot assign host process")

        with (
            patch("agentic_evo.organ_broker.subprocess.Popen", return_value=process),
            patch("agentic_evo.windows_native.KillOnCloseJob", return_value=job),
            self.assertRaises(OSError),
        ):
            broker._start_process(executable="codex", args=("--sandbox", "read-only"))

        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=1.0)
        job.close.assert_called_once_with()

    def test_executor_injects_broker_and_closes_temporary_session(self) -> None:
        opportunity = self._opportunity()
        broker = _FakeBroker()
        calls: dict[str, object] = {}

        class FakeBody:
            def offer_development(self, *, opportunity):
                calls["opportunity"] = opportunity
                return BodyActionResult(
                    action="no_change",
                    opportunity_id=opportunity.id,
                    current_body_ref=opportunity.current_body_ref,
                )

            def close(self) -> None:
                calls["closed"] = True

        with patch("agentic_evo.body_process.BodyProcessSupervisor") as supervisor:
            supervisor.return_value.spawn_current.return_value = FakeBody()
            result = DevelopmentExecutor(
                self.runtime,
                self.witness,
                development_timeout_seconds=300.0,
                organ_broker=broker,
            ).offer_development(opportunity)

        self.assertEqual(result.action, "no_change")
        self.assertIs(calls["opportunity"], opportunity)
        self.assertTrue(calls["closed"])
        self.assertIs(supervisor.call_args.kwargs["organ_broker"], broker)
        self.assertEqual(self.runtime.status().active_sessions, ())

    def test_executor_injects_a_bound_direct_recall_provider(self) -> None:
        opportunity = self._opportunity()
        broker = _FakeBroker()
        calls: dict[str, object] = {}

        class FakeBody:
            def offer_development(self, *, opportunity):
                provider = supervisor.call_args.kwargs["development_recall"]
                calls["recall"] = provider(
                    opportunity=opportunity,
                    before_sequence=7,
                    limit=2,
                    cancel_event=threading.Event(),
                )
                calls["deadline"] = supervisor.call_args.kwargs[
                    "development_deadline"
                ]
                return BodyActionResult(
                    action="no_change",
                    opportunity_id=opportunity.id,
                    current_body_ref=opportunity.current_body_ref,
                )

            def close(self) -> None:
                return None

        with (
            patch("agentic_evo.body_process.BodyProcessSupervisor") as supervisor,
            patch.object(
                self.runtime,
                "recall_experiences",
                return_value=([{"event_id": "same-root-event"}], False),
            ) as recall,
        ):
            supervisor.return_value.spawn_current.return_value = FakeBody()
            result = DevelopmentExecutor(
                self.runtime,
                self.witness,
                development_timeout_seconds=30.0,
                organ_broker=broker,
            ).offer_development(opportunity)

        self.assertEqual(result.action, "no_change")
        self.assertEqual(calls["recall"], ([{"event_id": "same-root-event"}], False))
        self.assertIsInstance(calls["deadline"], float)
        recall.assert_called_once_with(
            execution_surface="agentic-evo-body",
            session_id=opportunity.id,
            limit=2,
            before_sequence=7,
        )

    def test_cancel_revokes_active_broker_and_closes_body(self) -> None:
        opportunity = self._opportunity()
        broker = _FakeBroker()
        started = threading.Event()
        closed = threading.Event()

        class BlockingBody:
            def offer_development(self, *, opportunity):
                started.set()
                if not closed.wait(2.0):
                    raise AssertionError("cancel did not close the active Body")
                raise DevelopmentExecutorError("development_cancelled")

            def close(self) -> None:
                closed.set()

        executor = DevelopmentExecutor(self.runtime, self.witness, organ_broker=broker)
        outcome: dict[str, BaseException] = {}

        def offer() -> None:
            try:
                executor.offer_development(opportunity)
            except BaseException as error:
                outcome["error"] = error

        with patch("agentic_evo.body_process.BodyProcessSupervisor") as supervisor:
            supervisor.return_value.spawn_current.return_value = BlockingBody()
            thread = threading.Thread(target=offer)
            thread.start()
            self.assertTrue(started.wait(1.0))
            executor.cancel()
            thread.join(2.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(broker.cancelled, [opportunity.id])
        self.assertIsInstance(outcome.get("error"), DevelopmentExecutorError)
        self.assertEqual(str(outcome["error"]), "development_cancelled")

    def test_cancel_before_offer_prevents_body_and_session_start(self) -> None:
        opportunity = self._opportunity()
        executor = DevelopmentExecutor(self.runtime, self.witness, organ_broker=_FakeBroker())
        executor.cancel()

        with (
            patch("agentic_evo.body_process.BodyProcessSupervisor") as supervisor,
            self.assertRaisesRegex(DevelopmentExecutorError, "development_cancelled"),
        ):
            executor.offer_development(opportunity)

        supervisor.assert_not_called()
        self.assertEqual(self.runtime.status().active_sessions, ())

    def test_primary_temporary_session_sleep_failure_is_visible(self) -> None:
        opportunity = self._opportunity()

        class FakeBody:
            def offer_development(self, *, opportunity):
                return BodyActionResult(
                    action="no_change",
                    opportunity_id=opportunity.id,
                    current_body_ref=opportunity.current_body_ref,
                )

            def close(self) -> None:
                return None

        with (
            patch("agentic_evo.body_process.BodyProcessSupervisor") as supervisor,
            patch.object(self.runtime, "sleep", side_effect=RuntimeError("sleep failed")),
            self.assertRaisesRegex(
                DevelopmentExecutorError,
                "temporary_body_session_sleep_failed",
            ),
        ):
            supervisor.return_value.spawn_current.return_value = FakeBody()
            DevelopmentExecutor(
                self.runtime,
                self.witness,
                organ_broker=_FakeBroker(),
            ).offer_development(opportunity)

    def test_private_worker_requests_broker_then_performs_private_cas(self) -> None:
        opportunity = self._opportunity()
        boot = BootEnvelope(
            protocol=BODY_BOOT_PROTOCOL,
            boot_session="boot-1",
            challenge="challenge-1",
            root=opportunity.root,
            head=opportunity.current_body_ref,
            generation=0,
            activation_kind="surface-context-utf8-v1",
            activation_artifact="entrypoint.md",
            activation_digest="a" * 64,
            body_package={},
        )
        candidate = "b" * 64
        organ_text = json.dumps(
            {
                "action": "submit_successor",
                "files": {
                    "entrypoint.md": "Self-authored successor",
                    "develop.py": "def develop(context):\n    return {'action': 'no_change'}\n",
                },
                "activation_kind": "surface-context-utf8-v1",
                "activation_artifact": "entrypoint.md",
                "causation_ref": "experience-1",
                "development_kind": "python-development-v1",
                "development_artifact": "develop.py",
            },
            separators=(",", ":"),
        )
        private_responses = b"".join(
            json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
            for value in (
                {
                    "protocol": BODY_LINEAGE_PROTOCOL,
                    "kind": "organ_response",
                    "operation": "invoke_organ",
                    "boot_session": boot.boot_session,
                    "opportunity_id": opportunity.id,
                    "bound_head": boot.head,
                    "sequence": 1,
                    "final_text": organ_text,
                    "organ_call_ref": "thread-1",
                    "recall_trace": [
                        {
                            "before_sequence": None,
                            "limit": 12,
                            "returned_event_refs": [],
                            "has_more": False,
                        }
                    ],
                },
                {
                    "protocol": BODY_LINEAGE_PROTOCOL,
                    "kind": "lineage_response",
                    "boot_session": boot.boot_session,
                    "sequence": 1,
                    "operation": "prepare_successor",
                    "ok": True,
                    "candidate_head": candidate,
                },
                {
                    "protocol": BODY_LINEAGE_PROTOCOL,
                    "kind": "lineage_response",
                    "boot_session": boot.boot_session,
                    "sequence": 2,
                    "operation": "advance_head",
                    "ok": True,
                    "head": candidate,
                    "generation": 1,
                    "authority": "on",
                },
            )
        )
        writer = io.BytesIO()
        command = {
            "protocol": BODY_LINEAGE_PROTOCOL,
            "kind": "development_offer",
            "operation": "offer_development",
            "opportunity": opportunity.to_body_mapping(),
        }

        with patch(
            "agentic_evo.body_worker.body_text_files_from_package",
            return_value={"entrypoint.md": "Body"},
        ):
            result = _run_development_offer(
                command,
                sequence=1,
                boot=boot,
                reader=io.BytesIO(private_responses),
                writer=writer,
            )

        self.assertEqual(result["action"], "candidate_submitted")
        self.assertEqual(result["organ_call_ref"], "thread-1")
        self.assertEqual(
            BodyActionResult.from_mapping(
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"protocol", "kind", "operation", "sequence"}
                }
            ).organ_call_ref,
            "thread-1",
        )
        requests = [json.loads(line) for line in writer.getvalue().splitlines()]
        self.assertEqual(
            [request["kind"] for request in requests],
            ["invoke_organ", "lineage_request", "lineage_request"],
        )
        self.assertEqual(
            set(requests[0]),
            {
                "protocol",
                "kind",
                "operation",
                "boot_session",
                "opportunity_id",
                "bound_head",
                "sequence",
                "prompt",
            },
        )
        self.assertNotIn("organ_argv", requests[0])
        self.assertNotIn("working_dir", requests[0])
        self.assertEqual(
            [request["sequence"] for request in requests[1:]],
            [1, 2],
        )
        self.assertEqual(requests[1]["development_kind"], "python-development-v1")
        self.assertEqual(requests[1]["development_artifact"], "develop.py")


if __name__ == "__main__":
    unittest.main()
