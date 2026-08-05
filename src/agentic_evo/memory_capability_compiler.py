"""Memory-to-Capability Compiler (v0.1) -- first closed-loop candidate.

This module establishes the *first working closed loop* of Memory-to-Capability
on top of the CAMU store (see ``memory_store.py``):

    recalled successful experiences -> compiled capability card
        -> future task behavior uses the card (behavior change)

It is explicitly a **candidate mechanism** authored by the Body's development
surface. Project design keeps the memory/learning algorithms Body-inventable:
recall evaluator, compilation strategy, trigger matching, and application are
all pluggable hooks with simple deterministic defaults. This slice proves the
loop can be closed and verified; it does NOT claim memory formation, causal
capability gain, learning, or self-evolution (those remain not_established per
experiment 001).

Design notes
------------

- The capability card is a declarative artifact: domain, trigger (activation
  predicate), procedure steps, expected outcome, and the source CAMU ids that
  grounded it. The card id is a content hash over its source evidence.
- The registry is an append-only, hash-chained JSONL (same tamper model as
  CAMU) under ``<home>/capabilities/capabilities.jsonl``. Every card append is
  chained; ``verify_chain`` detects tampering.
- The compiler is pure stdlib and caller-owned. It must NOT import
  runtime/body/kernel/trusted/witness: capabilities are replaceable declarative
  artifacts, never part of the TCB. It may reference CAMU ids (evidence_refs)
  but never imports or mutates trusted state.
- ``verify_behavior_change`` operationalizes the memory definition
  ``P(a,y | x, S+m) != P(a,y | x, S)`` at capability level: the same solver on
  the same task behaves differently with vs without the compiled card.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping

from .errors import MemoryIntegrityError, MemoryRecordError


CAPABILITY_SCHEMA_VERSION = "agentic-evo-capability-v1"
CAPABILITY_REGISTRY_SCHEMA_VERSION = "agentic-evo-capability-registry-v1"

DEFAULT_CAPABILITY_REGISTRY = Path("capabilities/capabilities.jsonl")

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
MAX_JSONL_LINE_BYTES = 2 * 1024 * 1024

#: influence domains the compiler will consider capability-forming.
COMPILABLE_DOMAINS = frozenset({"executable_skill", "plan"})

Evaluator = Callable[[Mapping[str, Any], Any], bool]
Solver = Any  # a solver object with .execute(steps, context) and .baseline(context)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_hex(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json_compatible(value: Any, name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise MemoryRecordError(f"{name} is not JSON-compatible: {exc}") from exc


def _validate_trigger(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MemoryRecordError("trigger must be a dict activation predicate")
    _json_compatible(value, "trigger")
    return value


def _validate_procedure(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(s, str) and s for s in value):
        raise MemoryRecordError("procedure must be a non-empty list of strings")
    return value


# ---------------------------------------------------------------------------
# Recall evaluator (candidate)
# ---------------------------------------------------------------------------


def successful_camu_evaluator(record: Mapping[str, Any], context: Any) -> bool:
    """Candidate recall evaluator: activation matches context AND status verified.

    ``record`` is the effective CAMU record: ``MemoryStore.recall`` passes the
    unwrapped ``{G, A, I, P, E}`` shape; ``MemoryStore.list/get`` wrap it as
    ``{"record": {G, A, I, P, E}, ...}``. Both are accepted.
    Activation match is a simple subset check: every key in ``record.A`` must
    exist in ``context`` and compare equal. This is a placeholder strategy; the
    Body may replace it.
    """
    try:
        rec = record["record"] if "record" in record else record
        activation = rec.get("A", {})
        prediction = rec.get("P", {})
        if not isinstance(activation, dict):
            return False
        if not isinstance(context, dict):
            return False
        if prediction.get("status") != "verified":
            return False
        for key, expected in activation.items():
            if key not in context or context[key] != expected:
                return False
        return True
    except (KeyError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Capability card + registry
# ---------------------------------------------------------------------------


def make_capability_card(
    *,
    domain: str,
    trigger: Mapping[str, Any],
    procedure: list[str],
    expected_outcome: Mapping[str, Any],
    evidence_refs: list[str],
    source_kinds: list[str] | None = None,
) -> dict[str, Any]:
    """Build a validated capability card; id = hash over evidence + procedure."""
    if domain not in COMPILABLE_DOMAINS:
        raise MemoryRecordError(f"domain {domain!r} is not compilable")
    trigger_v = _validate_trigger(dict(trigger))
    procedure_v = _validate_procedure(list(procedure))
    if not evidence_refs:
        raise MemoryRecordError("evidence_refs must be non-empty")
    _json_compatible(expected_outcome, "expected_outcome")
    payload = {
        "domain": domain,
        "trigger": trigger_v,
        "procedure": procedure_v,
        "expected_outcome": dict(expected_outcome),
        "evidence_refs": sorted(set(evidence_refs)),
    }
    card_id = _sha256_hex(payload)
    return {
        "schema": CAPABILITY_SCHEMA_VERSION,
        "id": card_id,
        **payload,
        "source_kinds": sorted(set(source_kinds or ["camu"])),
        "compiled_at": _utc_now(),
        "status": "candidate",
    }


class CapabilityRegistry:
    """Append-only, hash-chained registry of capability cards.

    One JSONL file (default ``<home>/capabilities/capabilities.jsonl``) whose
    first record is a meta header and whose following records are ``capability``
    cards. Every line is hash-chained (sha256 over the canonical JSON minus
    ``integrity_hash``); ``verify_chain`` detects tamper. Nothing is ever
    rewritten; a card is immutable once registered.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _initialize_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "schema": CAPABILITY_REGISTRY_SCHEMA_VERSION,
            "kind": "meta",
            "created_at": _utc_now(),
            "previous_integrity_hash": None,
        }
        line = dict(header)
        line["integrity_hash"] = _sha256_hex(header)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def _read_lines_unverified(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines: list[dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                if len(raw.encode("utf-8")) > MAX_JSONL_LINE_BYTES:
                    raise MemoryIntegrityError("capability line exceeds size cap")
                try:
                    lines.append(json.loads(raw))
                except json.JSONDecodeError as exc:
                    raise MemoryIntegrityError(
                        f"corrupt capability registry line: {exc}"
                    ) from exc
        return lines

    def _verify_chain(self, lines: list[dict[str, Any]]) -> None:
        prev: str | None = None
        for idx, line in enumerate(lines):
            if idx == 0:
                if line.get("kind") != "meta":
                    raise MemoryIntegrityError("capability registry missing meta header")
            body = {k: v for k, v in line.items() if k != "integrity_hash"}
            expected = _sha256_hex(body)
            if line.get("integrity_hash") != expected:
                raise MemoryIntegrityError(
                    f"capability registry integrity mismatch at line {idx + 1}"
                )
            if prev is not None and line.get("previous_integrity_hash") != prev:
                raise MemoryIntegrityError(
                    f"capability registry chain break at line {idx + 1}"
                )
            prev = line.get("integrity_hash")

    def _verified_lines(self) -> list[dict[str, Any]]:
        lines = self._read_lines_unverified()
        if not lines:
            return []
        self._verify_chain(lines)
        return lines

    def register(self, card: Mapping[str, Any]) -> str:
        """Append one validated capability card; returns its id."""
        card = dict(card)
        if card.get("schema") != CAPABILITY_SCHEMA_VERSION:
            raise MemoryRecordError("card schema mismatch")
        if not _SHA256_HEX.match(card.get("id", "")):
            raise MemoryRecordError("card id must be a sha256 hex")
        with _RegistryLock(self._lock_path):
            lines = self._read_lines_unverified()
            if lines:
                self._verify_chain(lines)
            else:
                self._initialize_locked()
                lines = self._read_lines_unverified()
            if any(
                line.get("kind") == "capability" and line.get("id") == card["id"]
                for line in lines
            ):
                raise MemoryRecordError(
                    f"duplicate capability card: {card['id']}"
                )
            envelope = {
                "schema": CAPABILITY_REGISTRY_SCHEMA_VERSION,
                "kind": "capability",
                "sequence": len(lines),
                "id": card["id"],
                "card": card,
                "registered_at": _utc_now(),
                "previous_integrity_hash": lines[-1]["integrity_hash"],
            }
            line = dict(envelope)
            line["integrity_hash"] = _sha256_hex(envelope)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return card["id"]

    def list(self) -> list[dict[str, Any]]:
        lines = self._verified_lines()
        return [
            {"id": line["id"], "sequence": line["sequence"], "card": line["card"]}
            for line in lines
            if line.get("kind") == "capability"
        ]

    def get(self, card_id: str) -> dict[str, Any]:
        for entry in self.list():
            if entry["id"] == card_id:
                return entry["card"]
        raise MemoryRecordError(f"capability card not found: {card_id}")

    def count(self) -> int:
        return len(self.list())

    def verify_chain(self) -> bool:
        lines = self._read_lines_unverified()
        if not lines:
            return True
        self._verify_chain(lines)
        return True


class _RegistryLock:
    """Minimal cross-process append lock (mirrors the CAMU store's lock)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: Any = None

    def __enter__(self) -> "_RegistryLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + 5.0
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                if time.time() > deadline:
                    raise MemoryIntegrityError(
                        f"capability registry lock timeout: {self.path}"
                    ) from None
                time.sleep(0.05)

    def __exit__(self, *exc: Any) -> None:
        if self._fd is not None:
            os.close(self._fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Compiler + application + verification
# ---------------------------------------------------------------------------


def _extract_procedure(record: Mapping[str, Any]) -> list[str] | None:
    """Extract procedure steps from a CAMU record.

    Candidate strategy (convention): a successful experience records its
    solution steps in ``P.expected.steps`` (list of strings). Fallbacks: if
    ``G.provenance`` is a multi-line string, each non-empty line is a step;
    otherwise the ``P.expected.description`` becomes a single weak step.
    """
    rec = record.get("record", record)
    expected = rec.get("P", {}).get("expected")
    if isinstance(expected, dict) and isinstance(expected.get("steps"), list):
        steps = [s for s in expected["steps"] if isinstance(s, str) and s]
        if steps:
            return steps
    g = rec.get("G", {})
    provenance = g.get("provenance")
    if isinstance(provenance, str):
        steps = [ln.strip() for ln in provenance.splitlines() if ln.strip()]
        if steps:
            return steps
    if isinstance(expected, dict) and expected.get("description"):
        return [str(expected["description"])]
    return None


def _extract_expected(record: Mapping[str, Any]) -> dict[str, Any]:
    rec = record.get("record", record)
    expected = rec.get("P", {}).get("expected")
    return dict(expected) if isinstance(expected, dict) else {}


class MemoryCapabilityCompiler:
    """Compile recalled successful experiences into capability cards."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        evaluator: Evaluator = successful_camu_evaluator,
        min_support: int = 1,
    ):
        self.registry = registry
        self.evaluator = evaluator
        self.min_support = min_support

    def compile(
        self,
        store: Any,
        context: Mapping[str, Any],
        *,
        domains: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Recall verified CAMUs matching context, group by domain, compile cards.

        Returns a list of capability cards (not yet registered). A card is only
        produced when at least ``min_support`` verified records support the same
        trigger+domain and a procedure can be extracted.
        """
        _json_compatible(context, "context")
        allowed = set(domains or COMPILABLE_DOMAINS)
        matched_ids = store.recall(context, evaluator=self.evaluator)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for camu_id in matched_ids:
            entry = store.get(camu_id)
            rec = entry["record"]
            domain = rec.get("I", {}).get("domain")
            if domain not in allowed:
                continue
            if rec.get("P", {}).get("status") != "verified":
                continue
            procedure = _extract_procedure(rec)
            if not procedure:
                continue
            grouped.setdefault(domain, []).append(
                {"camu_id": camu_id, "record": rec, "procedure": procedure}
            )
        cards: list[dict[str, Any]] = []
        for domain, records in sorted(grouped.items()):
            if len(records) < self.min_support:
                continue
            evidence_refs = sorted({r["camu_id"] for r in records})
            # Candidate strategy: prefer the procedure of the record with the
            # most steps (richest successful experience); same-trigger union is
            # a future refinement.
            best = max(records, key=lambda r: len(r["procedure"]))
            trigger = dict(best["record"]["A"])
            card = make_capability_card(
                domain=domain,
                trigger=trigger,
                procedure=best["procedure"],
                expected_outcome=_extract_expected(best["record"]),
                evidence_refs=evidence_refs,
                source_kinds=["camu"],
            )
            cards.append(card)
        return cards

    def compile_and_register(
        self,
        store: Any,
        context: Mapping[str, Any],
        *,
        domains: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Compile and append cards to the registry; returns registered cards."""
        cards = self.compile(store, context, domains=domains)
        for card in cards:
            self.registry.register(card)
        return cards


# ---------------------------------------------------------------------------
# Application + verification
# ---------------------------------------------------------------------------


def matches_trigger(card: Mapping[str, Any], task_context: Mapping[str, Any]) -> bool:
    """Candidate trigger matcher: every trigger key exists+equals in context."""
    trigger = card.get("trigger", {})
    if not isinstance(task_context, dict):
        return False
    return all(
        key in task_context and task_context[key] == value
        for key, value in trigger.items()
    )


def resolve_plan(
    registry: CapabilityRegistry,
    task_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve the first matching capability card into an actionable plan.

    Returns ``{"used_capability": bool, "card_id": str|None,
    "plan": [steps]|None, "domain": str|None}``. This is the runtime-facing
    entry: when a task matches a compiled card, the body receives the card's
    procedure as a plan instead of re-deriving from scratch.
    """
    for entry in registry.list():
        card = entry["card"]
        if matches_trigger(card, task_context):
            return {
                "used_capability": True,
                "card_id": card.get("id"),
                "plan": list(card.get("procedure", [])),
                "domain": card.get("domain"),
            }
    return {"used_capability": False, "card_id": None, "plan": None, "domain": None}


def apply_capability(
    card: Mapping[str, Any],
    task_context: Mapping[str, Any],
    solver: Solver,
) -> dict[str, Any]:
    """Apply a card when its trigger matches; otherwise run baseline.

    ``solver`` must expose ``execute(steps, context)`` and
    ``baseline(context)``; each returns a JSON-compatible result dict. Returns
    ``{"used_capability": bool, "card_id": str|None, "result": dict,
    "steps": list|None}``.
    """
    if matches_trigger(card, task_context):
        steps = list(card.get("procedure", []))
        result = solver.execute(steps, dict(task_context))
        return {
            "used_capability": True,
            "card_id": card.get("id"),
            "result": result,
            "steps": steps,
        }
    result = solver.baseline(dict(task_context))
    return {
        "used_capability": False,
        "card_id": None,
        "result": result,
        "steps": None,
    }


def verify_behavior_change(
    card: Mapping[str, Any],
    task_context: Mapping[str, Any],
    solver: Solver,
) -> dict[str, Any]:
    """Operationalize P(a,y|x,S+m) != P(a,y|x,S) at capability level.

    Runs the same task with and without the card under the same solver and
    returns ``{"with_card": ..., "baseline": ..., "changed": bool}`` where
    ``changed`` means the result dicts differ. This is the falsifiable check
    that the compiled card actually alters future behavior for a matching task.
    """
    with_card = apply_capability(card, task_context, solver)
    baseline = solver.baseline(dict(task_context))
    changed = with_card.get("result") != baseline
    return {
        "with_card": with_card,
        "baseline": {"used_capability": False, "card_id": None, "result": baseline},
        "changed": changed,
    }
