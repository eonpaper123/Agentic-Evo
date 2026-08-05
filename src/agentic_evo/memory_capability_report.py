"""Capability outcome tracking + measurement report (MC-6).

MC-1..MC-5 proved the Memory-to-Capability loop closes: real sessions become
CAMU memories, CAMU memories compile into capability cards, cards steer future
loop candidates, and each new session auto-sediments. What they did NOT
measure is the honest gap ②: does using a card actually change task outcomes
(causal capability gain)?

This module builds the measurement foundation. It records every capability
use and every verified outcome as append-only, hash-chained events, then
produces a per-card and per-task-class report that compares outcomes *with*
the card against outcomes *without* it. The comparison is descriptive, not yet
causal (experiment 001 C1/C2/C5 stays not_established); it gives the Body the
raw deltas needed to design the controlled experiment later.

Design notes
------------

- ``CapabilityOutcomeLog`` mirrors the registry's append-only hash-chained
  JSONL model (``<home>/capabilities/outcomes.jsonl``). Nothing is ever
  rewritten; ``verify_chain`` detects tamper.
- A ``use`` event is recorded when a capability plan is offered (even before
  it is chosen); an ``outcome`` event is recorded when the task finishes and
  the Body verifies the result through its own receipts (opencode exit 0 +
  tests OK). ``matched_outcome`` means the outcome carries the same
  session_id/card_id as an earlier use -- this is the signal that the card
  path was actually followed.
- The report's ``by_task_class`` section computes the with-card vs without-card
  success deltas that a future controlled experiment would test. It never
  mutates state and never claims causality.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping

from .errors import MemoryIntegrityError, MemoryRecordError


OUTCOME_LOG_SCHEMA_VERSION = "agentic-evo-capability-outcome-v1"

DEFAULT_OUTCOME_LOG = Path("capabilities/outcomes.jsonl")

MAX_JSONL_LINE_BYTES = 2 * 1024 * 1024

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


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


class CapabilityOutcomeLog:
    """Append-only, hash-chained log of capability use/outcome events.

    First record is a meta header; later records are ``use`` or ``outcome``
    events. Every line is hash-chained exactly like the capability registry and
    CAMU store.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _initialize_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "schema": OUTCOME_LOG_SCHEMA_VERSION,
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
                    raise MemoryIntegrityError("outcome line exceeds size cap")
                try:
                    lines.append(json.loads(raw))
                except json.JSONDecodeError as exc:
                    raise MemoryIntegrityError(f"corrupt outcome log line: {exc}") from exc
        return lines

    def _verify_chain(self, lines: list[dict[str, Any]]) -> None:
        prev: str | None = None
        for idx, line in enumerate(lines):
            if idx == 0:
                if line.get("kind") != "meta":
                    raise MemoryIntegrityError("outcome log missing meta header")
            body = {k: v for k, v in line.items() if k != "integrity_hash"}
            expected = _sha256_hex(body)
            if line.get("integrity_hash") != expected:
                raise MemoryIntegrityError(
                    f"outcome log integrity mismatch at line {idx + 1}"
                )
            if prev is not None and line.get("previous_integrity_hash") != prev:
                raise MemoryIntegrityError(
                    f"outcome log chain break at line {idx + 1}"
                )
            prev = line.get("integrity_hash")

    def _verified_lines(self) -> list[dict[str, Any]]:
        lines = self._read_lines_unverified()
        if not lines:
            return []
        self._verify_chain(lines)
        return lines

    def _append_event(self, event: dict[str, Any]) -> str:
        event_id = _sha256_hex(event)
        with _OutcomeLogLock(self._lock_path):
            lines = self._read_lines_unverified()
            if lines:
                self._verify_chain(lines)
            else:
                self._initialize_locked()
                lines = self._read_lines_unverified()
            envelope = {
                "schema": OUTCOME_LOG_SCHEMA_VERSION,
                "kind": event["kind"],
                "event_id": event_id,
                "event": event,
                "recorded_at": _utc_now(),
                "previous_integrity_hash": lines[-1]["integrity_hash"],
            }
            line = dict(envelope)
            line["integrity_hash"] = _sha256_hex(envelope)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return event_id

    def record_use(
        self,
        card_id: str,
        task_context: Mapping[str, Any],
        *,
        session_id: str | None = None,
        provenance: str | None = None,
    ) -> str:
        """Record that a capability plan was offered for a task."""
        if not _SHA256_HEX.match(card_id or ""):
            raise MemoryRecordError("card_id must be a sha256 hex")
        _json_compatible(task_context, "task_context")
        event = {
            "kind": "use",
            "card_id": card_id,
            "task_context": dict(task_context or {}),
            "session_id": session_id,
            "provenance": provenance,
        }
        return self._append_event(event)

    def record_outcome(
        self,
        card_id: str,
        *,
        ok: bool,
        session_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> str:
        """Record a verified task outcome for a capability card.

        ``ok`` must come from the Body's own verification (opencode exit 0 +
        tests OK), never from this log itself.
        """
        if not _SHA256_HEX.match(card_id or ""):
            raise MemoryRecordError("card_id must be a sha256 hex")
        _json_compatible(details or {}, "details")
        event = {
            "kind": "outcome",
            "card_id": card_id,
            "ok": bool(ok),
            "session_id": session_id,
            "details": dict(details or {}),
        }
        return self._append_event(event)

    def list(self) -> list[dict[str, Any]]:
        lines = self._verified_lines()
        return [
            {"event_id": line["event_id"], **line["event"]}
            for line in lines
            if line.get("kind") in ("use", "outcome")
        ]

    def count(self) -> int:
        return len(self.list())

    def verify_chain(self) -> bool:
        lines = self._read_lines_unverified()
        if not lines:
            return True
        self._verify_chain(lines)
        return True


class _OutcomeLogLock:
    """Minimal cross-process append lock (mirrors the registry's lock)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: Any = None

    def __enter__(self) -> "_OutcomeLogLock":
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
                        f"outcome log lock timeout: {self.path}"
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
# Report
# ---------------------------------------------------------------------------


def _card_stats(
    card_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    uses = [e for e in events if e["kind"] == "use" and e["card_id"] == card_id]
    outcomes = [
        e for e in events if e["kind"] == "outcome" and e["card_id"] == card_id
    ]
    ok = sum(1 for o in outcomes if o["ok"])
    fail = len(outcomes) - ok
    session_ids = {
        o["session_id"] for o in outcomes if o.get("session_id") is not None
    }
    # A matched outcome is one whose session also produced a use event for the
    # same card -- i.e. the card path was actually followed.
    use_sessions = {u.get("session_id") for u in uses if u.get("session_id")}
    matched = sum(1 for o in outcomes if o.get("session_id") in use_sessions)
    return {
        "card_id": card_id,
        "uses": len(uses),
        "outcomes": len(outcomes),
        "matched_outcomes": matched,
        "ok": ok,
        "fail": fail,
        "success_rate": round(ok / len(outcomes), 4) if outcomes else None,
        "sessions": sorted(session_ids),
    }


def build_capability_report(
    registry: Any,
    outcome_log: CapabilityOutcomeLog,
) -> dict[str, Any]:
    """Build the measurement report: per-card stats + per-task-class deltas.

    ``registry`` needs ``list()`` returning ``{"id", "sequence", "card"}``
    entries; ``outcome_log`` is a ``CapabilityOutcomeLog``. Returns a
    JSON-compatible dict; never mutates state.
    """
    events = outcome_log.list()
    card_ids = [entry["id"] for entry in registry.list()]
    cards = [_card_stats(cid, events) for cid in card_ids]
    # Per-task-class aggregation: outcome ok rate split by whether that session
    # used the card (use event with the same session_id), for any card.
    use_sessions: dict[str, set[str]] = {}
    for e in events:
        if e["kind"] == "use" and e.get("session_id"):
            use_sessions.setdefault(e["card_id"], set()).add(e["session_id"])
    by_task_class: dict[str, dict[str, Any]] = {}
    for e in events:
        if e["kind"] != "outcome":
            continue
        session_id = e.get("session_id")
        used = bool(session_id and session_id in use_sessions.get(e["card_id"], set()))
        task_class = None
        details = e.get("details") or {}
        task_class = details.get("task_class")
        key = str(task_class) if task_class else "__unknown__"
        bucket = by_task_class.setdefault(
            key,
            {
                "task_class": task_class,
                "with_card": {"sessions": 0, "ok": 0},
                "without_card": {"sessions": 0, "ok": 0},
            },
        )
        group = bucket["with_card"] if used else bucket["without_card"]
        group["sessions"] += 1
        if e["ok"]:
            group["ok"] += 1
    by_task_class_list = []
    for key in sorted(by_task_class):
        bucket = by_task_class[key]
        wc, woc = bucket["with_card"], bucket["without_card"]
        wc_rate = round(wc["ok"] / wc["sessions"], 4) if wc["sessions"] else None
        woc_rate = round(woc["ok"] / woc["sessions"], 4) if woc["sessions"] else None
        delta = None
        if wc_rate is not None and woc_rate is not None:
            delta = round(wc_rate - woc_rate, 4)
        by_task_class_list.append(
            {
                "task_class": bucket["task_class"],
                "with_card": {"sessions": wc["sessions"], "ok": wc["ok"], "success_rate": wc_rate},
                "without_card": {"sessions": woc["sessions"], "ok": woc["ok"], "success_rate": woc_rate},
                "delta": delta,
            }
        )
    total_outcomes = sum(1 for e in events if e["kind"] == "outcome")
    total_ok = sum(1 for e in events if e["kind"] == "outcome" and e["ok"])
    return {
        "schema": "agentic-evo-capability-report-v1",
        "generated_at": _utc_now(),
        "summary": {
            "cards": len(card_ids),
            "uses": sum(1 for e in events if e["kind"] == "use"),
            "outcomes": total_outcomes,
            "ok": total_ok,
            "overall_success_rate": round(total_ok / total_outcomes, 4) if total_outcomes else None,
            "chain_verified": outcome_log.verify_chain(),
        },
        "cards": cards,
        "by_task_class": by_task_class_list,
    }


def render_report_text(report: Mapping[str, Any]) -> str:
    """Human-readable text rendering of the report."""
    s = report["summary"]
    lines = [
        f"Capability outcome report (schema {report['schema']})",
        f"generated_at: {report['generated_at']}",
        f"cards={s['cards']} uses={s['uses']} outcomes={s['outcomes']} ok={s['ok']} "
        f"success_rate={s['overall_success_rate']} chain_verified={s['chain_verified']}",
        "",
        "Per-card:",
    ]
    if not report["cards"]:
        lines.append("  (no cards)")
    for card in report["cards"]:
        lines.append(
            f"  {card['card_id'][:12]} uses={card['uses']} outcomes={card['outcomes']} "
            f"matched={card['matched_outcomes']} ok={card['ok']} fail={card['fail']} "
            f"rate={card['success_rate']}"
        )
    lines.append("")
    lines.append("Per-task-class (with-card vs without-card):")
    if not report["by_task_class"]:
        lines.append("  (no outcome events yet)")
    for row in report["by_task_class"]:
        wc, woc = row["with_card"], row["without_card"]
        lines.append(
            f"  {row['task_class']}: with={wc['sessions']}(ok {wc['ok']}, "
            f"{wc['success_rate']}) without={woc['sessions']}(ok {woc['ok']}, "
            f"{woc['success_rate']}) delta={row['delta']}"
        )
    return "\n".join(lines)
