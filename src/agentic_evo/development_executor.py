from __future__ import annotations

"""Bounded, Body-owned development opportunities.

This module owns only the transport boundary between a Current Body and an
already-authorized model organ.  It deliberately does not choose what the Body
should remember, change, retain, withdraw, or request next.
"""

import base64
from dataclasses import dataclass
from datetime import datetime
import json
import sys
import threading
from time import monotonic
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from .body import DEVELOPMENT_DESCRIPTOR_UNSET
from .organ_broker import RecallTrace


DEVELOPMENT_PROTOCOL = "agentic-evo-body-development-v1"
DEVELOPMENT_REASONS = frozenset({"task_end", "body_due", "bootstrap"})
HEAD_ADVANCED_EVENT_STATES = frozenset(
    {"recorded", "no_recorded_head_advanced"}
)
RECORDED_RESOLUTION_ACTIONS = frozenset(
    {"retain", "withdraw", "no_recorded_resolution"}
)
BODY_ACTIONS = frozenset(
    {
        "no_change",
        "candidate_submitted",
        "retain",
        "withdraw",
        "request_later",
        "failed",
    }
)
_MODEL_ACTIONS = frozenset(
    {"no_change", "submit_successor", "retain", "withdraw", "request_later"}
)
_MAX_OPPORTUNITY_TEXT_BYTES = 4 * 1024
_MAX_ORGAN_ARG_BYTES = 16 * 1024
_MAX_BODY_PROMPT_BYTES = 512 * 1024
_MAX_MODEL_ACTION_BYTES = 128 * 1024
_MAX_EVIDENCE_REFS = 64


class DevelopmentExecutorError(RuntimeError):
    """A visible, recoverable failure at the Body development boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _required_text(value: object, field: str, *, maximum: int = _MAX_OPPORTUNITY_TEXT_BYTES) -> str:
    if not isinstance(value, str) or not value:
        raise DevelopmentExecutorError(f"invalid_{field}")
    if len(value.encode("utf-8")) > maximum:
        raise DevelopmentExecutorError(f"oversize_{field}")
    return value


@dataclass(frozen=True)
class CurrentBodyLineageFacts:
    """Raw, host-recorded facts about the exact Head offered to a Body."""

    manifest_head: str
    manifest_generation: int
    manifest_parent_head: str | None
    manifest_created_at: str
    manifest_activation_kind: str | None
    manifest_activation_artifact: str | None
    head_advanced_event_ref: str | None
    head_advanced_event_status: str
    latest_recorded_resolution_action: str
    latest_recorded_resolution_record_ref: str | None
    manifest_development_kind: str | None = None
    manifest_development_artifact: str | None = None

    def __post_init__(self) -> None:
        _required_text(self.manifest_head, "manifest_head")
        if (
            not isinstance(self.manifest_generation, int)
            or isinstance(self.manifest_generation, bool)
            or self.manifest_generation < 0
        ):
            raise DevelopmentExecutorError("invalid_manifest_generation")
        if self.manifest_parent_head is not None:
            _required_text(self.manifest_parent_head, "manifest_parent_head")
        _required_text(self.manifest_created_at, "manifest_created_at")
        for value, field in (
            (self.manifest_activation_kind, "manifest_activation_kind"),
            (self.manifest_activation_artifact, "manifest_activation_artifact"),
        ):
            if value is not None:
                _required_text(value, field)
        if (self.manifest_activation_kind is None) != (
            self.manifest_activation_artifact is None
        ):
            raise DevelopmentExecutorError("incomplete_manifest_activation")
        for value, field in (
            (self.manifest_development_kind, "manifest_development_kind"),
            (self.manifest_development_artifact, "manifest_development_artifact"),
        ):
            if value is not None:
                _required_text(value, field)
        if (self.manifest_development_kind is None) != (
            self.manifest_development_artifact is None
        ):
            raise DevelopmentExecutorError("incomplete_manifest_development")
        if self.head_advanced_event_status not in HEAD_ADVANCED_EVENT_STATES:
            raise DevelopmentExecutorError("invalid_head_advanced_event_status")
        if self.head_advanced_event_status == "recorded":
            _required_text(self.head_advanced_event_ref, "head_advanced_event_ref")
        elif self.head_advanced_event_ref is not None:
            raise DevelopmentExecutorError("head_advanced_event_missing_conflict")
        if self.latest_recorded_resolution_action not in RECORDED_RESOLUTION_ACTIONS:
            raise DevelopmentExecutorError("invalid_recorded_resolution_action")
        if self.latest_recorded_resolution_action == "no_recorded_resolution":
            if self.latest_recorded_resolution_record_ref is not None:
                raise DevelopmentExecutorError("recorded_resolution_missing_conflict")
        else:
            _required_text(
                self.latest_recorded_resolution_record_ref,
                "recorded_resolution_record_ref",
            )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "CurrentBodyLineageFacts":
        if set(value) != {
            "current_manifest",
            "head_advanced",
            "latest_recorded_resolution",
        }:
            raise DevelopmentExecutorError("invalid_lineage_facts_fields")
        manifest = value.get("current_manifest")
        advanced = value.get("head_advanced")
        resolution = value.get("latest_recorded_resolution")
        if (
            not isinstance(manifest, Mapping)
            or set(manifest)
            != {
                "head",
                "generation",
                "parent_head",
                "created_at",
                "activation_kind",
                "activation_artifact",
                "development_kind",
                "development_artifact",
            }
            or not isinstance(advanced, Mapping)
            or set(advanced) != {"event_ref", "status"}
            or not isinstance(resolution, Mapping)
            or set(resolution) != {"action", "record_ref"}
        ):
            raise DevelopmentExecutorError("invalid_lineage_facts_fields")
        return cls(
            manifest_head=manifest["head"],
            manifest_generation=manifest["generation"],
            manifest_parent_head=manifest["parent_head"],
            manifest_created_at=manifest["created_at"],
            manifest_activation_kind=manifest["activation_kind"],
            manifest_activation_artifact=manifest["activation_artifact"],
            manifest_development_kind=manifest["development_kind"],
            manifest_development_artifact=manifest["development_artifact"],
            head_advanced_event_ref=advanced["event_ref"],
            head_advanced_event_status=advanced["status"],
            latest_recorded_resolution_action=resolution["action"],
            latest_recorded_resolution_record_ref=resolution["record_ref"],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "current_manifest": {
                "head": self.manifest_head,
                "generation": self.manifest_generation,
                "parent_head": self.manifest_parent_head,
                "created_at": self.manifest_created_at,
                "activation_kind": self.manifest_activation_kind,
                "activation_artifact": self.manifest_activation_artifact,
                "development_kind": self.manifest_development_kind,
                "development_artifact": self.manifest_development_artifact,
            },
            "head_advanced": {
                "event_ref": self.head_advanced_event_ref,
                "status": self.head_advanced_event_status,
            },
            "latest_recorded_resolution": {
                "action": self.latest_recorded_resolution_action,
                "record_ref": self.latest_recorded_resolution_record_ref,
            },
        }

    def matches_current_manifest(self, manifest: "BodyManifest") -> bool:
        return (
            self.manifest_head == manifest.commitment
            and self.manifest_generation == manifest.generation
            and self.manifest_parent_head == manifest.parent_head
            and self.manifest_created_at == manifest.created_at
            and self.manifest_activation_kind == manifest.activation_kind
            and self.manifest_activation_artifact == manifest.activation_artifact
            and self.manifest_development_kind == manifest.development_kind
            and self.manifest_development_artifact == manifest.development_artifact
        )

    def matches_boot(
        self,
        *,
        head: str,
        generation: int,
        activation_kind: str,
        activation_artifact: str,
        development_kind: str | None,
        development_artifact: str | None,
    ) -> bool:
        return (
            self.manifest_head == head
            and self.manifest_generation == generation
            and self.manifest_activation_kind == activation_kind
            and self.manifest_activation_artifact == activation_artifact
            and self.manifest_development_kind == development_kind
            and self.manifest_development_artifact == development_artifact
        )


@dataclass(frozen=True)
class DevelopmentOpportunity:
    """One Scheduler-issued chance for the Current Body to act or do nothing."""

    id: str
    reason: str
    root: str
    current_body_ref: str
    after_sequence: int
    organ_argv: tuple[str, ...]
    working_dir: str
    lineage_facts: CurrentBodyLineageFacts

    def __post_init__(self) -> None:
        _required_text(self.id, "opportunity_id")
        if self.reason not in DEVELOPMENT_REASONS:
            raise DevelopmentExecutorError("invalid_opportunity_reason")
        _required_text(self.root, "root")
        _required_text(self.current_body_ref, "current_body_ref")
        if (
            not isinstance(self.after_sequence, int)
            or isinstance(self.after_sequence, bool)
            or self.after_sequence < 0
        ):
            raise DevelopmentExecutorError("invalid_after_sequence")
        if not isinstance(self.organ_argv, tuple) or not self.organ_argv:
            raise DevelopmentExecutorError("invalid_organ_argv")
        total = 0
        for value in self.organ_argv:
            total += len(_required_text(value, "organ_argv").encode("utf-8"))
        if total > _MAX_ORGAN_ARG_BYTES:
            raise DevelopmentExecutorError("oversize_organ_argv")
        _required_text(self.working_dir, "working_dir")
        if not isinstance(self.lineage_facts, CurrentBodyLineageFacts):
            raise DevelopmentExecutorError("invalid_lineage_facts")
        if self.lineage_facts.manifest_head != self.current_body_ref:
            raise DevelopmentExecutorError("lineage_facts_head_mismatch")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DevelopmentOpportunity":
        expected = {
            "id",
            "reason",
            "root",
            "current_body_ref",
            "after_sequence",
            "organ_argv",
            "working_dir",
            "lineage_facts",
        }
        if set(value) != expected:
            raise DevelopmentExecutorError("invalid_opportunity_fields")
        argv = value["organ_argv"]
        if not isinstance(argv, list):
            raise DevelopmentExecutorError("invalid_organ_argv")
        return cls(
            id=value["id"],
            reason=value["reason"],
            root=value["root"],
            current_body_ref=value["current_body_ref"],
            after_sequence=value["after_sequence"],
            organ_argv=tuple(argv),
            working_dir=value["working_dir"],
            lineage_facts=CurrentBodyLineageFacts.from_mapping(
                value["lineage_facts"]
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reason": self.reason,
            "root": self.root,
            "current_body_ref": self.current_body_ref,
            "after_sequence": self.after_sequence,
            "organ_argv": list(self.organ_argv),
            "working_dir": self.working_dir,
            "lineage_facts": self.lineage_facts.to_mapping(),
        }

    def to_body_mapping(self) -> dict[str, Any]:
        """Project only scheduler identity into the Low-IL private channel."""

        return BodyDevelopmentOpportunity(
            id=self.id,
            reason=self.reason,
            root=self.root,
            current_body_ref=self.current_body_ref,
            after_sequence=self.after_sequence,
            lineage_facts=self.lineage_facts,
        ).to_mapping()


@dataclass(frozen=True)
class BodyDevelopmentOpportunity:
    """The portion of an opportunity a restricted Body may learn about."""

    id: str
    reason: str
    root: str
    current_body_ref: str
    after_sequence: int
    lineage_facts: CurrentBodyLineageFacts

    def __post_init__(self) -> None:
        _required_text(self.id, "opportunity_id")
        if self.reason not in DEVELOPMENT_REASONS:
            raise DevelopmentExecutorError("invalid_opportunity_reason")
        _required_text(self.root, "root")
        _required_text(self.current_body_ref, "current_body_ref")
        if (
            not isinstance(self.after_sequence, int)
            or isinstance(self.after_sequence, bool)
            or self.after_sequence < 0
        ):
            raise DevelopmentExecutorError("invalid_after_sequence")
        if not isinstance(self.lineage_facts, CurrentBodyLineageFacts):
            raise DevelopmentExecutorError("invalid_lineage_facts")
        if self.lineage_facts.manifest_head != self.current_body_ref:
            raise DevelopmentExecutorError("lineage_facts_head_mismatch")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BodyDevelopmentOpportunity":
        expected = {
            "id",
            "reason",
            "root",
            "current_body_ref",
            "after_sequence",
            "lineage_facts",
        }
        if set(value) != expected:
            raise DevelopmentExecutorError("invalid_body_opportunity_fields")
        return cls(
            id=value["id"],
            reason=value["reason"],
            root=value["root"],
            current_body_ref=value["current_body_ref"],
            after_sequence=value["after_sequence"],
            lineage_facts=CurrentBodyLineageFacts.from_mapping(
                value["lineage_facts"]
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reason": self.reason,
            "root": self.root,
            "current_body_ref": self.current_body_ref,
            "after_sequence": self.after_sequence,
            "lineage_facts": self.lineage_facts.to_mapping(),
        }


@dataclass(frozen=True)
class BodyActionResult:
    """A statement of what the Body did, without an external score or verdict."""

    action: str
    opportunity_id: str
    current_body_ref: str
    candidate_head: str | None = None
    generation: int | None = None
    not_before: str | None = None
    reason: str | None = None
    evidence_refs: tuple[str, ...] = ()
    organ_call_ref: str | None = None
    recall_trace: tuple[RecallTrace, ...] = ()
    failure: str | None = None

    def __post_init__(self) -> None:
        if self.action not in BODY_ACTIONS:
            raise DevelopmentExecutorError("invalid_body_action")
        _required_text(self.opportunity_id, "opportunity_id")
        _required_text(self.current_body_ref, "current_body_ref")
        if self.candidate_head is not None:
            _required_text(self.candidate_head, "candidate_head")
        if self.generation is not None and (
            not isinstance(self.generation, int)
            or isinstance(self.generation, bool)
            or self.generation < 0
        ):
            raise DevelopmentExecutorError("invalid_generation")
        if len(self.evidence_refs) > _MAX_EVIDENCE_REFS:
            raise DevelopmentExecutorError("oversize_evidence_refs")
        for ref in self.evidence_refs:
            _required_text(ref, "evidence_ref")
        if self.organ_call_ref is not None:
            _required_text(self.organ_call_ref, "organ_call_ref")
        if not isinstance(self.recall_trace, tuple) or any(
            not isinstance(trace, RecallTrace) for trace in self.recall_trace
        ):
            raise DevelopmentExecutorError("invalid_recall_trace")

        if self.action == "candidate_submitted":
            if self.candidate_head is None or self.generation is None:
                raise DevelopmentExecutorError("candidate_submission_incomplete")
            if any(
                value is not None
                for value in (self.not_before, self.reason, self.failure)
            ) or self.evidence_refs:
                raise DevelopmentExecutorError("candidate_submission_fields")
        elif self.action in {"retain", "withdraw"}:
            if self.candidate_head is None or not self.evidence_refs:
                raise DevelopmentExecutorError("candidate_resolution_incomplete")
            if any(
                value is not None
                for value in (self.generation, self.not_before, self.reason, self.failure)
            ):
                raise DevelopmentExecutorError("candidate_resolution_fields")
        elif self.action == "request_later":
            if self.not_before is None or self.reason is None:
                raise DevelopmentExecutorError("deferred_request_incomplete")
            _require_rfc3339(self.not_before)
            _required_text(self.reason, "deferred_reason")
            if any(
                value is not None
                for value in (self.candidate_head, self.generation, self.failure)
            ) or self.evidence_refs:
                raise DevelopmentExecutorError("deferred_request_fields")
        elif self.action == "failed":
            if self.failure is None:
                raise DevelopmentExecutorError("failed_action_incomplete")
            _required_text(self.failure, "failure")
            if any(
                value is not None
                for value in (self.generation, self.not_before, self.reason)
            ) or self.evidence_refs:
                raise DevelopmentExecutorError("failed_action_fields")
        elif any(
            value is not None
            for value in (
                self.candidate_head,
                self.generation,
                self.not_before,
                self.reason,
                self.failure,
            )
        ) or self.evidence_refs:
            raise DevelopmentExecutorError("no_change_fields")

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action": self.action,
            "opportunity_id": self.opportunity_id,
            "current_body_ref": self.current_body_ref,
        }
        if self.candidate_head is not None:
            result["candidate_head"] = self.candidate_head
        if self.generation is not None:
            result["generation"] = self.generation
        if self.not_before is not None:
            result["not_before"] = self.not_before
        if self.reason is not None:
            result["reason"] = self.reason
        if self.evidence_refs:
            result["evidence_refs"] = list(self.evidence_refs)
        if self.organ_call_ref is not None:
            result["organ_call_ref"] = self.organ_call_ref
        if self.recall_trace:
            result["recall_trace"] = [
                trace.to_mapping() for trace in self.recall_trace
            ]
        if self.failure is not None:
            result["failure"] = self.failure
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BodyActionResult":
        action = value.get("action")
        if not isinstance(action, str):
            raise DevelopmentExecutorError("invalid_body_action")
        base = {"action", "opportunity_id", "current_body_ref"}
        optional_host_fields = {"organ_call_ref", "recall_trace"}
        expected_by_action = {
            "no_change": base,
            "candidate_submitted": base | {"candidate_head", "generation"},
            "retain": base | {"candidate_head", "evidence_refs"},
            "withdraw": base | {"candidate_head", "evidence_refs"},
            "request_later": base | {"not_before", "reason"},
            "failed": base | {"failure"},
        }
        expected = expected_by_action.get(action)
        if expected is None:
            raise DevelopmentExecutorError("invalid_body_action")
        received_fields = set(value)
        received_without_host_fields = received_fields - optional_host_fields
        if action == "failed" and (
            received_without_host_fields == expected | {"candidate_head"}
        ):
            expected = expected | {"candidate_head"}
        if received_without_host_fields != expected:
            raise DevelopmentExecutorError("invalid_body_action_fields")
        evidence = value.get("evidence_refs", [])
        if not isinstance(evidence, list) or any(
            not isinstance(item, str) for item in evidence
        ):
            raise DevelopmentExecutorError("invalid_evidence_refs")
        raw_recall_trace = value.get("recall_trace", [])
        if not isinstance(raw_recall_trace, list):
            raise DevelopmentExecutorError("invalid_recall_trace")
        try:
            recall_trace = tuple(
                RecallTrace.from_mapping(trace) for trace in raw_recall_trace
            )
        except ValueError as error:
            raise DevelopmentExecutorError("invalid_recall_trace") from error
        return cls(
            action=action,
            opportunity_id=value.get("opportunity_id"),
            current_body_ref=value.get("current_body_ref"),
            candidate_head=value.get("candidate_head"),
            generation=value.get("generation"),
            not_before=value.get("not_before"),
            reason=value.get("reason"),
            evidence_refs=tuple(evidence),
            organ_call_ref=value.get("organ_call_ref"),
            recall_trace=recall_trace,
            failure=value.get("failure"),
        )


class DevelopmentExecutorProtocol(Protocol):
    """Service-facing contract for a single Body-owned development offer."""

    def cancel(self) -> None:
        ...

    def offer_development(
        self,
        opportunity: DevelopmentOpportunity,
    ) -> BodyActionResult:
        ...


@dataclass(frozen=True)
class _DevelopmentDirective:
    action: str
    files: dict[str, str] | None = None
    activation_kind: str | None = None
    activation_artifact: str | None = None
    causation_ref: str | None = None
    candidate_head: str | None = None
    evidence_refs: tuple[str, ...] = ()
    not_before: str | None = None
    reason: str | None = None
    development_kind: str | None | object = DEVELOPMENT_DESCRIPTOR_UNSET
    development_artifact: str | None | object = DEVELOPMENT_DESCRIPTOR_UNSET


def _require_rfc3339(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DevelopmentExecutorError("invalid_not_before") from error
    if parsed.tzinfo is None:
        raise DevelopmentExecutorError("invalid_not_before")


def _parse_model_action(text: str) -> _DevelopmentDirective:
    if len(text.encode("utf-8")) > _MAX_MODEL_ACTION_BYTES:
        raise DevelopmentExecutorError("organ_result_oversize")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise DevelopmentExecutorError("organ_result_invalid") from error
    if not isinstance(value, dict):
        raise DevelopmentExecutorError("organ_result_invalid")
    action = value.get("action")
    if action not in _MODEL_ACTIONS:
        raise DevelopmentExecutorError("organ_result_invalid")

    if action == "no_change":
        if set(value) != {"action"}:
            raise DevelopmentExecutorError("organ_result_invalid")
        return _DevelopmentDirective(action=action)

    if action == "submit_successor":
        expected = {
            "action",
            "files",
            "activation_kind",
            "activation_artifact",
            "causation_ref",
        }
        descriptor_fields = {"development_kind", "development_artifact"}
        received = set(value)
        if received != expected and received != expected | descriptor_fields:
            raise DevelopmentExecutorError("organ_result_invalid")
        files = value["files"]
        if not isinstance(files, dict) or not files or any(
            not isinstance(path, str)
            or not path
            or not isinstance(content, str)
            for path, content in files.items()
        ):
            raise DevelopmentExecutorError("organ_result_invalid")
        activation_kind = _required_text(
            value["activation_kind"],
            "activation_kind",
        )
        activation_artifact = _required_text(
            value["activation_artifact"],
            "activation_artifact",
        )
        if activation_artifact not in files:
            raise DevelopmentExecutorError("organ_result_invalid")
        causation_ref = _required_text(
            value["causation_ref"],
            "causation_ref",
            maximum=1024,
        )
        if descriptor_fields <= received:
            development_kind = value["development_kind"]
            development_artifact = value["development_artifact"]
            if (development_kind is None) != (development_artifact is None):
                raise DevelopmentExecutorError("organ_result_invalid")
            if development_kind is not None:
                development_kind = _required_text(
                    development_kind,
                    "development_kind",
                    maximum=128,
                )
                development_artifact = _required_text(
                    development_artifact,
                    "development_artifact",
                )
                if development_artifact not in files:
                    raise DevelopmentExecutorError("organ_result_invalid")
        else:
            development_kind = DEVELOPMENT_DESCRIPTOR_UNSET
            development_artifact = DEVELOPMENT_DESCRIPTOR_UNSET
        return _DevelopmentDirective(
            action=action,
            files=dict(files),
            activation_kind=activation_kind,
            activation_artifact=activation_artifact,
            causation_ref=causation_ref,
            development_kind=development_kind,
            development_artifact=development_artifact,
        )

    if action == "request_later":
        if set(value) != {"action", "not_before", "reason"}:
            raise DevelopmentExecutorError("organ_result_invalid")
        not_before = _required_text(value["not_before"], "not_before")
        _require_rfc3339(not_before)
        return _DevelopmentDirective(
            action=action,
            not_before=not_before,
            reason=_required_text(value["reason"], "deferred_reason"),
        )

    if set(value) != {"action", "candidate_head", "evidence_refs"}:
        raise DevelopmentExecutorError("organ_result_invalid")
    candidate_head = _required_text(value["candidate_head"], "candidate_head")
    evidence = value["evidence_refs"]
    if (
        not isinstance(evidence, list)
        or not evidence
        or len(evidence) > _MAX_EVIDENCE_REFS
        or any(not isinstance(item, str) for item in evidence)
    ):
        raise DevelopmentExecutorError("organ_result_invalid")
    for ref in evidence:
        _required_text(ref, "evidence_ref")
    return _DevelopmentDirective(
        action=action,
        candidate_head=candidate_head,
        evidence_refs=tuple(evidence),
    )


def body_text_files_from_package(body_package: Mapping[str, Any]) -> dict[str, str]:
    """Expose the exact validated Current Body text to its organ prompt."""

    try:
        manifest = json.loads(
            base64.b64decode(
                body_package["manifest_base64"],
                validate=True,
            )
        )
        files = manifest["files"]
        blobs = body_package["blobs"]
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise DevelopmentExecutorError("body_package_unavailable") from error
    if not isinstance(files, dict) or not isinstance(blobs, dict):
        raise DevelopmentExecutorError("body_package_unavailable")
    extracted: dict[str, str] = {}
    try:
        for path, digest in files.items():
            if not isinstance(path, str) or not isinstance(digest, str):
                raise DevelopmentExecutorError("body_package_unavailable")
            extracted[path] = base64.b64decode(
                blobs[digest],
                validate=True,
            ).decode("utf-8")
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
        raise DevelopmentExecutorError("body_file_not_text") from error
    return extracted


def build_development_prompt(
    *,
    opportunity: BodyDevelopmentOpportunity,
    activation_context: str,
    body_files: Mapping[str, str],
) -> str:
    """Build an interface notice without choosing the Body's development method."""

    _required_text(activation_context, "activation_context", maximum=_MAX_BODY_PROMPT_BYTES)
    snapshot = json.dumps(
        dict(body_files),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    opportunity_json = json.dumps(
        opportunity.to_mapping(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    prompt = "\n".join(
        (
            "CURRENT BODY ACTIVATION (the Body's own existing content):",
            activation_context,
            "",
            "DEVELOPMENT OPPORTUNITY AND CURRENT HEAD LINEAGE FACTS (host-recorded raw facts, not a prescribed task or status judgment):",
            opportunity_json,
            "",
            "CURRENT BODY FILES (the exact readable Body material for this opportunity):",
            snapshot,
            "",
            "RUNNER INTERFACE AND BOUNDARY:",
            "- You may decide that no change is warranted. This opportunity does not require a summary, a memory method, a candidate, or a promotion.",
            "- lineage_facts reports only recorded manifest and ledger facts. no_recorded_resolution is not retain, and it does not imply pending, stable, probation, or any other status.",
            "- The Witness may append a bounded, same-Root, read-only experience page. It is context only: it grants no Surface client, submit, Authority, file-write, or arbitrary execution interface.",
            "- Your organ is read-only for external projects. Do not modify an external production project, exfiltrate secrets, or exceed the host's existing permissions.",
            "- To write a successor Body, return the complete successor files in the submit_successor action. The runner alone performs private Body lineage prepare and advance; do not claim a submission happened unless the runner returns it.",
            "- A submit_successor action may omit development_kind and development_artifact to preserve the current development descriptor, give both as strings to replace it, or give both as null to return its successor to text-only development.",
            "- Candidate trial, consequence reading, retain, withdraw, and a later opportunity are available only through the action object below. Retain and withdraw require a candidate head plus concrete evidence references.",
            "",
            "OPTIONAL PYTHON DEVELOPMENT DESCRIPTOR CONTRACT:",
            "- This section applies only if you choose both development_kind='python-development-v1' and a development_artifact in a submit_successor action. A text-only Body stays text-only; a filename or .py suffix never enables execution.",
            "- The artifact must be UTF-8 source that defines synchronous def develop(context). It returns one JSON-serializable dict using the same action-object mapping below; the runner, not the source, validates and applies that action.",
            "- context.opportunity and context.body_files are deep read-only mappings of the bound opportunity and exact current Body text. They expose no environment, credential, Authority, Surface, submit, filesystem, or arbitrary-process interface.",
            "- context.recall(*, before_sequence: int | None = None, limit: int = 12) accepts null/current-page or a positive earlier sequence and a limit from 1 through 12. It returns a read-only mapping {'experiences':[host-recorded same-Root events], 'has_more':bool}; each event includes its event_id, sequence, occurred_at, event_kind, execution metadata, heads, and payload.",
            "- context.invoke_organ(prompt: str) accepts one bounded text prompt. It returns a read-only mapping with exactly one of final_text or failure, always recall_trace, and optionally organ_call_ref. final_text, when present, is raw model text that you may parse into an action object. Serial calls may continue the same host-native organ thread while this opportunity remains valid; concurrent calls, a changed call reference, or a revoked opportunity are rejected.",
            "- All context ports, including repeated recall and serial organ calls, share this opportunity's single absolute host deadline and may be cancelled by timeout, Authority Off, lease loss, or Body exit; no call receives an independent retry budget.",
            "- Python source is not itself a sandbox. It is eligible to run only inside the established LPAC Body boundary: reviewed staged runtime/payload is readable, only private scratch is writable, and external project files, network, loopback, host credentials, and host control surfaces are unavailable.",
            "",
            "Your FINAL agent message must be exactly one JSON object, with no Markdown or explanation, using one of:",
            '{"action":"no_change"}',
            '{"action":"submit_successor","files":{"entrypoint.md":"..."},"activation_kind":"surface-context-utf8-v1","activation_artifact":"entrypoint.md","causation_ref":"...","development_kind":null,"development_artifact":null}',
            '{"action":"submit_successor","files":{"entrypoint.md":"...","develop.py":"..."},"activation_kind":"surface-context-utf8-v1","activation_artifact":"entrypoint.md","causation_ref":"...","development_kind":"python-development-v1","development_artifact":"develop.py"}',
            '{"action":"request_later","not_before":"RFC3339 timestamp with timezone","reason":"..."}',
            '{"action":"retain","candidate_head":"...","evidence_refs":["..."]}',
            '{"action":"withdraw","candidate_head":"...","evidence_refs":["..."]}',
        )
    )
    if len(prompt.encode("utf-8")) > _MAX_BODY_PROMPT_BYTES:
        raise DevelopmentExecutorError("body_prompt_oversize")
    return prompt


if TYPE_CHECKING:
    from .body import BodyManifest
    from .organ_broker import OrganInvoker
    from .runtime import DevelopmentalRuntime
    from .witness import WitnessCore


class DevelopmentExecutor:
    """Loads one Current Body and gives it one bounded opportunity to act."""

    def __init__(
        self,
        runtime: "DevelopmentalRuntime",
        witness: "WitnessCore",
        *,
        development_timeout_seconds: float = 300.0,
        ready_timeout_seconds: float = 5.0,
        organ_broker: "OrganInvoker | None" = None,
    ) -> None:
        if development_timeout_seconds <= 0 or ready_timeout_seconds <= 0:
            raise ValueError("development timeouts must be positive")
        self._runtime = runtime
        self._witness = witness
        self._development_timeout_seconds = development_timeout_seconds
        self._ready_timeout_seconds = ready_timeout_seconds
        self._organ_broker = organ_broker
        self._active_body_guard = threading.Lock()
        self._active_body: Any | None = None
        self._active_broker: "OrganInvoker | None" = None
        self._active_opportunity_id: str | None = None
        self._offer_active = False
        self._offer_cancelled = False

    def cancel(self) -> None:
        """Permanently cancel this executor's one Body opportunity."""

        with self._active_body_guard:
            self._offer_cancelled = True
            body = self._active_body
            broker = self._active_broker
            opportunity_id = self._active_opportunity_id
        if broker is not None and opportunity_id is not None:
            broker.cancel(opportunity_id)
        if body is not None:
            body.close()

    def _begin_offer(self) -> None:
        with self._active_body_guard:
            if self._offer_active:
                raise DevelopmentExecutorError("development_offer_already_active")
            if self._offer_cancelled:
                raise DevelopmentExecutorError("development_cancelled")
            self._offer_active = True

    def _register_active_offer(
        self,
        *,
        body: Any | None,
        broker: "OrganInvoker",
        opportunity_id: str,
    ) -> bool:
        with self._active_body_guard:
            self._active_body = body
            self._active_broker = broker
            self._active_opportunity_id = opportunity_id
            return self._offer_cancelled

    def _finish_offer(self, body: Any | None, broker: "OrganInvoker | None") -> None:
        with self._active_body_guard:
            if self._active_body is body:
                self._active_body = None
            if self._active_broker is broker:
                self._active_broker = None
                self._active_opportunity_id = None
            self._offer_active = False

    def offer_development(
        self,
        opportunity: DevelopmentOpportunity,
    ) -> BodyActionResult:
        self._begin_offer()
        opportunity_deadline = monotonic() + self._development_timeout_seconds
        session_started = False
        body: Any | None = None
        broker: "OrganInvoker | None" = None
        try:
            status = self._runtime.status()
            if status.authority != "on":
                raise DevelopmentExecutorError("authority_off")
            if (
                status.root != opportunity.root
                or status.head != opportunity.current_body_ref
            ):
                raise DevelopmentExecutorError("opportunity_current_body_mismatch")
            manifest = self._runtime.body_store.read_manifest(status.head)
            if not opportunity.lineage_facts.matches_current_manifest(manifest):
                raise DevelopmentExecutorError("opportunity_lineage_facts_mismatch")

            wake = self._runtime.wake(
                execution_surface="agentic-evo-body",
                session_id=opportunity.id,
                project_environment=opportunity.working_dir,
            )
            session_started = True
            if wake.root != opportunity.root or wake.head != opportunity.current_body_ref:
                raise DevelopmentExecutorError("opportunity_current_body_mismatch")

            broker = self._broker_for(
                opportunity,
                deadline=opportunity_deadline,
            )
            if self._register_active_offer(
                body=None,
                broker=broker,
                opportunity_id=opportunity.id,
            ):
                broker.cancel(opportunity.id)
                raise DevelopmentExecutorError("development_cancelled")

            from .body_process import BodyProcessSupervisor

            body = BodyProcessSupervisor(
                self._runtime,
                self._witness,
                ready_timeout_seconds=self._ready_timeout_seconds,
                request_timeout_seconds=self._development_timeout_seconds,
                organ_broker=broker,
                development_recall=self._recall_experiences_for_body,
                development_deadline=opportunity_deadline,
            ).spawn_current()
            if self._register_active_offer(
                body=body,
                broker=broker,
                opportunity_id=opportunity.id,
            ):
                body.close()
                raise DevelopmentExecutorError("development_cancelled")
            return body.offer_development(opportunity=opportunity)
        finally:
            pending_failure = sys.exc_info()[1]
            try:
                if body is not None:
                    body.close()
                if session_started:
                    try:
                        self._runtime.sleep(
                            execution_surface="agentic-evo-body",
                            session_id=opportunity.id,
                        )
                    except Exception as error:
                        if pending_failure is None:
                            raise DevelopmentExecutorError(
                                "temporary_body_session_sleep_failed"
                            ) from error
            finally:
                self._finish_offer(body, broker)

    def _recall_experiences_for_body(
        self,
        *,
        opportunity: DevelopmentOpportunity,
        before_sequence: int | None,
        limit: int,
        cancel_event: threading.Event,
    ) -> tuple[list[dict[str, Any]], bool]:
        self._assert_active_recall_opportunity(
            opportunity=opportunity,
            cancel_event=cancel_event,
        )
        kwargs: dict[str, Any] = {
            "execution_surface": "agentic-evo-body",
            "session_id": opportunity.id,
            "limit": limit,
        }
        if before_sequence is not None:
            kwargs["before_sequence"] = before_sequence
        experiences, has_more = self._runtime.recall_experiences(**kwargs)
        self._assert_active_recall_opportunity(
            opportunity=opportunity,
            cancel_event=cancel_event,
        )
        return experiences, has_more

    def _assert_active_recall_opportunity(
        self,
        *,
        opportunity: DevelopmentOpportunity,
        cancel_event: threading.Event,
    ) -> None:
        if cancel_event.is_set():
            raise DevelopmentExecutorError("development_cancelled")
        with self._active_body_guard:
            if (
                self._offer_cancelled
                or not self._offer_active
                or self._active_opportunity_id != opportunity.id
            ):
                raise DevelopmentExecutorError("development_cancelled")
        status = self._runtime.status()
        if status.authority != "on":
            raise DevelopmentExecutorError("authority_off")
        if (
            status.root != opportunity.root
            or status.head != opportunity.current_body_ref
        ):
            raise DevelopmentExecutorError("opportunity_current_body_mismatch")

    def _broker_for(
        self,
        opportunity: DevelopmentOpportunity,
        *,
        deadline: float,
    ) -> "OrganInvoker":
        if self._organ_broker is not None:
            return self._organ_broker
        from .organ_broker import WitnessOrganBroker

        return WitnessOrganBroker(
            runtime=self._runtime,
            opportunity_id=opportunity.id,
            root=opportunity.root,
            current_body_ref=opportunity.current_body_ref,
            organ_argv=opportunity.organ_argv,
            working_dir=opportunity.working_dir,
            timeout_seconds=self._development_timeout_seconds,
            deadline=deadline,
        )
