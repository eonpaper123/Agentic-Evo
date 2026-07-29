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


TRUSTED_SCHEMA_VERSION = "agentic-evo-trusted-v1"
CHECKPOINT_SCHEMA_VERSION = "agentic-evo-checkpoint-v1"


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
            empty_sessions_hash = sha256_hex(canonical_json_bytes({}))
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

    def current(self) -> tuple[KernelSnapshot, dict[str, dict[str, Any]]]:
        with self._read_transaction() as connection:
            state, _, sessions = self._verify_connection(connection)
        return self._snapshot_from_state(state), sessions

    def snapshot(self) -> KernelSnapshot:
        return self.current()[0]

    def gate(self) -> KernelSnapshot:
        snapshot = self.snapshot()
        if not snapshot.is_on:
            raise RuntimeOffError("runtime is off")
        return snapshot

    def records(self) -> tuple[EvidenceRecord, ...]:
        with self._read_transaction() as connection:
            _, records, _ = self._verify_connection(connection)
        return tuple(records)

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
        with self._write_transaction() as connection:
            state = self._read_state(connection)
            self._require_on(state)
            if state["head"] != expected_head:
                raise HeadConflictError("Head changed before session activation")
            connection.execute(
                """
                INSERT INTO sessions (session_id, value_json)
                VALUES (?, ?)
                ON CONFLICT(session_id) DO UPDATE SET value_json = excluded.value_json
                """,
                (session_id, canonical_json_bytes(dict(value))),
            )
            state["sessions_hash"] = self._sessions_hash(connection)
            return self._finish_transition(
                connection,
                state,
                event_kind="session_start",
                head_before=expected_head,
                head_after=expected_head,
                source_kind="execution_surface",
                author_kind="normal_host_interaction",
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

    def end_session(self, *, session_id: str) -> EvidenceRecord:
        with self._write_transaction() as connection:
            state = self._read_state(connection)
            self._require_on(state)
            row = connection.execute(
                "SELECT value_json FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            session = self._decode_mapping(row[0], "session") if row else None
            connection.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            state["sessions_hash"] = self._sessions_hash(connection)
            return self._finish_transition(
                connection,
                state,
                event_kind="session_end",
                head_before=state["head"],
                head_after=state["head"],
                source_kind="execution_surface",
                author_kind="normal_host_interaction",
                execution_surface=(
                    str(session.get("execution_surface")) if session else None
                ),
                session_id=session_id,
                project_environment=(
                    str(session.get("project_environment")) if session else None
                ),
                payload={"session_was_active": session is not None},
            )

    def advance_head(
        self,
        *,
        expected_head: str,
        candidate: BodyManifest,
        human_intervention_kind: str | None,
    ) -> EvidenceRecord:
        with self._write_transaction() as connection:
            state = self._read_state(connection)
            self._require_on(state)
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
                author_kind=candidate.author_kind,
                human_intervention_kind=human_intervention_kind,
                payload={
                    "body_generation": candidate.generation,
                    "parent_head": candidate.parent_head,
                },
            )

    def set_authority(
        self,
        *,
        authority: str,
        host_binding: str | None = None,
    ) -> EvidenceRecord:
        if authority not in {"on", "off"}:
            raise IntegrityError("invalid trusted authority state")
        with self._write_transaction() as connection:
            state = self._read_state(connection)
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
                event_kind=f"host_{authority}",
                head_before=state["head"],
                head_after=state["head"],
                source_kind="kernel",
                author_kind="normal_host_interaction",
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
            "occurred_at": occurred_at or utc_now(),
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
        dict[str, dict[str, Any]],
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
        if state["sessions_hash"] != sha256_hex(canonical_json_bytes(sessions)):
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
    ) -> dict[str, dict[str, Any]]:
        sessions: dict[str, dict[str, Any]] = {}
        rows = connection.execute(
            "SELECT session_id, value_json FROM sessions ORDER BY session_id"
        ).fetchall()
        for row in rows:
            session_id = row["session_id"]
            if not isinstance(session_id, str) or not session_id:
                raise IntegrityError("invalid trusted session id")
            sessions[session_id] = self._decode_mapping(
                row["value_json"],
                "session",
            )
        return sessions

    def _sessions_hash(self, connection: sqlite3.Connection) -> str:
        return sha256_hex(canonical_json_bytes(self._read_sessions(connection)))

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
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        value_json BLOB NOT NULL
                    )
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
        except sqlite3.DatabaseError as exc:
            raise IntegrityError("cannot initialize trusted state schema") from exc

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
                self._verify_connection(connection)
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
