from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4

from ._util import (
    ExclusiveFileLock,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    read_json,
    sha256_hex,
    utc_now,
)
from .errors import GenesisExistsError, IntegrityError, SensitiveContentError


EVIDENCE_SCHEMA_VERSION = "agentic-evo-evidence-v1"
MAX_PAYLOAD_BYTES = 16 * 1024
MAX_PAYLOAD_KEYS = 32
_SENSITIVE_KEY = re.compile(
    r"(access[_-]?key|authorization|chain[_-]?of[_-]?thought|cookie|credential|"
    r"environment[_-]?variables?|password|private[_-]?key|secret|session[_-]?token|"
    r"token)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EvidenceRecord:
    schema_version: str
    event_id: str
    sequence: int
    instrument_version: str
    protocol_version: str
    event_kind: str
    occurred_at: str
    observed_at: str
    root_commitment: str
    head_before: str | None
    head_after: str | None
    source_kind: str
    author_kind: str
    execution_surface: str | None
    session_id: str | None
    turn_id: str | None
    tool_call_id: str | None
    project_environment: str | None
    correlation_ref: str | None
    causation_ref: str | None
    parent_ref: str | None
    human_intervention_kind: str | None
    coverage_gap: str | None
    payload: dict[str, Any]
    previous_integrity_hash: str | None
    integrity_hash: str


class EvidenceLedger:
    """Append-only, hash-chained research evidence separate from the body store."""

    def __init__(
        self,
        path: Path,
        *,
        instrument_version: str,
        protocol_version: str,
    ) -> None:
        self.path = Path(path)
        self.instrument_version = instrument_version
        self.protocol_version = protocol_version
        self.log_path = self.path / "events.jsonl"

    @classmethod
    def create(
        cls,
        path: Path,
        *,
        instrument_version: str,
        protocol_version: str,
    ) -> "EvidenceLedger":
        path = Path(path)
        meta_path = path / "meta.json"
        if meta_path.exists() or (path / "events.jsonl").exists():
            raise GenesisExistsError("evidence ledger already exists")
        path.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            meta_path,
            {
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "instrument_version": instrument_version,
                "protocol_version": protocol_version,
                "created_at": utc_now(),
            },
        )
        atomic_write_bytes(path / "events.jsonl", b"")
        return cls(
            path,
            instrument_version=instrument_version,
            protocol_version=protocol_version,
        )

    @classmethod
    def load(cls, path: Path) -> "EvidenceLedger":
        path = Path(path)
        meta = read_json(path / "meta.json")
        if meta.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
            raise IntegrityError("unsupported evidence ledger schema")
        instrument = meta.get("instrument_version")
        protocol = meta.get("protocol_version")
        if not isinstance(instrument, str) or not isinstance(protocol, str):
            raise IntegrityError("invalid evidence ledger metadata")
        if not (path / "events.jsonl").is_file():
            raise IntegrityError("evidence log is missing")
        return cls(
            path,
            instrument_version=instrument,
            protocol_version=protocol,
        )

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
        clean_payload = dict(payload)
        self._validate_payload(clean_payload)
        if not all(
            isinstance(value, str) and value
            for value in (event_kind, root_commitment, source_kind, author_kind)
        ):
            raise IntegrityError("required evidence envelope fields are missing")

        with ExclusiveFileLock(self.path / ".evidence.lock"):
            existing = self._read_records_unverified()
            self._verify_records(existing)
            previous = existing[-1] if existing else None
            unsigned: dict[str, Any] = {
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "event_id": uuid4().hex,
                "sequence": len(existing) + 1,
                "instrument_version": self.instrument_version,
                "protocol_version": self.protocol_version,
                "event_kind": event_kind,
                "occurred_at": utc_now() if occurred_at is None else occurred_at,
                "observed_at": utc_now(),
                "root_commitment": root_commitment,
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
                "previous_integrity_hash": (
                    previous.integrity_hash if previous is not None else None
                ),
            }
            record = dict(unsigned)
            record["integrity_hash"] = sha256_hex(canonical_json_bytes(unsigned))
            with self.log_path.open("ab") as handle:
                handle.write(canonical_json_bytes(record) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        return self._from_dict(record)

    def records(self) -> tuple[EvidenceRecord, ...]:
        records = self._read_records_unverified()
        self._verify_records(records)
        return tuple(records)

    def verify(self) -> bool:
        self._verify_records(self._read_records_unverified())
        return True

    @staticmethod
    def _verify_records(records: list[EvidenceRecord]) -> None:
        previous: str | None = None
        for expected_sequence, record in enumerate(records, start=1):
            if record.sequence != expected_sequence:
                raise IntegrityError("evidence sequence is not contiguous")
            if record.previous_integrity_hash != previous:
                raise IntegrityError("evidence previous hash does not match")
            value = EvidenceLedger._to_dict(record)
            claimed = value.pop("integrity_hash")
            actual = sha256_hex(canonical_json_bytes(value))
            if claimed != actual:
                raise IntegrityError("evidence record integrity mismatch")
            previous = record.integrity_hash

    def _read_records_unverified(self) -> list[EvidenceRecord]:
        records: list[EvidenceRecord] = []
        try:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise IntegrityError("cannot read evidence log") from exc
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise TypeError("record is not an object")
                records.append(self._from_dict(raw))
            except (ValueError, TypeError, KeyError) as exc:
                raise IntegrityError(
                    f"invalid evidence record at line {line_number}"
                ) from exc
        return records

    @staticmethod
    def _from_dict(raw: Mapping[str, Any]) -> EvidenceRecord:
        return EvidenceRecord(
            schema_version=str(raw["schema_version"]),
            event_id=str(raw["event_id"]),
            sequence=int(raw["sequence"]),
            instrument_version=str(raw["instrument_version"]),
            protocol_version=str(raw["protocol_version"]),
            event_kind=str(raw["event_kind"]),
            occurred_at=str(raw["occurred_at"]),
            observed_at=str(raw["observed_at"]),
            root_commitment=str(raw["root_commitment"]),
            head_before=raw.get("head_before"),
            head_after=raw.get("head_after"),
            source_kind=str(raw["source_kind"]),
            author_kind=str(raw["author_kind"]),
            execution_surface=raw.get("execution_surface"),
            session_id=raw.get("session_id"),
            turn_id=raw.get("turn_id"),
            tool_call_id=raw.get("tool_call_id"),
            project_environment=raw.get("project_environment"),
            correlation_ref=raw.get("correlation_ref"),
            causation_ref=raw.get("causation_ref"),
            parent_ref=raw.get("parent_ref"),
            human_intervention_kind=raw.get("human_intervention_kind"),
            coverage_gap=raw.get("coverage_gap"),
            payload=dict(raw.get("payload") or {}),
            previous_integrity_hash=raw.get("previous_integrity_hash"),
            integrity_hash=str(raw["integrity_hash"]),
        )

    @staticmethod
    def _to_dict(record: EvidenceRecord) -> dict[str, Any]:
        return {
            field: getattr(record, field)
            for field in EvidenceRecord.__dataclass_fields__
        }

    @classmethod
    def _validate_payload(cls, value: Mapping[str, Any]) -> None:
        if len(value) > MAX_PAYLOAD_KEYS:
            raise IntegrityError("evidence payload has too many top-level keys")
        cls._reject_sensitive_keys(value)
        if len(canonical_json_bytes(value)) > MAX_PAYLOAD_BYTES:
            raise IntegrityError("evidence payload exceeds its size boundary")

    @classmethod
    def _reject_sensitive_keys(cls, value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if _SENSITIVE_KEY.search(str(key)):
                    raise SensitiveContentError(
                        f"sensitive evidence payload key: {key}"
                    )
                cls._reject_sensitive_keys(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                cls._reject_sensitive_keys(item)
