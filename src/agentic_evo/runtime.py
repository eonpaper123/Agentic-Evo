from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping

from ._util import ExclusiveFileLock, atomic_write_json, read_json, utc_now
from .body import BODY_SCHEMA_VERSION, BodyManifest, BodyStore
from .errors import (
    GenesisExistsError,
    HeadConflictError,
    IntegrityError,
    InvalidBodyError,
)
from .evidence import EvidenceLedger
from .kernel import MicroLifeKernel


RUNTIME_SCHEMA_VERSION = "agentic-evo-runtime-v1"
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
    """Coordinates the kernel, opaque body store, and independent evidence ledger."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.kernel = MicroLifeKernel.load(self.home / "kernel")
        self.body_store = BodyStore(self.home / "body")
        self.evidence = EvidenceLedger.load(self.home / "evidence")
        self.runtime_path = self.home / "runtime"
        with ExclusiveFileLock(self.runtime_path / ".lifecycle.lock"):
            self._validate_runtime_state(self._read_runtime_state())
            self._validate_cross_domain_state()

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
        home = Path(home)
        marker_paths = [
            home / "kernel" / "state.json",
            home / "evidence" / "meta.json",
            home / "runtime" / "state.json",
        ]
        if any(path.exists() for path in marker_paths):
            raise GenesisExistsError("runtime Genesis already exists")
        root = MicroLifeKernel.generate_root()
        body_store = BodyStore(home / "body")
        initial_head = body_store.commit(
            root=root,
            parent_head=None,
            files=initial_body,
            author_kind="research_instrument",
            activation_kind=initial_activation_kind,
            activation_artifact=initial_activation_artifact,
        )
        MicroLifeKernel.genesis(
            home / "kernel",
            host_binding=host_binding,
            purpose_anchor=purpose_anchor,
            root=root,
            initial_head=initial_head,
        )
        ledger = EvidenceLedger.create(
            home / "evidence",
            instrument_version=instrument_version,
            protocol_version=protocol_version,
        )
        runtime_path = home / "runtime"
        atomic_write_json(
            runtime_path / "state.json",
            {
                "schema_version": RUNTIME_SCHEMA_VERSION,
                "active_sessions": {},
                "updated_at": utc_now(),
            },
        )
        ledger.append(
            event_kind="genesis",
            root_commitment=root,
            head_before=None,
            head_after=initial_head,
            source_kind="research_instrument",
            author_kind="research_instrument",
            payload={
                "body_schema": BODY_SCHEMA_VERSION,
                "kernel_schema": "agentic-evo-kernel-v1",
                "runtime_schema": RUNTIME_SCHEMA_VERSION,
                "activation_kind": initial_activation_kind,
            },
        )
        return cls(home)

    @classmethod
    def load(cls, home: Path) -> "DevelopmentalRuntime":
        return cls(home)

    def status(self) -> RuntimeStatus:
        kernel = MicroLifeKernel.load(self.home / "kernel").snapshot()
        manifest = self.body_store.read_manifest(kernel.head)
        runtime_state = self._read_runtime_state()
        self._validate_runtime_state(runtime_state)
        active_sessions = tuple(sorted(runtime_state["active_sessions"]))
        lifecycle = (
            "off"
            if not kernel.is_on
            else ("awake" if active_sessions else "waiting")
        )
        return RuntimeStatus(
            root=kernel.root,
            head=kernel.head,
            generation=manifest.generation,
            authority=kernel.authority,
            lifecycle_state=lifecycle,
            active_sessions=active_sessions,
            instrument_version=self.evidence.instrument_version,
            protocol_version=self.evidence.protocol_version,
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
        kernel = MicroLifeKernel.load(self.home / "kernel")
        snapshot = kernel.gate()
        manifest = self.body_store.read_manifest(snapshot.head)
        kernel.bind(root=manifest.root, head=manifest.commitment)
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
        self._update_session(
            session_id=session_id,
            value={
                "execution_surface": execution_surface,
                "project_environment": project_environment,
                "model": model,
                "started_at": utc_now(),
            },
        )
        self.evidence.append(
            event_kind="session_start",
            root_commitment=snapshot.root,
            head_before=snapshot.head,
            head_after=snapshot.head,
            source_kind="execution_surface",
            author_kind="normal_host_interaction",
            execution_surface=execution_surface,
            session_id=session_id,
            project_environment=project_environment,
            payload={
                "body_generation": manifest.generation,
                "model_ref": model,
                "activation_kind": activation_kind,
                "activation_artifact": activation_artifact,
                "activation_digest": activation_digest,
            },
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
        previous = self.status()
        session = self._remove_session(session_id)
        self.evidence.append(
            event_kind="session_end",
            root_commitment=previous.root,
            head_before=previous.head,
            head_after=previous.head,
            source_kind="execution_surface",
            author_kind="normal_host_interaction",
            execution_surface=(
                str(session.get("execution_surface")) if session else None
            ),
            session_id=session_id,
            project_environment=(
                str(session.get("project_environment")) if session else None
            ),
            payload={
                "session_was_active": session is not None,
            },
        )
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
        MicroLifeKernel.load(self.home / "kernel").gate()
        status = self.status()
        return self.evidence.append(
            event_kind=event_kind,
            root_commitment=status.root,
            head_before=status.head,
            head_after=status.head,
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
        status = self.status()
        MicroLifeKernel.load(self.home / "kernel").gate()
        candidate = self.body_store.commit(
            root=status.root,
            parent_head=expected_parent,
            files=files,
            author_kind=author_kind,
            activation_kind=activation_kind,
            activation_artifact=activation_artifact,
        )
        self.evidence.append(
            event_kind="body_candidate_prepared",
            root_commitment=status.root,
            head_before=status.head,
            head_after=status.head,
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
        kernel = MicroLifeKernel.load(self.home / "kernel")
        before = kernel.gate()
        if before.head != expected_head:
            raise HeadConflictError("Head changed before this transition")
        current = self.body_store.read_manifest(before.head)
        candidate = self.body_store.read_manifest(candidate_head)
        _require_supported_activation(candidate)
        if candidate.generation != current.generation + 1:
            raise IntegrityError("candidate generation does not follow current Head")
        self.evidence.append(
            event_kind="head_advance_requested",
            root_commitment=before.root,
            head_before=before.head,
            head_after=before.head,
            source_kind="body",
            author_kind=candidate.author_kind,
            payload={
                "candidate_head": candidate_head,
                "expected_head": expected_head,
            },
            human_intervention_kind=(
                "selected_successor"
                if candidate.author_kind == "human_learning_intervention"
                else None
            ),
        )
        after = kernel.advance_head(
            expected_head=expected_head,
            candidate=candidate,
        )
        self.evidence.append(
            event_kind="head_advanced",
            root_commitment=after.root,
            head_before=expected_head,
            head_after=after.head,
            source_kind="kernel",
            author_kind=candidate.author_kind,
            payload={
                "body_generation": candidate.generation,
                "parent_head": candidate.parent_head,
            },
            human_intervention_kind=(
                "selected_successor"
                if candidate.author_kind == "human_learning_intervention"
                else None
            ),
        )
        return self.status()

    @_serialized_lifecycle
    def turn_off(self) -> RuntimeStatus:
        before = self.status()
        kernel = MicroLifeKernel.load(self.home / "kernel")
        after = kernel.turn_off()
        self._clear_sessions()
        self.evidence.append(
            event_kind="host_off",
            root_commitment=after.root,
            head_before=before.head,
            head_after=after.head,
            source_kind="kernel",
            author_kind="normal_host_interaction",
            payload={},
        )
        return self.status()

    @_serialized_lifecycle
    def turn_on(self, *, host_binding: str) -> RuntimeStatus:
        kernel = MicroLifeKernel.load(self.home / "kernel")
        after = kernel.turn_on(host_binding=host_binding)
        self.evidence.append(
            event_kind="host_on",
            root_commitment=after.root,
            head_before=after.head,
            head_after=after.head,
            source_kind="kernel",
            author_kind="normal_host_interaction",
            payload={},
        )
        return self.status()

    def _read_runtime_state(self) -> dict[str, Any]:
        return read_json(self.runtime_path / "state.json")

    def _validate_cross_domain_state(self) -> None:
        kernel = self.kernel.snapshot()
        body = self.body_store.read_manifest(kernel.head)
        if body.root != kernel.root:
            raise IntegrityError("current body Root does not match kernel Root")

        records = self.evidence.records()
        if not records:
            raise IntegrityError("evidence ledger has no Genesis record")
        if any(record.root_commitment != kernel.root for record in records):
            raise IntegrityError("evidence ledger contains a conflicting Root")
        if records[-1].head_after != kernel.head:
            raise IntegrityError("kernel Head does not match latest witnessed Head")

        expected_authority = "on"
        for record in records:
            if record.event_kind == "host_off":
                expected_authority = "off"
            elif record.event_kind == "host_on":
                expected_authority = "on"
        if kernel.authority != expected_authority:
            raise IntegrityError(
                "kernel authority does not match latest witnessed authority"
            )

    @staticmethod
    def _validate_runtime_state(state: Mapping[str, Any]) -> None:
        if state.get("schema_version") != RUNTIME_SCHEMA_VERSION:
            raise IntegrityError("invalid runtime state schema")
        if not isinstance(state.get("active_sessions"), dict):
            raise IntegrityError("invalid runtime active session state")

    def _update_session(self, *, session_id: str, value: dict[str, Any]) -> None:
        with ExclusiveFileLock(self.runtime_path / ".runtime.lock"):
            state = self._read_runtime_state()
            self._validate_runtime_state(state)
            sessions = dict(state["active_sessions"])
            sessions[session_id] = value
            state["active_sessions"] = sessions
            state["updated_at"] = utc_now()
            atomic_write_json(self.runtime_path / "state.json", state)

    def _remove_session(self, session_id: str) -> dict[str, Any] | None:
        with ExclusiveFileLock(self.runtime_path / ".runtime.lock"):
            state = self._read_runtime_state()
            self._validate_runtime_state(state)
            sessions = dict(state["active_sessions"])
            previous = sessions.pop(session_id, None)
            state["active_sessions"] = sessions
            state["updated_at"] = utc_now()
            atomic_write_json(self.runtime_path / "state.json", state)
        return previous

    def _clear_sessions(self) -> None:
        with ExclusiveFileLock(self.runtime_path / ".runtime.lock"):
            state = self._read_runtime_state()
            self._validate_runtime_state(state)
            state["active_sessions"] = {}
            state["updated_at"] = utc_now()
            atomic_write_json(self.runtime_path / "state.json", state)
