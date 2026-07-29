from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping

from ._util import ExclusiveFileLock, utc_now
from .body import BODY_SCHEMA_VERSION, BodyManifest, BodyStore
from .errors import (
    GenesisExistsError,
    HeadConflictError,
    IntegrityError,
    InvalidBodyError,
    RootBindingError,
)
from .trusted import TRUSTED_SCHEMA_VERSION, TrustedState


RUNTIME_SCHEMA_VERSION = "agentic-evo-runtime-v2"
SURFACE_CONTEXT_ACTIVATION_KIND = "surface-context-utf8-v1"


def _require_supported_activation_kind(activation_kind: str) -> None:
    if activation_kind != SURFACE_CONTEXT_ACTIVATION_KIND:
        raise InvalidBodyError(f"unsupported activation kind: {activation_kind}")


def _require_supported_activation(manifest: BodyManifest) -> tuple[str, str]:
    if manifest.activation_kind is None or manifest.activation_artifact is None:
        raise InvalidBodyError("Body has no activation descriptor")
    _require_supported_activation_kind(manifest.activation_kind)
    return manifest.activation_kind, manifest.activation_artifact


def _serialized_lifecycle(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapped(runtime: "DevelopmentalRuntime", *args: Any, **kwargs: Any) -> Any:
        with ExclusiveFileLock(runtime.runtime_path / ".lifecycle.lock"):
            return method(runtime, *args, **kwargs)

    return wrapped


@dataclass(frozen=True)
class RuntimeStatus:
    root: str
    head: str
    generation: int
    authority: str
    lifecycle_state: str
    active_sessions: tuple[str, ...]
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
            active_sessions=tuple(sorted(sessions)),
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
    def sleep(self, *, session_id: str) -> RuntimeStatus:
        self.trusted.end_session(session_id=session_id)
        return self.status()

    @_serialized_lifecycle
    def observe(
        self,
        *,
        event_kind: str,
        source_kind: str,
        author_kind: str,
        payload: Mapping[str, Any],
        execution_surface: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        tool_call_id: str | None = None,
        project_environment: str | None = None,
        human_intervention_kind: str | None = None,
        coverage_gap: str | None = None,
    ):
        snapshot = self.trusted.gate()
        return self.trusted.append(
            event_kind=event_kind,
            root_commitment=snapshot.root,
            head_before=snapshot.head,
            head_after=snapshot.head,
            source_kind=source_kind,
            author_kind=author_kind,
            execution_surface=execution_surface,
            session_id=session_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            project_environment=project_environment,
            human_intervention_kind=human_intervention_kind,
            coverage_gap=coverage_gap,
            payload=payload,
        )

    @_serialized_lifecycle
    def prepare_successor(
        self,
        *,
        expected_parent: str,
        files: Mapping[str, str | bytes],
        author_kind: str,
        activation_kind: str | None = None,
        activation_artifact: str | None = None,
    ) -> str:
        snapshot = self.trusted.gate()
        candidate = self.body_store.commit(
            root=snapshot.root,
            parent_head=expected_parent,
            files=files,
            author_kind=author_kind,
            activation_kind=activation_kind,
            activation_artifact=activation_artifact,
        )
        self.trusted.append(
            event_kind="body_candidate_prepared",
            root_commitment=snapshot.root,
            head_before=snapshot.head,
            head_after=snapshot.head,
            source_kind="body",
            author_kind=author_kind,
            payload={
                "candidate_head": candidate,
                "expected_parent": expected_parent,
            },
            human_intervention_kind=(
                "body_content_authored"
                if author_kind == "human_learning_intervention"
                else None
            ),
        )
        return candidate

    @_serialized_lifecycle
    def advance_head(
        self,
        *,
        expected_head: str,
        candidate_head: str,
    ) -> RuntimeStatus:
        before = self.trusted.gate()
        if before.head != expected_head:
            raise HeadConflictError("Head changed before this transition")
        current = self.body_store.read_manifest(before.head)
        candidate = self.body_store.read_manifest(candidate_head)
        self._bind_manifest(before.root, before.head, current)
        _require_supported_activation(candidate)
        if candidate.generation != current.generation + 1:
            raise IntegrityError("candidate generation does not follow current Head")
        self.trusted.advance_head(
            expected_head=expected_head,
            candidate=candidate,
            human_intervention_kind=(
                "selected_successor"
                if candidate.author_kind == "human_learning_intervention"
                else None
            ),
        )
        return self.status()

    @_serialized_lifecycle
    def turn_off(self) -> RuntimeStatus:
        self.trusted.set_authority(authority="off")
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
