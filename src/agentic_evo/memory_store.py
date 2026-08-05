"""Body-owned CAMU memory store (v1.0 mechanism space).

This module establishes the *mechanism space* for Memory-to-Capability in
Agentic-Evo v1.0: an append-only, hash-chained store of CAMU records owned by
the Body (never by the Witness) and deliberately free of fixed recall,
forgetting, or learning algorithms.

CAMU contract -- each record m_i = <G, A, I, P, E>:

- G grounding: evidence_refs (list of {sequence, hash}, validated against the
  EvidenceRecord shape when present), provenance (body-authored text),
  collected_at (ISO-8601).
- A activation: a body-authored JSON predicate declaration (dict); NOT a fixed
  similarity algorithm. The evaluator is a pluggable hook.
- I influence: {domain: one of predict|context|tool_selection|plan|
  executable_skill|learning_goal|successor_generation, description: str};
  unknown domains are rejected.
- P prediction: {condition, expected, counterfactual, status} -- a falsifiable
  observable claim of what differs with vs without this memory. status starts
  as "pending" and is only changed by record_outcome / consolidate bookkeeping.
- E epistemic state: {uncertainty (0..1), support, oppose, conflicts, version,
  lineage, use_log}. use_log is append-only: record_use appends without
  rewriting history.

Store format: one append-only JSONL file (default
``<agent home>/memory/camus.jsonl``) whose first record is a meta header and
whose following records are either ``camu`` records (content-addressed) or
``camu_update`` records (use / outcome / status bookkeeping). Every line is
hash-chained (sha256 over the canonical JSON of the line minus
``integrity_hash``); ``verify_chain`` detects any tamper. No record is ever
rewritten: epistemic evidence accumulates as chained update lines, and
``get``/``list`` replay the chain to produce the effective record.

The store is caller-owned and pure stdlib. It must NOT import
runtime/body/kernel/trusted/witness: memory records are declarative and
replaceable, never part of the TCB. It may reference evidence by hash
(``evidence_refs``) for grounding but never imports or mutates trusted state.

Honest claim ceilings:

- This slice proves the mechanism space exists, is verifiable, and is
  Body-owned. It does NOT claim memory formation, causal capability gain,
  learning, self-evolution, or that any recall mechanism is "the" memory
  system.
- ``default_evaluator`` (recall) is an explicit placeholder; the Body may pass
  its own evaluator.
- ``consolidate`` is a status scaffold (pending + older than TTL -> "overdue");
  it is not a forgetting policy and never deletes.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping

from .errors import MemoryIntegrityError, MemoryRecordError


MEMORY_STORE_SCHEMA_VERSION = "agentic-evo-memory-store-v1"
CAMU_SCHEMA_VERSION = "agentic-evo-camu-v1"

CAMU_INFLUENCE_DOMAINS = frozenset(
    {
        "predict",
        "context",
        "tool_selection",
        "plan",
        "executable_skill",
        "learning_goal",
        "successor_generation",
    }
)
CAMU_PREDICTION_STATUSES = frozenset(
    {"pending", "verified", "contradicted", "overdue"}
)

DEFAULT_CONSOLIDATION_TTL_SECONDS = 7 * 24 * 60 * 60

MAX_JSONL_LINE_BYTES = 2 * 1024 * 1024
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

Evaluator = Callable[[Mapping[str, Any], Any], bool]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_hex(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _json_compatible(value: Any, field: str) -> Any:
    try:
        _canonical_json_bytes(value)
    except (TypeError, ValueError, RecursionError) as exc:
        raise MemoryRecordError(f"{field} must be JSON-compatible") from exc
    return value

# -- contract validation ------------------------------------------------------


def _non_empty_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise MemoryRecordError(f"{field} must be a non-empty string")
    return value


def _json_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MemoryRecordError(f"{field} must be a JSON object")
    return value


def _validate_evidence_refs(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise MemoryRecordError(
            "G.evidence_refs must be a list of {sequence, hash} objects"
        )
    for index, ref in enumerate(value):
        if not isinstance(ref, dict):
            raise MemoryRecordError(
                f"G.evidence_refs[{index}] must be an object"
            )
        sequence = ref.get("sequence")
        ref_hash = ref.get("hash")
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 1
        ):
            raise MemoryRecordError(
                f"G.evidence_refs[{index}].sequence must be a positive integer"
            )
        if not isinstance(ref_hash, str) or not _SHA256_HEX.fullmatch(ref_hash):
            raise MemoryRecordError(
                f"G.evidence_refs[{index}].hash must be a sha256 hex digest"
            )


def _validate_grounding(value: Any) -> dict[str, Any]:
    grounding = _json_object(value, "G")
    _validate_evidence_refs(grounding.get("evidence_refs"))
    if "provenance" not in grounding:
        raise MemoryRecordError("G.provenance is required")
    _non_empty_str(grounding["provenance"], "G.provenance")
    if "collected_at" not in grounding:
        raise MemoryRecordError("G.collected_at is required")
    collected = _non_empty_str(grounding["collected_at"], "G.collected_at")
    try:
        datetime.fromisoformat(collected)
    except ValueError as exc:
        raise MemoryRecordError(
            "G.collected_at must be an ISO-8601 timestamp"
        ) from exc
    return grounding


def _validate_activation(value: Any) -> dict[str, Any]:
    return _json_object(value, "A")


def _validate_influence(value: Any) -> dict[str, Any]:
    influence = _json_object(value, "I")
    if "domain" not in influence:
        raise MemoryRecordError("I.domain is required")
    domain = _non_empty_str(influence["domain"], "I.domain")
    if domain not in CAMU_INFLUENCE_DOMAINS:
        raise MemoryRecordError(f"unknown influence domain: {domain!r}")
    if "description" not in influence:
        raise MemoryRecordError("I.description is required")
    _non_empty_str(influence["description"], "I.description")
    return influence


def _validate_prediction(value: Any, *, initial: bool) -> dict[str, Any]:
    prediction = _json_object(value, "P")
    for field in ("condition", "expected", "counterfactual"):
        if field not in prediction:
            raise MemoryRecordError(f"P.{field} is required")
        _non_empty_str(prediction[field], f"P.{field}")
    status = prediction.get("status", "pending")
    if (
        not isinstance(status, str)
        or status not in CAMU_PREDICTION_STATUSES
    ):
        raise MemoryRecordError(
            f"P.status must be one of {sorted(CAMU_PREDICTION_STATUSES)}"
        )
    if initial and status != "pending":
        raise MemoryRecordError("P.status must be 'pending' when creating a CAMU")
    return prediction


def _validate_epistemic(value: Any) -> dict[str, Any]:
    epistemic = _json_object(value, "E")
    if "uncertainty" not in epistemic:
        raise MemoryRecordError("E.uncertainty is required")
    uncertainty = epistemic["uncertainty"]
    if isinstance(uncertainty, bool) or not isinstance(
        uncertainty, (int, float)
    ):
        raise MemoryRecordError("E.uncertainty must be a number in [0, 1]")
    if not 0.0 <= float(uncertainty) <= 1.0:
        raise MemoryRecordError("E.uncertainty must be a number in [0, 1]")
    for field in ("support", "oppose", "conflicts", "use_log"):
        if field in epistemic and not isinstance(epistemic[field], list):
            raise MemoryRecordError(f"E.{field} must be a list")
    if "version" in epistemic and (
        not isinstance(epistemic["version"], int)
        or isinstance(epistemic["version"], bool)
        or epistemic["version"] < 1
    ):
        raise MemoryRecordError("E.version must be an integer >= 1")
    lineage = epistemic.get("lineage")
    if lineage is not None and not isinstance(lineage, str):
        raise MemoryRecordError("E.lineage must be null or a CAMU id string")
    return epistemic


def _validate_camu_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one CAMU record; returns a canonical copy."""
    if not isinstance(record, Mapping):
        raise MemoryRecordError("CAMU record must be an object")
    for field in ("G", "A", "I", "P", "E"):
        if field not in record:
            raise MemoryRecordError(
                f"CAMU record is missing required field {field}"
            )
    unknown = set(record) - {"G", "A", "I", "P", "E"}
    if unknown:
        raise MemoryRecordError(
            f"CAMU record has unknown fields: {sorted(unknown)}"
        )
    normalized = copy.deepcopy(
        {
            "G": _validate_grounding(record["G"]),
            "A": _validate_activation(record["A"]),
            "I": _validate_influence(record["I"]),
            "P": _validate_prediction(record["P"], initial=True),
            "E": _validate_epistemic(record["E"]),
        }
    )
    normalized["G"].setdefault("evidence_refs", [])
    normalized["P"].setdefault("status", "pending")
    normalized["E"].setdefault("support", [])
    normalized["E"].setdefault("oppose", [])
    normalized["E"].setdefault("conflicts", [])
    normalized["E"].setdefault("version", 1)
    normalized["E"].setdefault("lineage", None)
    normalized["E"].setdefault("use_log", [])
    return normalized


# -- default recall evaluator (explicit placeholder) ---------------------------


def default_evaluator(record: Mapping[str, Any], context: Any) -> bool:
    """Trivial JSON-predicate matcher -- an explicit placeholder evaluator.

    A CAMU matches iff the context JSON object contains every key-value pair
    declared in the CAMU activation declaration ``A`` (shallow subset match).
    This is NOT a similarity/retrieval algorithm; the Body is expected to
    supply its own evaluator through ``MemoryStore.recall(context, evaluator)``.
    """
    if not isinstance(context, Mapping):
        return False
    activation = record.get("A")
    if not isinstance(activation, Mapping):
        return False
    return all(context.get(key) == value for key, value in activation.items())


# -- cross-process lock (mirrored from the repo hash-chain pattern) ------------


class _StoreLock:
    """Cross-process exclusive lock released automatically when a process dies."""

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._handle: Any = None

    def __enter__(self) -> "_StoreLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+b", closefd=True)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while True:
                handle.seek(0)
                if self._try_lock(handle):
                    self._handle = handle
                    return self
                if time.monotonic() >= deadline:
                    raise MemoryIntegrityError(
                        f"timed out acquiring store lock {self.path}"
                    )
                time.sleep(0.01)
        except BaseException:
            handle.close()
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._handle is None:
            return
        try:
            self._unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    @staticmethod
    def _try_lock(handle: Any) -> bool:
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True

        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    @staticmethod
    def _unlock(handle: Any) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# -- the store ----------------------------------------------------------------


class MemoryStore:
    """Body-owned append-only CAMU store with hash-chained JSONL records.

    The store file is itself the log: the first line is a meta header record
    and every following line is a ``camu`` or ``camu_update`` record chained to
    the previous one by sha256. Nothing is ever rewritten; epistemic updates
    (use / outcome / status) are appended as chained ``camu_update`` records
    and replayed on read. ``get`` returns the effective record (base CAMU plus
    all replayed updates).
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.log_path = self.path
        self._lock_path = self.path.with_name("." + self.path.name + ".lock")

    # -- lifecycle -----------------------------------------------------------

    @classmethod
    def create(cls, path: Path) -> "MemoryStore":
        path = Path(path)
        if path.exists():
            raise MemoryRecordError(f"memory store already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "schema": MEMORY_STORE_SCHEMA_VERSION,
            "kind": "meta",
            "sequence": 0,
            "created_at": _utc_now(),
            "previous_integrity_hash": None,
        }
        line = dict(envelope)
        line["integrity_hash"] = _sha256_hex(envelope)
        with path.open("ab") as handle:
            handle.write(_canonical_json_bytes(line) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return cls(path)

    @classmethod
    def load(cls, path: Path) -> "MemoryStore":
        """Load an existing store and verify its hash chain."""
        path = Path(path)
        store = cls(path)
        store._verify_chain(store._read_lines_unverified())
        return store

    def verify_chain(self) -> bool:
        """Re-verify the whole hash chain; raises on any tamper."""
        self._verify_chain(self._read_lines_unverified())
        return True

    # -- store primitives ----------------------------------------------------

    def _read_lines_unverified(self) -> list[dict[str, Any]]:
        if not self.log_path.is_file():
            raise MemoryIntegrityError(
                f"memory store does not exist: {self.log_path}"
            )
        lines: list[dict[str, Any]] = []
        try:
            with self.log_path.open("r", encoding="utf-8") as handle:
                for line_number, raw in enumerate(handle, start=1):
                    line = raw.strip()
                    if not line:
                        continue
                    if len(raw.encode("utf-8")) > MAX_JSONL_LINE_BYTES:
                        raise MemoryIntegrityError(
                            f"store line {line_number} exceeds the line bound"
                        )
                    try:
                        value = json.loads(line)
                    except (json.JSONDecodeError, RecursionError) as exc:
                        raise MemoryIntegrityError(
                            f"invalid JSONL line {line_number} in memory store"
                        ) from exc
                    if not isinstance(value, dict):
                        raise MemoryIntegrityError(
                            f"store line {line_number} is not an object"
                        )
                    lines.append(value)
        except UnicodeDecodeError as exc:
            raise MemoryIntegrityError(
                "memory store is not valid UTF-8 text"
            ) from exc
        except OSError as exc:
            raise MemoryIntegrityError(
                f"cannot read memory store {self.log_path}"
            ) from exc
        return lines

    def _verified_lines(self) -> list[dict[str, Any]]:
        lines = self._read_lines_unverified()
        self._verify_chain(lines)
        return lines

    def _verify_chain(self, lines: list[dict[str, Any]]) -> None:
        if not lines or lines[0].get("kind") != "meta":
            raise MemoryIntegrityError("memory store has no meta header record")
        previous: str | None = None
        camu_ids: set[str] = set()
        for expected_sequence, line in enumerate(lines):
            kind = line.get("kind")
            if kind not in ("meta", "camu", "camu_update"):
                raise MemoryIntegrityError(
                    f"unknown store record kind: {kind!r}"
                )
            if kind == "meta" and expected_sequence != 0:
                raise MemoryIntegrityError(
                    "meta header must be the first store record"
                )
            if line.get("sequence") != expected_sequence:
                raise MemoryIntegrityError("store sequence is not contiguous")
            envelope = {
                key: value
                for key, value in line.items()
                if key != "integrity_hash"
            }
            claimed = line.get("integrity_hash")
            if not isinstance(claimed, str) or claimed != _sha256_hex(envelope):
                raise MemoryIntegrityError("store record integrity mismatch")
            if line.get("previous_integrity_hash") != previous:
                raise MemoryIntegrityError("store hash chain is broken")
            if kind == "camu":
                record = line.get("record")
                if not isinstance(record, dict):
                    raise MemoryIntegrityError("camu record is not an object")
                if line.get("id") != _sha256_hex(record):
                    raise MemoryIntegrityError(
                        "camu content address mismatch"
                    )
                camu_ids.add(line["id"])
            elif kind == "camu_update":
                if line.get("id") not in camu_ids:
                    raise MemoryIntegrityError(
                        "camu update references an unknown camu"
                    )
            previous = claimed

    def _append_line(self, line: Mapping[str, Any]) -> None:
        with self.log_path.open("ab") as handle:
            handle.write(_canonical_json_bytes(dict(line)) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _find_camu(
        lines: list[dict[str, Any]], camu_id: str
    ) -> dict[str, Any] | None:
        for line in lines:
            if line.get("kind") == "camu" and line.get("id") == camu_id:
                return line
        return None

    def _append_update_unlocked(
        self,
        lines: list[dict[str, Any]],
        camu_id: str,
        update: Mapping[str, Any],
    ) -> None:
        envelope = {
            "schema": CAMU_SCHEMA_VERSION,
            "kind": "camu_update",
            "sequence": len(lines),
            "id": camu_id,
            "update": dict(update),
            "recorded_at": _utc_now(),
            "previous_integrity_hash": (
                lines[-1]["integrity_hash"] if lines else None
            ),
        }
        line = dict(envelope)
        line["integrity_hash"] = _sha256_hex(envelope)
        self._append_line(line)

    # -- replay: base CAMU + chained updates ---------------------------------

    def _replay(
        self, base_line: Mapping[str, Any], lines: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Reconstruct the effective CAMU record from base + update lines."""
        record = copy.deepcopy(dict(base_line["record"]))
        use_log = list(record["E"].get("use_log", []))
        support = list(record["E"].get("support", []))
        oppose = list(record["E"].get("oppose", []))
        status = record["P"].get("status", "pending")
        for line in lines:
            if line.get("kind") != "camu_update":
                continue
            if line.get("id") != base_line.get("id"):
                continue
            update = line.get("update")
            if not isinstance(update, dict):
                continue
            kind = update.get("kind")
            if kind == "use":
                use_log.append(
                    {
                        "at": update.get("at"),
                        "context": update.get("context"),
                        "observed": update.get("observed"),
                        "matched": None,
                    }
                )
            elif kind == "outcome":
                entry = {"at": update.get("at"), "observed": update.get("observed")}
                if update.get("matched") is True:
                    support.append(entry)
                else:
                    oppose.append(entry)
            elif kind == "status":
                status = update.get("status")
        record["E"]["use_log"] = use_log
        record["E"]["support"] = support
        record["E"]["oppose"] = oppose
        record["P"]["status"] = status
        return record

    # -- public API ----------------------------------------------------------

    def add_camu(self, record: Mapping[str, Any]) -> str:
        """Append one content-addressed CAMU record; returns its id."""
        validated = _validate_camu_record(record)
        camu_id = _sha256_hex(validated)
        with _StoreLock(self._lock_path):
            lines = self._read_lines_unverified()
            self._verify_chain(lines)
            if any(
                line.get("kind") == "camu" and line.get("id") == camu_id
                for line in lines
            ):
                raise MemoryRecordError(
                    f"duplicate CAMU content address: {camu_id}"
                )
            envelope = {
                "schema": CAMU_SCHEMA_VERSION,
                "kind": "camu",
                "sequence": len(lines),
                "id": camu_id,
                "record": validated,
                "recorded_at": _utc_now(),
                "previous_integrity_hash": (
                    lines[-1]["integrity_hash"] if lines else None
                ),
            }
            line = dict(envelope)
            line["integrity_hash"] = _sha256_hex(envelope)
            self._append_line(line)
        return camu_id

    def get(self, camu_id: str) -> dict[str, Any]:
        """Return the effective CAMU (base record plus replayed updates)."""
        lines = self._verified_lines()
        base = self._find_camu(lines, camu_id)
        if base is None:
            raise MemoryRecordError(f"CAMU not found: {camu_id}")
        return {
            "id": camu_id,
            "sequence": base["sequence"],
            "recorded_at": base["recorded_at"],
            "record": self._replay(base, lines),
        }

    def list(self) -> list[dict[str, Any]]:
        """Return every effective CAMU in chain order."""
        lines = self._verified_lines()
        result: list[dict[str, Any]] = []
        for line in lines:
            if line.get("kind") == "camu":
                result.append(
                    {
                        "id": line["id"],
                        "sequence": line["sequence"],
                        "recorded_at": line["recorded_at"],
                        "record": self._replay(line, lines),
                    }
                )
        return result

    def count(self) -> int:
        """Number of CAMU records (excluding meta and update lines)."""
        lines = self._verified_lines()
        return sum(1 for line in lines if line.get("kind") == "camu")

    def record_use(
        self,
        camu_id: str,
        context: Any,
        observed: Any = None,
    ) -> dict[str, Any]:
        """Append one use entry to the CAMU use_log; never rewrites history."""
        _json_compatible(context, "context")
        _json_compatible(observed, "observed")
        with _StoreLock(self._lock_path):
            lines = self._read_lines_unverified()
            self._verify_chain(lines)
            if self._find_camu(lines, camu_id) is None:
                raise MemoryRecordError(f"CAMU not found: {camu_id}")
            update = {
                "kind": "use",
                "at": _utc_now(),
                "context": context,
                "observed": observed,
            }
            self._append_update_unlocked(lines, camu_id, update)
            final_lines = self._read_lines_unverified()
            self._verify_chain(final_lines)
            base = self._find_camu(final_lines, camu_id)
            assert base is not None
            return {"id": camu_id, "record": self._replay(base, final_lines)}

    def record_outcome(
        self,
        camu_id: str,
        observed: Any,
        matched: bool,
    ) -> dict[str, Any]:
        """Record one outcome and apply the pending->verified/contradicted rule.

        Status bookkeeping (not a learning algorithm): if every recorded
        outcome supports the prediction the status becomes "verified"; if every
        recorded outcome contradicts it, "contradicted"; mixed evidence returns
        to "pending". An "overdue" status set by ``consolidate`` is replaced
        when fresh outcome evidence exists.
        """
        if not isinstance(matched, bool):
            raise MemoryRecordError("matched must be a boolean")
        _json_compatible(observed, "observed")
        with _StoreLock(self._lock_path):
            lines = self._read_lines_unverified()
            self._verify_chain(lines)
            base = self._find_camu(lines, camu_id)
            if base is None:
                raise MemoryRecordError(f"CAMU not found: {camu_id}")
            current = self._replay(base, lines)
            support = list(current["E"]["support"])
            oppose = list(current["E"]["oppose"])
            entry = {"at": _utc_now(), "observed": observed}
            if matched:
                support.append(entry)
            else:
                oppose.append(entry)
            if oppose and not support:
                new_status = "contradicted"
            elif support and not oppose:
                new_status = "verified"
            else:
                new_status = "pending"
            outcome_update = {
                "kind": "outcome",
                "at": entry["at"],
                "observed": observed,
                "matched": matched,
            }
            self._append_update_unlocked(lines, camu_id, outcome_update)
            if new_status != current["P"]["status"]:
                lines = self._read_lines_unverified()
                self._verify_chain(lines)
                status_update = {
                    "kind": "status",
                    "at": _utc_now(),
                    "status": new_status,
                }
                self._append_update_unlocked(lines, camu_id, status_update)
            final_lines = self._read_lines_unverified()
            self._verify_chain(final_lines)
            base = self._find_camu(final_lines, camu_id)
            assert base is not None
            return {"id": camu_id, "record": self._replay(base, final_lines)}

    def recall(
        self,
        context: Any,
        evaluator: Evaluator | None = None,
    ) -> list[str]:
        """Return matching CAMU ids; evaluator defaults to the placeholder matcher.

        The evaluator is a pluggable hook: ``callable(record, context) -> bool``.
        The default is ``default_evaluator``, a trivial JSON-predicate matcher
        documented as a placeholder. No forgetting policy is applied here.
        """
        _json_compatible(context, "context")
        evaluate = default_evaluator if evaluator is None else evaluator
        if not callable(evaluate):
            raise MemoryRecordError("evaluator must be callable")
        lines = self._verified_lines()
        matches: list[str] = []
        for line in lines:
            if line.get("kind") != "camu":
                continue
            record = self._replay(line, lines)
            try:
                if evaluate(record, context):
                    matches.append(line["id"])
            except Exception as exc:  # noqa: BLE001 - evaluator failures are recorded
                raise MemoryRecordError(
                    f"recall evaluator failed for CAMU {line['id']}"
                ) from exc
        return matches

    def consolidate(
        self,
        now: str | datetime | None = None,
        ttl_seconds: float = DEFAULT_CONSOLIDATION_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Sleep-consolidation scaffold: pending + older than TTL -> "overdue".

        Never deletes and never implements a forgetting policy. ``now`` accepts
        an ISO-8601 string, a datetime, or None (real wall clock). Returns a
        summary dict ``{"scanned": int, "overdue": int}``.
        """
        if isinstance(now, datetime):
            now_value = now
        elif isinstance(now, str):
            try:
                now_value = datetime.fromisoformat(now)
            except ValueError as exc:
                raise MemoryRecordError(
                    "now must be an ISO-8601 timestamp or datetime"
                ) from exc
        else:
            now_value = datetime.now(UTC)
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or ttl_seconds < 0
        ):
            raise MemoryRecordError(
                "ttl_seconds must be a non-negative number"
            )
        cutoff = now_value - timedelta(seconds=ttl_seconds)
        scanned = 0
        overdue = 0
        with _StoreLock(self._lock_path):
            lines = self._read_lines_unverified()
            self._verify_chain(lines)
            for line in lines:
                if line.get("kind") != "camu":
                    continue
                scanned += 1
                record = self._replay(line, lines)
                if record["P"]["status"] != "pending":
                    continue
                try:
                    recorded_at = datetime.fromisoformat(line["recorded_at"])
                except (KeyError, ValueError):
                    continue
                if recorded_at > cutoff:
                    continue
                status_update = {
                    "kind": "status",
                    "at": _utc_now(),
                    "status": "overdue",
                }
                self._append_update_unlocked(lines, line["id"], status_update)
                overdue += 1
                lines = self._read_lines_unverified()
                self._verify_chain(lines)
        return {"scanned": scanned, "overdue": overdue}
