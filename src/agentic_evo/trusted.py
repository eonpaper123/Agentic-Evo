from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Iterator, Mapping
from uuid import uuid4

from ._util import (
    atomic_write_bytes,
    canonical_json_bytes,
    sha256_hex,
    utc_now,
)
from .body import BodyManifest
from .errors import (
    AuthorityError,
    GenesisExistsError,
    HeadConflictError,
    IntegrityError,
    RootBindingError,
    RuntimeOffError,
)
from .evidence import EVIDENCE_SCHEMA_VERSION, EvidenceLedger, EvidenceRecord
from .kernel import KernelSnapshot


TRUSTED_SCHEMA_VERSION = "agentic-evo-trusted-v2"
CHECKPOINT_SCHEMA_VERSION = "agentic-evo-checkpoint-v1"
SESSIONS_SCHEMA_VERSION = "agentic-evo-sessions-v2"
_AUTHORITY_OFF_EVENT_KINDS = frozenset({"host_off", "control_rehearsal_off"})
SessionKey = tuple[str, str]


def _session_commitment(
    sessions: Mapping[SessionKey, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SESSIONS_SCHEMA_VERSION,
        "sessions": [
            {
                "execution_surface": execution_surface,
                "session_id": session_id,
                "value": dict(value),
            }
            for (execution_surface, session_id), value in sorted(sessions.items())
        ],
    }


def _session_commitment_hash(
    sessions: Mapping[SessionKey, Mapping[str, Any]],
) -> str:
    return sha256_hex(canonical_json_bytes(_session_commitment(sessions)))


class TrustedState:
    """One local transaction domain for identity, lineage, sessions, and evidence."""

    def __init__(
        self,
        path: Path,
        *,
        key: bytes,
        instrument_version: str,
        protocol_version: str,
    ) -> None:
        self.path = Path(path)
        self.db_path = self.path / "state.sqlite3"
        self._key = key
        self.instrument_version = instrument_version
        self.protocol_version = protocol_version

    @staticmethod
    def generate_root() -> str:
        return secrets.token_hex(32)

    @classmethod
    def genesis(
        cls,
        path: Path,
        *,
        host_binding: str,
        purpose_anchor: str,
        root: str,
        initial_head: str,
        instrument_version: str,
        protocol_version: str,
        genesis_payload: Mapping[str, Any],
    ) -> "TrustedState":
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        key = cls._load_or_create_key(path / "witness.key")
        trusted = cls(
            path,
            key=key,
            instrument_version=instrument_version,
            protocol_version=protocol_version,
        )
        trusted._ensure_schema()

        with trusted._write_transaction(verify=False) as connection:
            existing = connection.execute(
                "SELECT 1 FROM state WHERE id = 1"
            ).fetchone()
            if existing is not None:
                raise GenesisExistsError("runtime Genesis already exists")
            empty_sessions_hash = _session_commitment_hash({})
            state: dict[str, Any] = {
                "schema_version": TRUSTED_SCHEMA_VERSION,
                "who": sha256_hex(host_binding),
                "why": sha256_hex(purpose_anchor),
                "authority": "on",
                "root": root,
                "head": initial_head,
                "revision": 0,
                "instrument_version": instrument_version,
                "protocol_version": protocol_version,
                "last_event_sequence": 0,
                "last_event_hash": None,
                "checkpoint_sequence": 0,
                "checkpoint_hash": None,
                "sessions_hash": empty_sessions_hash,
            }
            connection.execute(
                """
                INSERT INTO state (
                    id, schema_version, who, why, authority, root, head,
                    revision, instrument_version, protocol_version,
                    last_event_sequence, last_event_hash,
                    checkpoint_sequence, checkpoint_hash, sessions_hash
                ) VALUES (
                    1, :schema_version, :who, :why, :authority, :root, :head,
                    :revision, :instrument_version, :protocol_version,
                    :last_event_sequence, :last_event_hash,
                    :checkpoint_sequence, :checkpoint_hash, :sessions_hash
                )
                """,
                state,
            )
            trusted._finish_transition(
                connection,
                state,
                event_kind="genesis",
                head_before=None,
                head_after=initial_head,
                source_kind="research_instrument",
                author_kind="research_instrument",
                payload=genesis_payload,
            )
            trusted._verify_connection(connection)

        trusted.verify()
        return trusted

    @classmethod
    def load(cls, path: Path) -> "TrustedState":
        path = Path(path)
        key = cls._read_key(path / "witness.key")
        db_path = path / "state.sqlite3"
        if not db_path.is_file():
            raise IntegrityError("trusted state database is unavailable")
        try:
            with closing(cls._connect_path(db_path)) as connection:
                row = connection.execute(
                    """
                    SELECT instrument_version, protocol_version
                    FROM state
                    WHERE id = 1
                    """
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise IntegrityError("cannot read trusted state metadata") from exc
        if row is None or not all(
            isinstance(value, str) and value for value in row
        ):
            raise IntegrityError("trusted state has no committed Genesis")
        trusted = cls(
            path,
            key=key,
            instrument_version=str(row[0]),
            protocol_version=str(row[1]),
        )
        trusted._ensure_schema()
        trusted.verify()
        return trusted

    @classmethod
    def has_genesis(cls, path: Path) -> bool:
        db_path = Path(path) / "state.sqlite3"
        if not db_path.exists():
            return False
        try:
            with closing(cls._connect_path(db_path)) as connection:
                table = connection.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'state'
                    """
                ).fetchone()
                if table is None:
                    return False
                row = connection.execute(
                    "SELECT 1 FROM state WHERE id = 1"
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise IntegrityError("cannot inspect trusted Genesis state") from exc
        if row is None:
            return False
        cls.load(path)
        return True

    def current(self) -> tuple[KernelSnapshot, dict[SessionKey, dict[str, Any]]]:
        with self._read_transaction() as connection:
            state, sessions = self._verify_current_connection(connection)
        return self._snapshot_from_state(state), sessions

    def snapshot(self) -> KernelSnapshot:
        return self.current()[0]

    def last_event_sequence(self) -> int:
        with self._read_transaction() as connection:
            state = self._read_state(connection)
        return int(state["last_event_sequence"])

    def gate(self) -> KernelSnapshot:
        snapshot = self.snapshot()
        if not snapshot.is_on:
            raise RuntimeOffError("runtime is off")
        return snapshot

    def records(self) -> tuple[EvidenceRecord, ...]:
        with self._read_transaction() as connection:
            _, records, _ = self._verify_connection(connection)
        return tuple(records)

    def recent_records(
        self,
        *,
        execution_surface: str,
        excluded_session_id: str,
        event_kinds: frozenset[str],
        limit: int,
        before_sequence: int | None = None,
    ) -> tuple[tuple[EvidenceRecord, ...], bool]:
        """Read a bounded recent projection from the already verified ledger."""

        placeholders = ", ".join("?" for _ in event_kinds)
        before_clause = "" if before_sequence is None else "AND e.sequence < ?"
        query = f"""
            SELECT e.sequence, e.event_id, e.event_kind, e.record_json
            FROM events AS e
            LEFT JOIN event_projection AS p ON p.sequence = e.sequence
            WHERE (
                    p.sequence IS NULL
                    OR NOT (p.execution_surface = ? AND p.session_id = ?)
                  )
              AND e.event_kind IN ({placeholders})
              {before_clause}
            ORDER BY e.sequence DESC
            LIMIT ?
        """
        parameters: tuple[Any, ...] = (
            execution_surface,
            excluded_session_id,
            *sorted(event_kinds),
        )
        if before_sequence is not None:
            parameters += (before_sequence,)
        parameters += (limit + 1,)
        with self._read_transaction() as connection:
            state = self._read_state(connection)
            self._require_on(state)
            active = connection.execute(
                """
                SELECT 1 FROM sessions
                WHERE execution_surface = ? AND session_id = ?
                """,
                (execution_surface, excluded_session_id),
            ).fetchone()
            if active is None:
                raise AuthorityError("experience recall requires an active surface session")
            rows = connection.execute(query, parameters).fetchall()

        records: list[EvidenceRecord] = []
        for row in rows[:limit]:
            record = self._record_from_event_row(row, state)
            if (
                (
                    record.execution_surface,
                    record.session_id,
                ) == (execution_surface, excluded_session_id)
            ):
                raise IntegrityError("recent evidence row is inconsistent")
            records.append(record)
        records.reverse()
        return tuple(records), len(rows) > limit

    def current_body_lineage_records(
        self,
        *,
        expected_head: str,
    ) -> tuple[EvidenceRecord | None, EvidenceRecord | None]:
        """Read recorded lineage facts for exactly the Current Head."""

        with self._read_transaction() as connection:
            state = self._read_state(connection)
            self._require_on(state)
            if state["head"] != expected_head:
                raise HeadConflictError("Head changed before lineage facts were read")
            advanced_row = connection.execute(
                """
                SELECT sequence, event_id, event_kind, record_json
                FROM events
                WHERE event_kind = 'head_advanced'
                ORDER BY sequence DESC
                LIMIT 1
                """
            ).fetchone()
            resolution_row = connection.execute(
                """
                SELECT sequence, event_id, event_kind, record_json
                FROM events
                WHERE event_kind = 'body_development_action'
                  AND json_extract(record_json, '$.payload.action')
                      IN ('retain', 'withdraw')
                  AND json_extract(record_json, '$.payload.candidate_head') = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (expected_head,),
            ).fetchone()

        advanced = (
            None
            if advanced_row is None
            else self._record_from_event_row(advanced_row, state)
        )
        if advanced is not None and advanced.head_after != expected_head:
            raise IntegrityError("latest Head advance does not describe Current Head")
        resolution = (
            None
            if resolution_row is None
            else self._record_from_event_row(resolution_row, state)
        )
        if resolution is not None and (
            resolution.payload.get("action") not in {"retain", "withdraw"}
            or resolution.payload.get("candidate_head") != expected_head
        ):
            raise IntegrityError("recorded Body resolution is inconsistent")
        return advanced, resolution

    def session_value(
        self,
        *,
        execution_surface: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        self._require_session_identity(execution_surface, session_id)
        with self._read_transaction() as connection:
            row = connection.execute(
                """
                SELECT value_json FROM sessions
                WHERE execution_surface = ? AND session_id = ?
                """,
                (execution_surface, session_id),
            ).fetchone()
        if row is None:
            return None
        return self._decode_mapping(row["value_json"], "session")

    def authority_epoch(self) -> int:
        placeholders = ", ".join("?" for _ in _AUTHORITY_OFF_EVENT_KINDS)
        with self._read_transaction() as connection:
            state, _ = self._verify_current_connection(connection)
            row = connection.execute(
                f"""
                SELECT MAX(sequence)
                FROM events
                WHERE event_kind IN ({placeholders})
                """,
                tuple(sorted(_AUTHORITY_OFF_EVENT_KINDS)),
            ).fetchone()
        sequence = 0 if row is None or row[0] is None else int(row[0])
        if sequence < 0 or sequence > state["last_event_sequence"]:
            raise IntegrityError("invalid authority epoch")
        return sequence

    def verify(self) -> bool:
        with self._read_transaction() as connection:
            self._verify_connection(connection)
        return True

    def append(
        self,
        *,
        event_kind: str,
        root_commitment: str,
        head_before: str | None,
        head_after: str | None,
        source_kind: str,
        author_kind: str,
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
        human_intervention_kind: str | None = None,
        coverage_gap: str | None = None,
    ) -> EvidenceRecord:
        with self._write_transaction() as connection:
            state = self._read_state(connection)
            self._require_on(state)
            if root_commitment != state["root"]:
                raise RootBindingError("evidence Root does not match trusted Root")
            if head_before != state["head"] or head_after != state["head"]:
                raise HeadConflictError("evidence Head does not match trusted Head")
            return self._finish_transition(
                connection,
                state,
                event_kind=event_kind,
                head_before=head_before,
                head_after=head_after,
                source_kind=source_kind,
                author_kind=author_kind,
                payload=payload,
                occurred_at=occurred_at,
                execution_surface=execution_surface,
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                project_environment=project_environment,
                correlation_ref=correlation_ref,
                causation_ref=causation_ref,
                parent_ref=parent_ref,
                human_intervention_kind=human_intervention_kind,
                coverage_gap=coverage_gap,
            )

    def append_observation(
        self,
        *,
        event_kind: str,
        source_kind: str,
        author_kind: str,
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
    ) -> EvidenceRecord:
        """Append one observation while deriving Root and Head atomically."""

        with self._write_transaction() as connection:
            state = self._read_state(connection)
            self._require_on(state)
            return self._finish_transition(
                connection,
                state,
                event_kind=event_kind,
                head_before=state["head"],
                head_after=state["head"],
                source_kind=source_kind,
                author_kind=author_kind,
                payload=payload,
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
            )

    def start_session(
        self,
        *,
        expected_head: str,
        session_id: str,
        value: Mapping[str, Any],
        execution_surface: str,
        project_environment: str,
        model: str | None,
        body_generation: int,
        activation_kind: str,
        activation_artifact: str,
        activation_digest: str,
    ) -> EvidenceRecord:
        self._require_session_identity(execution_surface, session_id)
        if value.get("execution_surface") != execution_surface:
            raise IntegrityError(
                "trusted session value does not match its execution surface"
            )
        with self._write_transaction() as connection:
            state = self._read_state(connection)
            self._require_on(state)
            if state["head"] != expected_head:
                raise HeadConflictError("Head changed before session activation")
            connection.execute(
                """
                INSERT INTO sessions (
                    execution_surface, session_id, value_json
                )
                VALUES (?, ?, ?)
                ON CONFLICT(execution_surface, session_id)
                DO UPDATE SET value_json = excluded.value_json
                """,
                (
                    execution_surface,
                    session_id,
                    canonical_json_bytes(dict(value)),
                ),
            )
            state["sessions_hash"] = self._sessions_hash(connection)
            return self._finish_transition(
                connection,
                state,
                event_kind="session_start",
                head_before=expected_head,
                head_after=expected_head,
                source_kind="execution_surface",
                author_kind="surface_unverified",
                execution_surface=execution_surface,
                session_id=session_id,
                project_environment=project_environment,
                payload={
                    "body_generation": body_generation,
                    "model_ref": model,
                    "activation_kind": activation_kind,
                    "activation_artifact": activation_artifact,
                    "activation_digest": activation_digest,
                },
            )

    def end_session(
        self,
        *,
        execution_surface: str,
        session_id: str,
    ) -> EvidenceRecord:
        self._require_session_identity(execution_surface, session_id)
        with self._write_transaction() as connection:
            state = self._read_state(connection)
            self._require_on(state)
            row = connection.execute(
                """
                SELECT value_json
                FROM sessions
                WHERE execution_surface = ? AND session_id = ?
                """,
                (execution_surface, session_id),
            ).fetchone()
            session = self._decode_mapping(row[0], "session") if row else None
            connection.execute(
                """
                DELETE FROM sessions
                WHERE execution_surface = ? AND session_id = ?
                """,
                (execution_surface, session_id),
            )
            state["sessions_hash"] = self._sessions_hash(connection)
            return self._finish_transition(
                connection,
                state,
                event_kind="session_end",
                head_before=state["head"],
                head_after=state["head"],
                source_kind="execution_surface",
                author_kind="surface_unverified",
                execution_surface=execution_surface,
                session_id=session_id,
                project_environment=(
                    str(session.get("project_environment")) if session else None
                ),
                payload={"session_was_active": session is not None},
            )

    def detach_persisted_sessions(self) -> EvidenceRecord | None:
        """Detach sessions left by a prior Witness process in one transition."""

        with self._write_transaction() as connection:
            state = self._read_state(connection)
            sessions = self._read_sessions(connection)
            if not sessions:
                return None
            sessions_by_execution_surface: dict[str, int] = {}
            for execution_surface, _ in sessions:
                sessions_by_execution_surface[execution_surface] = (
                    sessions_by_execution_surface.get(execution_surface, 0) + 1
                )
            connection.execute("DELETE FROM sessions")
            state["sessions_hash"] = self._sessions_hash(connection)
            return self._finish_transition(
                connection,
                state,
                event_kind="surface_sessions_detached",
                head_before=state["head"],
                head_after=state["head"],
                source_kind="witness_service",
                author_kind="research_instrument",
                payload={
                    "session_count": len(sessions),
                    "sessions_by_execution_surface": (
                        sessions_by_execution_surface
                    ),
                },
            )

    def advance_head(
        self,
        *,
        expected_head: str,
        candidate: BodyManifest,
        author_kind: str,
        ingress_path: str,
        human_intervention_kind: str | None,
        execution_surface: str | None = None,
        session_id: str | None = None,
    ) -> EvidenceRecord:
        if (execution_surface is None) != (session_id is None):
            raise IntegrityError("Head advance session binding is incomplete")
        with self._write_transaction() as connection:
            state = self._read_state(connection)
            self._require_on(state)
            if execution_surface is not None and session_id is not None:
                self._require_session_identity(execution_surface, session_id)
                active = connection.execute(
                    """
                    SELECT 1 FROM sessions
                    WHERE execution_surface = ? AND session_id = ?
                    """,
                    (execution_surface, session_id),
                ).fetchone()
                if active is None:
                    raise AuthorityError(
                        "surface session ended before Head advance"
                    )
            if state["head"] != expected_head:
                raise HeadConflictError("Head changed before this transition")
            if candidate.root != state["root"]:
                raise RootBindingError("candidate body belongs to another Root")
            if candidate.parent_head != expected_head:
                raise HeadConflictError("candidate parent is not the expected Head")
            state["head"] = candidate.commitment
            return self._finish_transition(
                connection,
                state,
                event_kind="head_advanced",
                head_before=expected_head,
                head_after=candidate.commitment,
                source_kind="body",
                author_kind=author_kind,
                human_intervention_kind=human_intervention_kind,
                execution_surface=execution_surface,
                session_id=session_id,
                payload={
                    "body_generation": candidate.generation,
                    "parent_head": candidate.parent_head,
                    "ingress_path": ingress_path,
                    "operation": "advance_head",
                    "affected_domain": "body_lineage",
                },
            )

    def set_authority(
        self,
        *,
        authority: str,
        host_binding: str | None = None,
    ) -> EvidenceRecord:
        record = self._set_authority(
            authority=authority,
            host_binding=host_binding,
            event_kind=f"host_{authority}",
            source_kind="kernel",
            author_kind="normal_host_interaction",
            no_op_if_same=False,
        )
        if record is None:
            raise AssertionError("non-idempotent authority transition returned no record")
        return record

    def set_off_rehearsal(self) -> EvidenceRecord | None:
        return self._set_authority(
            authority="off",
            host_binding=None,
            event_kind="control_rehearsal_off",
            source_kind="host_control_rehearsal",
            author_kind="control_unverified",
            no_op_if_same=True,
        )

    def _set_authority(
        self,
        *,
        authority: str,
        host_binding: str | None,
        event_kind: str,
        source_kind: str,
        author_kind: str,
        no_op_if_same: bool,
    ) -> EvidenceRecord | None:
        if authority not in {"on", "off"}:
            raise IntegrityError("invalid trusted authority state")
        with self._write_transaction() as connection:
            state = self._read_state(connection)
            if no_op_if_same and state["authority"] == authority:
                return None
            if authority == "on":
                if host_binding is None or not hmac.compare_digest(
                    state["who"],
                    sha256_hex(host_binding),
                ):
                    raise AuthorityError(
                        "host binding does not match trusted authority"
                    )
            state["authority"] = authority
            if authority == "off":
                connection.execute("DELETE FROM sessions")
                state["sessions_hash"] = self._sessions_hash(connection)
            return self._finish_transition(
                connection,
                state,
                event_kind=event_kind,
                head_before=state["head"],
                head_after=state["head"],
                source_kind=source_kind,
                author_kind=author_kind,
                payload={},
            )

    def _finish_transition(
        self,
        connection: sqlite3.Connection,
        state: dict[str, Any],
        *,
        event_kind: str,
        head_before: str | None,
        head_after: str | None,
        source_kind: str,
        author_kind: str,
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
        human_intervention_kind: str | None = None,
        coverage_gap: str | None = None,
    ) -> EvidenceRecord:
        if head_after != state["head"]:
            raise HeadConflictError("transition result does not match trusted Head")
        clean_payload = dict(payload)
        EvidenceLedger._validate_payload(clean_payload)
        if not all(
            isinstance(value, str) and value
            for value in (event_kind, state["root"], source_kind, author_kind)
        ):
            raise IntegrityError("required evidence envelope fields are missing")

        sequence = int(state["last_event_sequence"]) + 1
        unsigned: dict[str, Any] = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "event_id": uuid4().hex,
            "sequence": sequence,
            "instrument_version": state["instrument_version"],
            "protocol_version": state["protocol_version"],
            "event_kind": event_kind,
            "occurred_at": utc_now() if occurred_at is None else occurred_at,
            "observed_at": utc_now(),
            "root_commitment": state["root"],
            "head_before": head_before,
            "head_after": head_after,
            "source_kind": source_kind,
            "author_kind": author_kind,
            "execution_surface": execution_surface,
            "session_id": session_id,
            "turn_id": turn_id,
            "tool_call_id": tool_call_id,
            "project_environment": project_environment,
            "correlation_ref": correlation_ref,
            "causation_ref": causation_ref,
            "parent_ref": parent_ref,
            "human_intervention_kind": human_intervention_kind,
            "coverage_gap": coverage_gap,
            "payload": clean_payload,
            "previous_integrity_hash": state["last_event_hash"],
        }
        raw_record = dict(unsigned)
        raw_record["integrity_hash"] = sha256_hex(canonical_json_bytes(unsigned))
        connection.execute(
            """
            INSERT INTO events (
                sequence, event_id, event_kind, record_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                sequence,
                raw_record["event_id"],
                event_kind,
                canonical_json_bytes(raw_record),
            ),
        )
        if execution_surface is not None and session_id is not None:
            connection.execute(
                """
                INSERT INTO event_projection (
                    sequence, execution_surface, session_id
                ) VALUES (?, ?, ?)
                """,
                (sequence, execution_surface, session_id),
            )
        record = EvidenceLedger._from_dict(raw_record)

        state["revision"] = int(state["revision"]) + 1
        state["last_event_sequence"] = sequence
        state["last_event_hash"] = record.integrity_hash
        checkpoint_sequence, checkpoint_hash = self._insert_checkpoint(
            connection,
            state,
            record,
        )
        state["checkpoint_sequence"] = checkpoint_sequence
        state["checkpoint_hash"] = checkpoint_hash
        self._update_state(connection, state)
        return record

    def _insert_checkpoint(
        self,
        connection: sqlite3.Connection,
        state: Mapping[str, Any],
        event: EvidenceRecord,
    ) -> tuple[int, str]:
        sequence = int(state["checkpoint_sequence"]) + 1
        unsigned = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_seq": sequence,
            "previous_checkpoint_hash": state["checkpoint_hash"],
            "transition_id": event.event_id,
            "who": state["who"],
            "why": state["why"],
            "root": state["root"],
            "head": state["head"],
            "authority": state["authority"],
            "revision": state["revision"],
            "sessions_hash": state["sessions_hash"],
            "evidence_seq": event.sequence,
            "evidence_hash": event.integrity_hash,
            "created_at": utc_now(),
        }
        checkpoint_hash = sha256_hex(canonical_json_bytes(unsigned))
        record = dict(unsigned)
        record["checkpoint_hash"] = checkpoint_hash
        mac = hmac.new(
            self._key,
            canonical_json_bytes(record),
            hashlib.sha256,
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO checkpoints (
                sequence, record_json, checkpoint_mac
            ) VALUES (?, ?, ?)
            """,
            (sequence, canonical_json_bytes(record), mac),
        )
        return sequence, checkpoint_hash

    def _verify_connection(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[
        dict[str, Any],
        list[EvidenceRecord],
        dict[SessionKey, dict[str, Any]],
    ]:
        state = self._read_state(connection)
        records = self._read_records(connection)
        checkpoints = self._read_checkpoints(connection)
        sessions = self._read_sessions(connection)

        if not records or not checkpoints:
            raise IntegrityError("trusted state has no Genesis history")
        if len(records) != len(checkpoints):
            raise IntegrityError("evidence and checkpoint counts differ")
        if state["revision"] != len(records):
            raise IntegrityError("trusted revision does not match evidence history")
        if state["last_event_sequence"] != len(records):
            raise IntegrityError("trusted evidence tail sequence does not match")
        if state["checkpoint_sequence"] != len(checkpoints):
            raise IntegrityError("trusted checkpoint tail sequence does not match")
        if state["sessions_hash"] != _session_commitment_hash(sessions):
            raise IntegrityError("trusted session commitment mismatch")
        if state["authority"] == "off" and sessions:
            raise IntegrityError("off trusted state cannot retain active sessions")

        EvidenceLedger._verify_records(records)
        for record in records:
            if record.schema_version != EVIDENCE_SCHEMA_VERSION:
                raise IntegrityError("unsupported evidence record schema")
            if record.root_commitment != state["root"]:
                raise IntegrityError("evidence contains a conflicting Root")
            if record.instrument_version != state["instrument_version"]:
                raise IntegrityError("evidence instrument version mismatch")
            if record.protocol_version != state["protocol_version"]:
                raise IntegrityError("evidence protocol version mismatch")

        previous_checkpoint_hash: str | None = None
        for sequence, (record, checkpoint) in enumerate(
            zip(records, checkpoints),
            start=1,
        ):
            raw, row_mac = checkpoint
            if raw.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                raise IntegrityError("unsupported checkpoint schema")
            if raw.get("checkpoint_seq") != sequence:
                raise IntegrityError("checkpoint sequence is not contiguous")
            if raw.get("previous_checkpoint_hash") != previous_checkpoint_hash:
                raise IntegrityError("checkpoint previous hash does not match")
            claimed_hash = raw.get("checkpoint_hash")
            unsigned = dict(raw)
            unsigned.pop("checkpoint_hash", None)
            actual_hash = sha256_hex(canonical_json_bytes(unsigned))
            if (
                not isinstance(claimed_hash, str)
                or not hmac.compare_digest(claimed_hash, actual_hash)
            ):
                raise IntegrityError("checkpoint integrity mismatch")
            expected_mac = hmac.new(
                self._key,
                canonical_json_bytes(raw),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(row_mac, expected_mac):
                raise IntegrityError("checkpoint witness MAC mismatch")
            if raw.get("transition_id") != record.event_id:
                raise IntegrityError("checkpoint transition does not match evidence")
            if raw.get("evidence_seq") != record.sequence:
                raise IntegrityError("checkpoint evidence sequence mismatch")
            if raw.get("evidence_hash") != record.integrity_hash:
                raise IntegrityError("checkpoint evidence hash mismatch")
            if raw.get("root") != record.root_commitment:
                raise IntegrityError("checkpoint Root does not match evidence")
            if raw.get("head") != record.head_after:
                raise IntegrityError("checkpoint Head does not match evidence")
            if raw.get("revision") != sequence:
                raise IntegrityError("checkpoint revision is not contiguous")
            previous_checkpoint_hash = actual_hash

        tail_record = records[-1]
        tail_checkpoint = checkpoints[-1][0]
        if state["last_event_hash"] != tail_record.integrity_hash:
            raise IntegrityError("trusted evidence tail hash does not match")
        if state["head"] != tail_record.head_after:
            raise IntegrityError("trusted Head does not match latest evidence")
        expected_tail = {
            "who": state["who"],
            "why": state["why"],
            "root": state["root"],
            "head": state["head"],
            "authority": state["authority"],
            "revision": state["revision"],
            "sessions_hash": state["sessions_hash"],
            "evidence_seq": state["last_event_sequence"],
            "evidence_hash": state["last_event_hash"],
            "checkpoint_seq": state["checkpoint_sequence"],
            "checkpoint_hash": state["checkpoint_hash"],
        }
        if any(tail_checkpoint.get(key) != value for key, value in expected_tail.items()):
            raise IntegrityError("trusted state does not match checkpoint tail")
        return state, records, sessions

    def _verify_current_connection(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[dict[str, Any], dict[SessionKey, dict[str, Any]]]:
        """Verify the live state and committed tail without rescanning history."""

        state = self._read_state(connection)
        sessions = self._read_sessions(connection)
        if state["sessions_hash"] != _session_commitment_hash(sessions):
            raise IntegrityError("trusted session commitment mismatch")
        if state["authority"] == "off" and sessions:
            raise IntegrityError("off trusted state cannot retain active sessions")

        event_row = connection.execute(
            """
            SELECT sequence, event_id, event_kind, record_json
            FROM events
            ORDER BY sequence DESC
            LIMIT 1
            """
        ).fetchone()
        checkpoint_row = connection.execute(
            """
            SELECT sequence, record_json, checkpoint_mac
            FROM checkpoints
            ORDER BY sequence DESC
            LIMIT 1
            """
        ).fetchone()
        if event_row is None or checkpoint_row is None:
            raise IntegrityError("trusted state has no Genesis history")

        record = self._record_from_event_row(event_row, state)
        if (
            record.schema_version != EVIDENCE_SCHEMA_VERSION
            or record.sequence != state["revision"]
            or record.sequence != state["last_event_sequence"]
            or record.integrity_hash != state["last_event_hash"]
            or record.head_after != state["head"]
        ):
            raise IntegrityError("trusted state does not match evidence tail")
        record_value = EvidenceLedger._to_dict(record)
        claimed_record_hash = record_value.pop("integrity_hash")
        if claimed_record_hash != sha256_hex(canonical_json_bytes(record_value)):
            raise IntegrityError("trusted evidence tail hash does not match")

        checkpoint = self._decode_mapping(
            checkpoint_row["record_json"],
            "checkpoint",
        )
        row_mac = checkpoint_row["checkpoint_mac"]
        if not isinstance(row_mac, str):
            raise IntegrityError("invalid checkpoint row")
        checkpoint_sequence = checkpoint_row["sequence"]
        if (
            checkpoint_sequence != state["checkpoint_sequence"]
            or checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
            or checkpoint.get("checkpoint_seq") != checkpoint_sequence
        ):
            raise IntegrityError("trusted state does not match checkpoint tail")
        claimed_checkpoint_hash = checkpoint.get("checkpoint_hash")
        unsigned_checkpoint = dict(checkpoint)
        unsigned_checkpoint.pop("checkpoint_hash", None)
        actual_checkpoint_hash = sha256_hex(
            canonical_json_bytes(unsigned_checkpoint)
        )
        expected_mac = hmac.new(
            self._key,
            canonical_json_bytes(checkpoint),
            hashlib.sha256,
        ).hexdigest()
        if (
            not isinstance(claimed_checkpoint_hash, str)
            or not hmac.compare_digest(
                claimed_checkpoint_hash,
                actual_checkpoint_hash,
            )
            or not hmac.compare_digest(row_mac, expected_mac)
        ):
            raise IntegrityError("checkpoint tail integrity mismatch")
        expected_checkpoint = {
            "transition_id": record.event_id,
            "who": state["who"],
            "why": state["why"],
            "root": state["root"],
            "head": state["head"],
            "authority": state["authority"],
            "revision": state["revision"],
            "sessions_hash": state["sessions_hash"],
            "evidence_seq": record.sequence,
            "evidence_hash": record.integrity_hash,
            "checkpoint_seq": state["checkpoint_sequence"],
            "checkpoint_hash": state["checkpoint_hash"],
        }
        if any(
            checkpoint.get(key) != value
            for key, value in expected_checkpoint.items()
        ):
            raise IntegrityError("trusted state does not match checkpoint tail")
        return state, sessions

    def _read_records(self, connection: sqlite3.Connection) -> list[EvidenceRecord]:
        records: list[EvidenceRecord] = []
        try:
            rows = connection.execute(
                """
                SELECT sequence, event_id, event_kind, record_json
                FROM events
                ORDER BY sequence
                """
            ).fetchall()
            for row in rows:
                raw = self._decode_mapping(row["record_json"], "evidence record")
                record = EvidenceLedger._from_dict(raw)
                if (
                    row["sequence"] != record.sequence
                    or row["event_id"] != record.event_id
                    or row["event_kind"] != record.event_kind
                ):
                    raise IntegrityError("evidence row does not match record")
                records.append(record)
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrityError("invalid evidence record") from exc
        return records

    def _record_from_event_row(
        self,
        row: sqlite3.Row,
        state: Mapping[str, Any],
    ) -> EvidenceRecord:
        raw = self._decode_mapping(row["record_json"], "evidence record")
        record = EvidenceLedger._from_dict(raw)
        if (
            row["sequence"] != record.sequence
            or row["event_id"] != record.event_id
            or row["event_kind"] != record.event_kind
            or record.root_commitment != state["root"]
            or record.instrument_version != state["instrument_version"]
            or record.protocol_version != state["protocol_version"]
            or record.sequence > state["last_event_sequence"]
        ):
            raise IntegrityError("evidence row is inconsistent")
        return record

    def _read_checkpoints(
        self,
        connection: sqlite3.Connection,
    ) -> list[tuple[dict[str, Any], str]]:
        checkpoints: list[tuple[dict[str, Any], str]] = []
        rows = connection.execute(
            """
            SELECT sequence, record_json, checkpoint_mac
            FROM checkpoints
            ORDER BY sequence
            """
        ).fetchall()
        for expected, row in enumerate(rows, start=1):
            if row["sequence"] != expected:
                raise IntegrityError("checkpoint row sequence is not contiguous")
            raw = self._decode_mapping(row["record_json"], "checkpoint")
            row_mac = row["checkpoint_mac"]
            if not isinstance(row_mac, str):
                raise IntegrityError("invalid checkpoint row")
            checkpoints.append((raw, row_mac))
        return checkpoints

    def _read_sessions(
        self,
        connection: sqlite3.Connection,
    ) -> dict[SessionKey, dict[str, Any]]:
        sessions: dict[SessionKey, dict[str, Any]] = {}
        rows = connection.execute(
            """
            SELECT execution_surface, session_id, value_json
            FROM sessions
            ORDER BY execution_surface, session_id
            """
        ).fetchall()
        for row in rows:
            execution_surface = row["execution_surface"]
            session_id = row["session_id"]
            self._require_session_identity(execution_surface, session_id)
            value = self._decode_mapping(
                row["value_json"],
                "session",
            )
            if value.get("execution_surface") != execution_surface:
                raise IntegrityError(
                    "trusted session value does not match its execution surface"
                )
            sessions[(execution_surface, session_id)] = value
        return sessions

    def _sessions_hash(self, connection: sqlite3.Connection) -> str:
        return _session_commitment_hash(self._read_sessions(connection))

    @staticmethod
    def _require_session_identity(
        execution_surface: Any,
        session_id: Any,
    ) -> None:
        if not isinstance(execution_surface, str) or not execution_surface:
            raise IntegrityError("invalid trusted execution surface")
        if not isinstance(session_id, str) or not session_id:
            raise IntegrityError("invalid trusted session id")

    @staticmethod
    def _decode_mapping(raw: Any, kind: str) -> dict[str, Any]:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise IntegrityError(f"invalid {kind} JSON") from exc
        if not isinstance(value, dict):
            raise IntegrityError(f"invalid {kind} value")
        return value

    @staticmethod
    def _snapshot_from_state(state: Mapping[str, Any]) -> KernelSnapshot:
        return KernelSnapshot(
            who=str(state["who"]),
            why=str(state["why"]),
            authority=str(state["authority"]),
            root=str(state["root"]),
            head=str(state["head"]),
        )

    @staticmethod
    def _require_on(state: Mapping[str, Any]) -> None:
        if state["authority"] != "on":
            raise RuntimeOffError("runtime is off")

    def _read_state(self, connection: sqlite3.Connection) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM state WHERE id = 1"
        ).fetchone()
        if row is None:
            raise IntegrityError("trusted state has no committed Genesis")
        state = dict(row)
        if state.get("schema_version") != TRUSTED_SCHEMA_VERSION:
            raise IntegrityError("unsupported trusted state schema")
        if state.get("authority") not in {"on", "off"}:
            raise IntegrityError("invalid trusted authority state")
        for field in (
            "who",
            "why",
            "root",
            "head",
            "instrument_version",
            "protocol_version",
            "sessions_hash",
        ):
            if not isinstance(state.get(field), str) or not state[field]:
                raise IntegrityError(f"invalid trusted state field: {field}")
        for field in (
            "revision",
            "last_event_sequence",
            "checkpoint_sequence",
        ):
            if not isinstance(state.get(field), int) or state[field] < 0:
                raise IntegrityError(f"invalid trusted state counter: {field}")
        for field in ("last_event_hash", "checkpoint_hash"):
            if state[field] is not None and not isinstance(state[field], str):
                raise IntegrityError(f"invalid trusted state hash: {field}")
        return state

    @staticmethod
    def _update_state(
        connection: sqlite3.Connection,
        state: Mapping[str, Any],
    ) -> None:
        result = connection.execute(
            """
            UPDATE state
            SET authority = :authority,
                head = :head,
                revision = :revision,
                last_event_sequence = :last_event_sequence,
                last_event_hash = :last_event_hash,
                checkpoint_sequence = :checkpoint_sequence,
                checkpoint_hash = :checkpoint_hash,
                sessions_hash = :sessions_hash
            WHERE id = 1
            """,
            state,
        )
        if result.rowcount != 1:
            raise IntegrityError("trusted singleton state is unavailable")

    def _ensure_schema(self) -> None:
        try:
            with closing(self._connect_path(self.db_path)) as connection:
                existing_sessions = connection.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'sessions'
                    """
                ).fetchone()
                if existing_sessions is not None:
                    self._require_sessions_schema(connection)
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        schema_version TEXT NOT NULL,
                        who TEXT NOT NULL,
                        why TEXT NOT NULL,
                        authority TEXT NOT NULL,
                        root TEXT NOT NULL,
                        head TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        instrument_version TEXT NOT NULL,
                        protocol_version TEXT NOT NULL,
                        last_event_sequence INTEGER NOT NULL,
                        last_event_hash TEXT,
                        checkpoint_sequence INTEGER NOT NULL,
                        checkpoint_hash TEXT,
                        sessions_hash TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        sequence INTEGER PRIMARY KEY,
                        event_id TEXT NOT NULL UNIQUE,
                        event_kind TEXT NOT NULL,
                        record_json BLOB NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS event_projection (
                        sequence INTEGER PRIMARY KEY,
                        execution_surface TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        FOREIGN KEY (sequence) REFERENCES events(sequence)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        execution_surface TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        value_json BLOB NOT NULL,
                        PRIMARY KEY (execution_surface, session_id)
                    ) WITHOUT ROWID
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS checkpoints (
                        sequence INTEGER PRIMARY KEY,
                        record_json BLOB NOT NULL,
                        checkpoint_mac TEXT NOT NULL
                    )
                    """
                )
                self._require_sessions_schema(connection)
        except sqlite3.DatabaseError as exc:
            raise IntegrityError("cannot initialize trusted state schema") from exc

    @staticmethod
    def _require_sessions_schema(connection: sqlite3.Connection) -> None:
        rows = connection.execute("PRAGMA table_info(sessions)").fetchall()
        layout = tuple(
            (
                row["name"],
                str(row["type"]).upper(),
                int(row["notnull"]),
                int(row["pk"]),
            )
            for row in rows
        )
        expected = (
            ("execution_surface", "TEXT", 1, 1),
            ("session_id", "TEXT", 1, 2),
            ("value_json", "BLOB", 1, 0),
        )
        if layout != expected:
            raise IntegrityError("incompatible trusted state schema")

    @contextmanager
    def _read_transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect_path(self.db_path)
        try:
            connection.execute("BEGIN")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @contextmanager
    def _write_transaction(
        self,
        *,
        verify: bool = True,
    ) -> Iterator[sqlite3.Connection]:
        connection = self._connect_path(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            if verify:
                self._verify_current_connection(connection)
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @staticmethod
    def _connect_path(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(
            path,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _load_or_create_key(path: Path) -> bytes:
        if path.exists():
            return TrustedState._read_key(path)
        key = secrets.token_bytes(32)
        atomic_write_bytes(path, key)
        return key

    @staticmethod
    def _read_key(path: Path) -> bytes:
        try:
            key = path.read_bytes()
        except OSError as exc:
            raise IntegrityError("trusted witness key is unavailable") from exc
        if len(key) != 32:
            raise IntegrityError("trusted witness key has an invalid length")
        return key
