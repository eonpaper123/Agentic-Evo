from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from ._util import canonical_json_bytes, sha256_hex
from .adapters.observer_codex import adapt_codex_record
from .adapters.observer_lingtai import adapt_lingtai_record


OBSERVER_SCHEMA_VERSION = "agentic-evo-observer-v1"
_MAX_JSONL_RECORD_BYTES = 256 * 1024


@dataclass(frozen=True)
class SessionKey:
    agent_kind: str
    execution_surface: str
    session_id: str | None


@dataclass(frozen=True)
class SessionEvent:
    schema_version: str
    session_key: SessionKey
    sequence: int | None
    occurred_at: str | None
    event_kind: str
    actor: str
    action: str
    status: str
    exit_code: int | None
    duration_ms: int | None
    safe_summary: str
    source_locator: str
    raw_fingerprint: str
    coverage_gap: str | None = None


@dataclass(frozen=True)
class ProblemObservation:
    schema_version: str
    problem_id: str
    category: str
    severity: str
    confidence: str
    session_key: SessionKey
    sequence_start: int | None
    sequence_end: int | None
    occurrences: int
    evidence_reason: str
    evidence_locator: str
    explanation: str
    limitations: tuple[str, ...]


def load_source(
    path: Path,
) -> tuple[list[tuple[int, Mapping[str, Any]]], list[str], int, list[str]]:
    """Read JSON or JSONL without retaining malformed content in diagnostics."""

    if path.suffix.lower() == ".jsonl":
        return _load_jsonl_source(path)

    text = path.read_text(encoding="utf-8")
    records: list[tuple[int, Mapping[str, Any]]] = []
    warnings: list[str] = []
    skipped = 0
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                warnings.append(f"line {line_number}: malformed JSON skipped")
                skipped += 1
                continue
            if not isinstance(item, Mapping):
                warnings.append(f"line {line_number}: non-object event skipped")
                skipped += 1
                continue
            records.append((line_number, item))
        return records, warnings, skipped, []

    values = value if isinstance(value, list) else [value]
    for index, item in enumerate(values, start=1):
        if not isinstance(item, Mapping):
            warnings.append(f"item {index}: non-object event skipped")
            skipped += 1
            continue
        records.append((index, item))
    return records, warnings, skipped, []


def _load_jsonl_source(
    path: Path,
) -> tuple[list[tuple[int, Mapping[str, Any]]], list[str], int, list[str]]:
    records: list[tuple[int, Mapping[str, Any]]] = []
    warnings: list[str] = []
    coverage_gaps: list[str] = []
    skipped = 0
    with path.open("rb") as source:
        line_number = 0
        while line := source.readline(_MAX_JSONL_RECORD_BYTES + 1):
            line_number += 1
            if len(line) > _MAX_JSONL_RECORD_BYTES:
                while not line.endswith(b"\n"):
                    line = source.readline(_MAX_JSONL_RECORD_BYTES + 1)
                    if not line:
                        break
                warnings.append(f"line {line_number}: oversize JSONL record skipped")
                coverage_gaps.append("oversize JSONL record skipped")
                skipped += 1
                continue
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                warnings.append(f"line {line_number}: malformed JSON skipped")
                skipped += 1
                continue
            if not isinstance(item, Mapping):
                warnings.append(f"line {line_number}: non-object event skipped")
                skipped += 1
                continue
            records.append((line_number, item))
    return records, warnings, skipped, coverage_gaps


def observe(path: Path, *, agent_kind: str = "auto") -> dict[str, Any]:
    records, warnings, skipped, coverage_gaps = load_source(path)
    source_label = _safe_source_label(path.name)
    events: list[SessionEvent] = []
    for line_number, record in records:
        selected = agent_kind if agent_kind != "auto" else _detect_agent(record)
        locator = f"{source_label}#line:{line_number}"
        if selected is None:
            warnings.append(f"{locator}: unknown provider event skipped")
            coverage_gaps.append(
                "event shape not supported by bounded adapter projection"
            )
            skipped += 1
            continue
        adapter = adapt_codex_record if selected == "codex" else adapt_lingtai_record
        event = adapter(record, locator=locator, sequence=line_number)
        if event is None:
            warnings.append(f"{locator}: unknown {selected} event skipped")
            coverage_gaps.append(
                "event shape not supported by bounded adapter projection"
            )
            skipped += 1
            continue
        events.append(event)

    events.sort(key=_event_sort_key)
    observations = _detect_problems(events)
    coverage_gaps = sorted(
        {
            *coverage_gaps,
            *(event.coverage_gap for event in events if event.coverage_gap),
        }
    )
    agent_counts = Counter(event.session_key.agent_kind for event in events)
    session_counts = Counter(
        (
            event.session_key.agent_kind,
            event.session_key.execution_surface,
            event.session_key.session_id,
        )
        for event in events
    )
    categories = Counter(item.category for item in observations)
    return {
        "schema_version": OBSERVER_SCHEMA_VERSION,
        "source": source_label,
        "agent_kinds": dict(sorted(agent_counts.items())),
        "session_count": len(session_counts),
        "event_count": len(events),
        "events": [_as_dict(event) for event in events],
        "observations": [_as_dict(item) for item in observations],
        "category_summary": dict(sorted(categories.items())),
        "warnings": sorted(warnings),
        "skipped_count": skipped,
        "coverage_gaps": coverage_gaps,
        "limits": {
            "read_only": True,
            "structured_failures_only": True,
            "memory_suggestion": None,
            "capability_suggestion": None,
        },
    }


def report_json(report: Mapping[str, Any]) -> str:
    return canonical_json_bytes(report).decode("utf-8") + "\n"


def report_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Coding Agent Observation",
        "",
        f"- Source: `{report['source']}`",
        f"- Sessions: {report['session_count']}",
        f"- Events: {report['event_count']}",
        f"- Skipped: {report['skipped_count']}",
        "",
        "## Problems",
        "",
    ]
    observations = report["observations"]
    if not observations:
        lines.append("No structured command or test failures observed.")
    for item in observations:
        lines.extend(
            [
                f"### {item['problem_id']}",
                "",
                f"- Category: `{item['category']}`",
                f"- Severity: `{item['severity']}`",
                f"- Confidence: `{item['confidence']}`",
                f"- Evidence: {item['evidence_reason']} at `{item['evidence_locator']}`",
                f"- Explanation: {item['explanation']}",
                "",
            ]
        )
    lines.extend(["## Coverage gaps", ""])
    gaps = report["coverage_gaps"]
    lines.extend(f"- {gap}" for gap in gaps)
    if not gaps:
        lines.append("None recorded.")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {warning}" for warning in report["warnings"])
    if not report["warnings"]:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def _detect_agent(record: Mapping[str, Any]) -> str | None:
    provider = record.get("agent_kind") or record.get("provider")
    if provider in {"codex", "lingtai"}:
        return str(provider)
    if "hook_event_name" in record:
        return "codex"
    if record.get("source") in {"lingtai_daemon", "lingtai_receipt"}:
        return "lingtai"
    return None


def _safe_source_label(name: str) -> str:
    if (
        0 < len(name) <= 128
        and all(character.isalnum() or character in "._-" for character in name)
    ):
        return name
    return "source-sha256-" + sha256_hex(name)[:16]


def _detect_problems(events: Iterable[SessionEvent]) -> list[ProblemObservation]:
    observations: list[ProblemObservation] = []
    for event in events:
        if event.status != "failed":
            continue
        category = "test_failure" if event.action == "test" else "command_failure"
        reason = (
            f"explicit non-zero exit code {event.exit_code}"
            if event.exit_code is not None and event.exit_code != 0
            else "explicit structured failed status"
        )
        identity = {
            "category": category,
            "session_key": asdict(event.session_key),
            "sequence": event.sequence,
            "locator": event.source_locator,
            "reason": reason,
        }
        problem_id = "problem-" + sha256_hex(canonical_json_bytes(identity))[:16]
        observations.append(
            ProblemObservation(
                schema_version=OBSERVER_SCHEMA_VERSION,
                problem_id=problem_id,
                category=category,
                severity="error",
                confidence="high",
                session_key=event.session_key,
                sequence_start=event.sequence,
                sequence_end=event.sequence,
                occurrences=1,
                evidence_reason=reason,
                evidence_locator=event.source_locator,
                explanation=f"A structured {event.action} result reported failure.",
                limitations=("No prompt, tool body, or unstructured output was inspected.",),
            )
        )
    return observations


def _event_sort_key(event: SessionEvent) -> tuple[Any, ...]:
    key = event.session_key
    return (
        key.agent_kind,
        key.execution_surface,
        key.session_id or "",
        event.sequence if event.sequence is not None else -1,
        event.source_locator,
    )


def _as_dict(value: Any) -> dict[str, Any]:
    result = asdict(value)
    if "limitations" in result:
        result["limitations"] = list(result["limitations"])
    return result
