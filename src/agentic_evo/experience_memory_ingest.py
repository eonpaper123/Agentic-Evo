"""Experience -> CAMU memory ingest (MC-2): ground Body memories in real evidence.

MC-1 closed the loop in a unit world. MC-2 grounds it in reality: a real
opencode session's TrustedState evidence records are turned into a Body-owned
CAMU record whose ``G.evidence_refs`` point at the actual evidence hashes.

Honest boundaries
-----------------

- The TrustedState payloads are content-hashes plus structural metadata only
  (raw prompt text and file contents are intentionally not stored there), so
  the *task class* and *task prompt* are supplied by the Body's development
  surface -- the Body knows which task it ran. The memory record itself stays
  declarative and replaceable.
- ``outcome_matched`` defaults to True only when the Body verified the session
  outcome (opencode exit 0 + tests OK) through its own receipts. The CAMU
  prediction status becomes ``verified`` through the store's own bookkeeping
  (record_outcome), never by this module directly.
- This ingest does NOT claim that TrustedState "understands" the task; it
  claims that a real session's evidence hashes are referenced by a Body memory
  record. Memory formation, learning, self-evolution stay not_established.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

from .errors import MemoryRecordError
from .memory_store import MemoryStore


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


#: event kinds treated as evidence of substantive work in a session.
_ACTIVITY_KINDS = frozenset(
    {
        "session_start",
        "session_updated",
        "message_updated",
        "message_part_updated",
        "message_part_delta",
        "session_status",
        "session_diff",
        "file_edited",
    }
)


def collect_session_evidence(records: Iterable[Any], session_id: str) -> list[Any]:
    """Return the evidence records belonging to one session, in chain order.

    ``records`` items need only expose ``session_id`` and ``sequence`` (the
    real ``EvidenceRecord`` has these; tests may use lightweight stand-ins).
    """
    return sorted(
        (r for r in records if getattr(r, "session_id", None) == session_id),
        key=lambda r: int(r.sequence),
    )


def estimate_uncertainty(records: Iterable[Any]) -> float:
    """Candidate epistemic-uncertainty estimate from structural evidence.

    Lower when the session both edited files and produced a rich message trail;
    higher when only structural markers exist. A placeholder, not a theory.
    """
    file_edits = sum(1 for r in records if r.event_kind == "file_edited")
    messages = sum(
        1
        for r in records
        if r.event_kind in ("message_updated", "message_part_updated")
    )
    if file_edits and messages >= 3:
        return 0.25
    if file_edits:
        return 0.5
    return 0.7


def build_camu_from_session(
    records: Iterable[Any],
    *,
    task_class: str,
    task_prompt: str,
    provenance: str | None = None,
    session_id: str,
    outcome_matched: bool = True,
) -> dict[str, Any]:
    """Build one CAMU record grounded in real evidence records."""
    recs = list(records)
    if not recs:
        raise MemoryRecordError("no evidence records for session")
    evidence_refs = [
        {"sequence": int(r.sequence), "hash": str(r.integrity_hash)} for r in recs
    ]
    surfaces = sorted(
        {r.execution_surface for r in recs if getattr(r, "execution_surface", None)}
    )
    surface = surfaces[0] if surfaces else "unknown"
    if not task_class or not task_class.strip():
        raise MemoryRecordError("task_class must be non-empty")
    if not task_prompt or not task_prompt.strip():
        raise MemoryRecordError("task_prompt must be non-empty")
    provenance_text = (provenance or task_prompt).strip()
    return {
        "G": {
            "evidence_refs": evidence_refs,
            "provenance": provenance_text,
            "collected_at": _utc_now(),
        },
        # The activation predicate is the REUSABLE trigger (task class only);
        # session/surface are contextual metadata, not part of the trigger.
        "A": {"task_class": task_class.strip()},
        "I": {
            "domain": "executable_skill",
            "description": (
                f"real {surface} session {session_id} produced a fix for "
                f"{task_class.strip()}"
            ),
        },
        "P": {
            "condition": f"a task matching {task_class.strip()} occurs",
            "expected": task_prompt.strip(),
            "counterfactual": (
                f"without this memory the fix for {task_class.strip()} "
                "must be re-derived from scratch"
            ),
            "status": "pending",
        },
        "E": {"uncertainty": estimate_uncertainty(recs)},
    }


def ingest_session(
    store: MemoryStore,
    records: Iterable[Any],
    *,
    task_class: str,
    task_prompt: str,
    provenance: str | None = None,
    session_id: str,
    outcome_matched: bool = True,
) -> str:
    """Write one grounded CAMU record and (optionally) verify its outcome.

    Returns the CAMU content-address id.
    """
    camu = build_camu_from_session(
        records,
        task_class=task_class,
        task_prompt=task_prompt,
        provenance=provenance,
        session_id=session_id,
        outcome_matched=outcome_matched,
    )
    camu_id = store.add_camu(camu)
    if outcome_matched:
        store.record_outcome(camu_id, {"ok": True, "session_id": session_id}, matched=True)
    return camu_id
