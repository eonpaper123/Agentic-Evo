from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping

from ._util import ExclusiveFileLock, utc_now
from .body import (
    BODY_SCHEMA_VERSION,
    DEVELOPMENT_DESCRIPTOR_UNSET,
    BodyManifest,
    BodyStore,
)
from .development_entrypoint import DEVELOPMENT_ENTRYPOINT_KIND
from .errors import (
    AuthorityError,
    BodyLeaseError,
    GenesisExistsError,
    HeadConflictError,
    IntegrityError,
    InvalidBodyError,
    RootBindingError,
)
from .trusted import TRUSTED_SCHEMA_VERSION, TrustedState


RUNTIME_SCHEMA_VERSION = "agentic-evo-runtime-v3"
SURFACE_CONTEXT_ACTIVATION_KIND = "surface-context-utf8-v1"
RECALLABLE_EXPERIENCE_EVENT_KINDS = frozenset(
    {
        "user_prompt_submitted",
        "tool_use_finished",
        "assistant_message_observed",
        "codex_turn_completed",
        "turn_stopped",
        "body_development_action",
        "body_development_failed",
        "head_advanced",
    }
)


def _require_supported_activation_kind(activation_kind: str) -> None:
    if activation_kind != SURFACE_CONTEXT_ACTIVATION_KIND:
        raise InvalidBodyError(f"unsupported activation kind: {activation_kind}")


def _require_supported_activation(manifest: BodyManifest) -> tuple[str, str]:
    if manifest.activation_kind is None or manifest.activation_artifact is None:
        raise InvalidBodyError("Body has no activation descriptor")
    _require_supported_activation_kind(manifest.activation_kind)
    return manifest.activation_kind, manifest.activation_artifact


def _require_supported_development(manifest: BodyManifest) -> None:
    if manifest.development_kind is None:
        return
    if manifest.development_kind != DEVELOPMENT_ENTRYPOINT_KIND:
        raise InvalidBodyError(
            f"unsupported development kind: {manifest.development_kind}"
        )
    try:
        from .body_lpac import is_lpac_body_runtime_available
    except ImportError as error:
        raise InvalidBodyError(
            "python development requires the Windows LPAC Body runtime"
        ) from error

    if not is_lpac_body_runtime_available():
        raise InvalidBodyError(
            "python development requires the Windows LPAC Body runtime"
        )


def _serialized_lifecycle(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapped(runtime: "DevelopmentalRuntime", *args: Any, **kwargs: Any) -> Any:
        with ExclusiveFileLock(runtime.runtime_path / ".lifecycle.lock"):
            return method(runtime, *args, **kwargs)

    return wrapped


@dataclass(frozen=True, order=True)
class SessionIdentity:
    execution_surface: str
    session_id: str


@dataclass(frozen=True)
class RuntimeStatus:
    root: str
    head: str
    generation: int
    authority: str
    lifecycle_state: str
    active_sessions: tuple[SessionIdentity, ...]
    instrument_version: str
    protocol_version: str


@dataclass(frozen=True)
class WakeState:
    root: str
    head: str
    generation: int
    body_files: tuple[str, ...]
    activation_kind: str
    activation_artifact: str
    activation_digest: str
    activation_context: str


class DevelopmentalRuntime:
    """Coordinates one trusted transaction domain with an opaque Body store."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.runtime_path = self.home / "trusted"
        self.trusted = TrustedState.load(self.runtime_path)
        self.evidence = self.trusted
        self.body_store = BodyStore(self.home / "body")
        with ExclusiveFileLock(self.runtime_path / ".lifecycle.lock"):
            self._validate_current_body()

    @classmethod
    def genesis(
        cls,
        home: Path,
        *,
        host_binding: str,
        purpose_anchor: str,
        initial_body: Mapping[str, str | bytes],
        instrument_version: str,
        protocol_version: str,
        initial_activation_kind: str = SURFACE_CONTEXT_ACTIVATION_KIND,
        initial_activation_artifact: str = "entrypoint.md",
    ) -> "DevelopmentalRuntime":
        _require_supported_activation_kind(initial_activation_kind)
        home = Path(home)
        with ExclusiveFileLock(home / ".genesis.lock"):
            return cls._genesis_locked(
                home,
                host_binding=host_binding,
                purpose_anchor=purpose_anchor,
                initial_body=initial_body,
                instrument_version=instrument_version,
                protocol_version=protocol_version,
                initial_activation_kind=initial_activation_kind,
                initial_activation_artifact=initial_activation_artifact,
            )

    @classmethod
    def _genesis_locked(
        cls,
        home: Path,
        *,
        host_binding: str,
        purpose_anchor: str,
        initial_body: Mapping[str, str | bytes],
        instrument_version: str,
        protocol_version: str,
        initial_activation_kind: str,
        initial_activation_artifact: str,
    ) -> "DevelopmentalRuntime":
        trusted_path = home / "trusted"
        if TrustedState.has_genesis(trusted_path):
            raise GenesisExistsError("runtime Genesis already exists")

        root = TrustedState.generate_root()
        body_store = BodyStore(home / "body")
        initial_head = body_store.commit(
            root=root,
            parent_head=None,
            files=initial_body,
            author_kind="research_instrument",
            activation_kind=initial_activation_kind,
            activation_artifact=initial_activation_artifact,
        )
        TrustedState.genesis(
            trusted_path,
            host_binding=host_binding,
            purpose_anchor=purpose_anchor,
            root=root,
            initial_head=initial_head,
            instrument_version=instrument_version,
            protocol_version=protocol_version,
            genesis_payload={
                "body_schema": BODY_SCHEMA_VERSION,
                "trusted_schema": TRUSTED_SCHEMA_VERSION,
                "runtime_schema": RUNTIME_SCHEMA_VERSION,
                "activation_kind": initial_activation_kind,
            },
        )
        return cls(home)

    @classmethod
    def load(cls, home: Path) -> "DevelopmentalRuntime":
        return cls(home)

    def status(self) -> RuntimeStatus:
        snapshot, sessions = self.trusted.current()
        manifest = self.body_store.read_manifest(snapshot.head)
        self._bind_manifest(snapshot.root, snapshot.head, manifest)
        lifecycle = (
            "off"
            if not snapshot.is_on
            else ("awake" if sessions else "waiting")
        )
        return RuntimeStatus(
            root=snapshot.root,
            head=snapshot.head,
            generation=manifest.generation,
            authority=snapshot.authority,
            lifecycle_state=lifecycle,
            active_sessions=tuple(
                SessionIdentity(
                    execution_surface=execution_surface,
                    session_id=session_id,
                )
                for execution_surface, session_id in sorted(sessions)
            ),
            instrument_version=self.trusted.instrument_version,
            protocol_version=self.trusted.protocol_version,
        )

    @_serialized_lifecycle
    def wake(
        self,
        *,
        execution_surface: str,
        session_id: str,
        project_environment: str,
        model: str | None = None,
    ) -> WakeState:
        snapshot = self.trusted.gate()
        manifest = self.body_store.read_manifest(snapshot.head)
        self._bind_manifest(snapshot.root, snapshot.head, manifest)
        activation_kind, activation_artifact = _require_supported_activation(
            manifest
        )
        file_map = dict(manifest.files)
        activation_digest = file_map[activation_artifact]
        raw_context = self.body_store.read_file(
            manifest.commitment,
            activation_artifact,
        )
        activation_context = raw_context.decode("utf-8", errors="replace")[:8000]
        self.trusted.start_session(
            expected_head=snapshot.head,
            session_id=session_id,
            value={
                "execution_surface": execution_surface,
                "project_environment": project_environment,
                "model": model,
                "loaded_body_head": snapshot.head,
                "started_at": utc_now(),
            },
            execution_surface=execution_surface,
            project_environment=project_environment,
            model=model,
            body_generation=manifest.generation,
            activation_kind=activation_kind,
            activation_artifact=activation_artifact,
            activation_digest=activation_digest,
        )
        return WakeState(
            root=snapshot.root,
            head=snapshot.head,
            generation=manifest.generation,
            body_files=manifest.file_names,
            activation_kind=activation_kind,
            activation_artifact=activation_artifact,
            activation_digest=activation_digest,
            activation_context=activation_context,
        )

    @_serialized_lifecycle
    def sleep(
        self,
        *,
        execution_surface: str,
        session_id: str,
    ) -> RuntimeStatus:
        self.trusted.end_session(
            execution_surface=execution_surface,
            session_id=session_id,
        )
        return self.status()

    @_serialized_lifecycle
    def detach_persisted_sessions(self):
        return self.trusted.detach_persisted_sessions()

    @_serialized_lifecycle
    def observe(
        self,
        *,
        event_kind: str,
        payload: Mapping[str, Any],
        occurred_at: str | None = None,
        execution_surface: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        tool_call_id: str | None = None,
        project_environment: str | None = None,
        correlation_ref: str | None = None,
        causation_ref: str | None = None,
        parent_ref: str | None = None,
        coverage_gap: str | None = None,
    ):
        observed_payload = dict(payload)
        if execution_surface and session_id:
            session = self.trusted.session_value(
                execution_surface=execution_surface,
                session_id=session_id,
            )
            if session is not None:
                observed_payload["loaded_body_head"] = session["loaded_body_head"]
        return self.trusted.append_observation(
            event_kind=event_kind,
            source_kind="execution_surface",
            author_kind="surface_unverified",
            occurred_at=occurred_at,
            execution_surface=execution_surface,
            session_id=session_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            project_environment=project_environment,
            correlation_ref=correlation_ref,
            causation_ref=causation_ref,
            parent_ref=parent_ref,
            coverage_gap=coverage_gap,
            payload=observed_payload,
        )

    @_serialized_lifecycle
    def recall_experiences(
        self,
        *,
        execution_surface: str,
        session_id: str,
        limit: int,
        before_sequence: int | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        records, has_more = self.trusted.recent_records(
            execution_surface=execution_surface,
            excluded_session_id=session_id,
            event_kinds=RECALLABLE_EXPERIENCE_EVENT_KINDS,
            limit=limit,
            before_sequence=before_sequence,
        )
        return (
            [
                {
                    "event_id": record.event_id,
                    "sequence": record.sequence,
                    "occurred_at": record.occurred_at,
                    "event_kind": record.event_kind,
                    "execution_surface": record.execution_surface,
                    "session_id": record.session_id,
                    "turn_id": record.turn_id,
                    "tool_call_id": record.tool_call_id,
                    "project_environment": record.project_environment,
                    "loaded_body_head": record.payload.get("loaded_body_head"),
                    "observed_global_head": record.head_before,
                    "payload": dict(record.payload),
                }
                for record in records
            ],
            has_more,
        )

    @_serialized_lifecycle
    def record_body_development(
        self,
        *,
        event_kind: str,
        opportunity_id: str,
        project_environment: str,
        payload: Mapping[str, Any],
        causation_ref: str | None = None,
    ):
        if event_kind not in {
            "body_development_action",
            "body_development_failed",
        }:
            raise ValueError("unsupported Body development event")
        return self.trusted.append_observation(
            event_kind=event_kind,
            source_kind="body",
            author_kind="in_process_rehearsal",
            execution_surface="agentic-evo-body",
            session_id=opportunity_id,
            project_environment=project_environment,
            causation_ref=causation_ref,
            payload=dict(payload),
        )

    @_serialized_lifecycle
    def _body_lease_binding(self) -> tuple[RuntimeStatus, int]:
        return self.status(), self.trusted.authority_epoch()

    @_serialized_lifecycle
    def _prepare_successor(
        self,
        *,
        expected_parent: str,
        files: Mapping[str, str | bytes],
        author_kind: str,
        ingress_path: str,
        expected_authority_epoch: int,
        activation_kind: str | None = None,
        activation_artifact: str | None = None,
        development_kind: str | None | object = DEVELOPMENT_DESCRIPTOR_UNSET,
        development_artifact: str | None | object = DEVELOPMENT_DESCRIPTOR_UNSET,
        causation_ref: str | None = None,
    ) -> str:
        snapshot = self.trusted.gate()
        if snapshot.head != expected_parent:
            raise HeadConflictError("candidate parent is not the Current Head")
        if self.trusted.authority_epoch() != expected_authority_epoch:
            raise BodyLeaseError("Body lease crossed an Off boundary")
        candidate = self.body_store.commit(
            root=snapshot.root,
            parent_head=expected_parent,
            files=files,
            author_kind=author_kind,
            activation_kind=activation_kind,
            activation_artifact=activation_artifact,
            development_kind=development_kind,
            development_artifact=development_artifact,
        )
        candidate_manifest = self.body_store.read_manifest(candidate)
        self.trusted.append(
            event_kind="body_candidate_prepared",
            root_commitment=snapshot.root,
            head_before=snapshot.head,
            head_after=snapshot.head,
            source_kind="body",
            author_kind=author_kind,
            causation_ref=causation_ref,
            payload={
                "candidate_head": candidate,
                "expected_parent": expected_parent,
                "ingress_path": ingress_path,
                "operation": "prepare_successor",
                "affected_domain": "body_lineage",
                "development_kind": candidate_manifest.development_kind,
                "development_artifact": candidate_manifest.development_artifact,
            },
            human_intervention_kind=(
                "body_content_authored"
                if author_kind == "human_learning_intervention"
                else None
            ),
        )
        return candidate

    @_serialized_lifecycle
    def _advance_head(
        self,
        *,
        expected_head: str,
        candidate_head: str,
        author_kind: str,
        ingress_path: str,
        expected_authority_epoch: int,
        execution_surface: str | None = None,
        session_id: str | None = None,
    ) -> RuntimeStatus:
        before = self.trusted.gate()
        if before.head != expected_head:
            raise HeadConflictError("Head changed before this transition")
        if self.trusted.authority_epoch() != expected_authority_epoch:
            raise BodyLeaseError("Body lease crossed an Off boundary")
        current = self.body_store.read_manifest(before.head)
        candidate = self.body_store.read_manifest(candidate_head)
        self._bind_manifest(before.root, before.head, current)
        if candidate.author_kind != author_kind:
            raise AuthorityError("candidate authorship does not match its ingress")
        _require_supported_activation(candidate)
        _require_supported_development(candidate)
        if candidate.generation != current.generation + 1:
            raise IntegrityError("candidate generation does not follow current Head")
        self.trusted.advance_head(
            expected_head=expected_head,
            candidate=candidate,
            author_kind=author_kind,
            ingress_path=ingress_path,
            execution_surface=execution_surface,
            session_id=session_id,
            human_intervention_kind=(
                "selected_successor"
                if author_kind == "human_learning_intervention"
                else None
            ),
        )
        return self.status()

    @_serialized_lifecycle
    def turn_off(self) -> RuntimeStatus:
        self.trusted.set_authority(authority="off")
        return self.status()

    @_serialized_lifecycle
    def rehearse_turn_off(self) -> RuntimeStatus:
        self.trusted.set_off_rehearsal()
        return self.status()

    @_serialized_lifecycle
    def turn_on(self, *, host_binding: str) -> RuntimeStatus:
        self.trusted.set_authority(
            authority="on",
            host_binding=host_binding,
        )
        return self.status()

    def _validate_current_body(self) -> None:
        snapshot = self.trusted.snapshot()
        manifest = self.body_store.read_manifest(snapshot.head)
        self._bind_manifest(snapshot.root, snapshot.head, manifest)
        _require_supported_activation(manifest)
        _require_supported_development(manifest)

    @staticmethod
    def _bind_manifest(
        root: str,
        head: str,
        manifest: BodyManifest,
    ) -> None:
        if manifest.root != root:
            raise RootBindingError("current Body Root does not match trusted Root")
        if manifest.commitment != head:
            raise HeadConflictError("current Body does not match trusted Head")
