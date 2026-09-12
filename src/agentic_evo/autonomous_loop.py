"""Caller-owned autonomous self-repair loop engine (v1.0).

The loop is a *caller-owned* component: it owns only its own append-only stores
under ``home`` (``meta.json``, ``events.jsonl``, ``consolidation.jsonl``) and
never touches the runtime life core (Who / Why / Authority / Root / Head).
Callers supply scoped, reversible repair candidates and probes; the engine
enforces the policy gate, runs the bounded probation window, records every
stage as hash-chained JSONL, and appends promoted procedures to a durable
consolidation store. The loop itself never changes production policy
automatically; the default policy denies all external side effects unless the
caller explicitly authorizes them.

State machine (one cycle):

    observe -> candidate -> probation -> outcome -> commit | rollback -> consolidate

Direct use:

    python -m agentic_evo.autonomous_loop --fixture passed --output out.jsonl

CLI (consistent with cli.py):

    agentic-evo autonomous-loop-smoke --fixture passed --output out.jsonl
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable, Mapping
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
from .errors import (
    GenesisExistsError,
    IntegrityError,
    InvalidCandidateError,
    PolicyGateError,
)


AUTONOMOUS_LOOP_SCHEMA_VERSION = "agentic-evo-autonomous-loop-v1"
CONSOLIDATION_SCHEMA_VERSION = "agentic-evo-consolidation-v1"
AUTONOMOUS_LOOP_PROTOCOL_VERSION = "autonomous-loop-v1.0"
SMOKE_INSTRUMENT_VERSION = "agentic-evo-autonomous-loop-smoke-1"

DEFAULT_MAX_PROBE_RUNS = 5
DEFAULT_MAX_PROBATION_SECONDS = 30.0
DEFAULT_PROMOTION_PASSES_REQUIRED = 2

LIFE_CORE_SCOPES = frozenset({"who", "why", "authority", "root", "head"})
ALLOWED_EFFECT_KINDS = frozenset({"in_memory", "external"})
ALLOWED_OUTCOMES = frozenset({"passed", "failed", "inconclusive"})
LOOP_EVENT_KINDS = frozenset(
    {
        "observe",
        "candidate",
        "probation",
        "outcome",
        "commit",
        "rollback",
        "consolidate",
    }
)

Probe = Callable[[], tuple[bool, Mapping[str, Any]]]


@dataclass(frozen=True)
class LoopPolicy:
    """Conservative caller-granted policy for one loop instance.

    Defaults deny external side effects; the loop performs no external effect
    unless the caller grants ``authorize_external_effects=True``.
    """

    authorize_external_effects: bool = False
    max_probe_runs: int = DEFAULT_MAX_PROBE_RUNS
    max_probation_seconds: float = DEFAULT_MAX_PROBATION_SECONDS
    promotion_passes_required: int = DEFAULT_PROMOTION_PASSES_REQUIRED

    def __post_init__(self) -> None:
        if self.max_probe_runs < 1:
            raise ValueError("max_probe_runs must be >= 1")
        if self.max_probation_seconds <= 0:
            raise ValueError("max_probation_seconds must be > 0")
        if self.promotion_passes_required < 1:
            raise ValueError("promotion_passes_required must be >= 1")

    def authorization_label(self) -> str:
        return "external_authorized" if self.authorize_external_effects else "in_memory_only"


@dataclass(frozen=True)
class CandidateRepair:
    """A scoped, reversible repair supplied by the caller.

    ``apply``/``revert`` operate on a caller-owned ``workspace`` object and must
    never rewrite the life core (Who / Why / Authority / Root / Head). The
    engine refuses candidates whose ``scope`` overlaps the life core, that are
    not declared reversible, or (by default policy) that claim external side
    effects.
    """

    candidate_id: str
    scope: str
    effect_kind: str
    plan: Mapping[str, Any]
    apply: Callable[[Any], None]
    revert: Callable[[Any], None]
    reversible: bool = True
    touches_life_core: bool = False


@dataclass(frozen=True)
class AutonomousLoopRecord:
    """One append-only, hash-chained cycle record."""

    schema_version: str
    event_id: str
    sequence: int
    instrument_version: str
    protocol_version: str
    event_kind: str
    occurred_at: str
    observed_at: str
    loop_id: str
    policy_authorization: str
    observation_ref: str | None
    candidate_ref: str | None
    probation_ref: str | None
    outcome_ref: str | None
    commit_ref: str | None
    payload: dict[str, Any]
    previous_integrity_hash: str | None
    integrity_hash: str


def _validate_candidate(candidate: CandidateRepair, policy: LoopPolicy) -> None:
    if not isinstance(candidate, CandidateRepair):
        raise InvalidCandidateError("candidate must be a CandidateRepair")
    if not candidate.candidate_id or not candidate.scope:
        raise InvalidCandidateError("candidate needs a non-empty id and scope")
    if candidate.scope.lower() in LIFE_CORE_SCOPES:
        raise InvalidCandidateError(
            f"candidate scope {candidate.scope!r} overlaps the life core"
        )
    if candidate.touches_life_core is not False:
        raise InvalidCandidateError("candidate must never rewrite the life core")
    if candidate.reversible is not True:
        raise InvalidCandidateError("candidate must be reversible for probation")
    if candidate.effect_kind not in ALLOWED_EFFECT_KINDS:
        raise InvalidCandidateError(f"unsupported effect_kind: {candidate.effect_kind!r}")
    if candidate.effect_kind == "external" and not policy.authorize_external_effects:
        raise PolicyGateError(
            "external side effects are denied by the default loop policy; "
            "the caller must grant authorize_external_effects=True"
        )


def _decide_outcome(
    probe_runs: list[dict[str, Any]],
    max_runs: int,
    window_exhausted: bool,
) -> tuple[str, str]:
    if any(run.get("passed") is True for run in probe_runs):
        return "passed", "a probation probe passed within the window"
    if any(run.get("error") for run in probe_runs):
        return "inconclusive", "probing raised non-decisive errors without a pass"
    if window_exhausted and len(probe_runs) < max_runs:
        return "inconclusive", "probation window exhausted before decisive evidence"
    if not probe_runs:
        return "inconclusive", "no probe runs completed in the window"
    return "failed", "every completed probe run failed within the window"


class AutonomousLoop:
    """Append-only self-repair loop ledger and state machine.

    The loop owns ``meta.json``, ``events.jsonl`` (hash-chained cycle records)
    and ``consolidation.jsonl`` (durable reusable procedures) under ``home``.
    It performs no writes outside ``home`` and no network or process effects.
    """

    def __init__(
        self,
        home: Path,
        *,
        instrument_version: str,
        protocol_version: str,
        loop_id: str,
        policy: LoopPolicy,
    ) -> None:
        self.home = Path(home)
        self.instrument_version = instrument_version
        self.protocol_version = protocol_version
        self.loop_id = loop_id
        self.policy = policy
        self.log_path = self.home / "events.jsonl"
        self.consolidation_path = self.home / "consolidation.jsonl"
        self._pass_counts: dict[str, int] = {}
        self._consolidated: set[str] = set()
        self._restore_session_state()

    @classmethod
    def create(
        cls,
        home: Path,
        *,
        instrument_version: str,
        protocol_version: str,
        loop_id: str | None = None,
        policy: LoopPolicy | None = None,
    ) -> "AutonomousLoop":
        home = Path(home)
        meta_path = home / "meta.json"
        if meta_path.exists() or (home / "events.jsonl").exists():
            raise GenesisExistsError("autonomous loop home already exists")
        home.mkdir(parents=True, exist_ok=True)
        policy = LoopPolicy() if policy is None else policy
        loop_id = uuid4().hex if loop_id is None else loop_id
        atomic_write_json(
            meta_path,
            {
                "schema_version": AUTONOMOUS_LOOP_SCHEMA_VERSION,
                "loop_id": loop_id,
                "instrument_version": instrument_version,
                "protocol_version": protocol_version,
                "policy": asdict(policy),
                "created_at": utc_now(),
            },
        )
        atomic_write_bytes(home / "events.jsonl", b"")
        atomic_write_bytes(home / "consolidation.jsonl", b"")
        return cls(
            home,
            instrument_version=instrument_version,
            protocol_version=protocol_version,
            loop_id=loop_id,
            policy=policy,
        )

    @classmethod
    def load(cls, home: Path) -> "AutonomousLoop":
        home = Path(home)
        meta = read_json(home / "meta.json")
        if meta.get("schema_version") != AUTONOMOUS_LOOP_SCHEMA_VERSION:
            raise IntegrityError("unsupported autonomous loop schema")
        instrument = meta.get("instrument_version")
        protocol = meta.get("protocol_version")
        loop_id = meta.get("loop_id")
        policy_value = meta.get("policy")
        if not isinstance(instrument, str) or not isinstance(protocol, str):
            raise IntegrityError("invalid autonomous loop metadata")
        if not isinstance(loop_id, str) or not loop_id:
            raise IntegrityError("invalid autonomous loop id")
        if not isinstance(policy_value, dict):
            raise IntegrityError("invalid autonomous loop policy")
        try:
            policy = LoopPolicy(**policy_value)
        except (TypeError, ValueError) as exc:
            raise IntegrityError("invalid autonomous loop policy") from exc
        if not (home / "events.jsonl").is_file():
            raise IntegrityError("autonomous loop log is missing")
        if not (home / "consolidation.jsonl").is_file():
            raise IntegrityError("autonomous loop consolidation store is missing")
        return cls(
            home,
            instrument_version=instrument,
            protocol_version=protocol,
            loop_id=loop_id,
            policy=policy,
        )

    def _restore_session_state(self) -> None:
        try:
            existing = self._read_records_unverified()
            self._verify_records(existing)
        except (OSError, IntegrityError):
            return
        for record in existing:
            if record.event_kind == "commit":
                candidate_id = record.payload.get("candidate_id")
                if isinstance(candidate_id, str) and candidate_id:
                    self._pass_counts[candidate_id] = self._pass_counts.get(candidate_id, 0) + 1
        for line in self._read_consolidation_unverified():
            candidate_id = line.get("candidate_id")
            if isinstance(candidate_id, str) and candidate_id:
                self._consolidated.add(candidate_id)

    # -- store primitives ---------------------------------------------------

    def _append(
        self,
        *,
        event_kind: str,
        payload: Mapping[str, Any],
        observation_ref: str | None = None,
        candidate_ref: str | None = None,
        probation_ref: str | None = None,
        outcome_ref: str | None = None,
        commit_ref: str | None = None,
    ) -> AutonomousLoopRecord:
        if event_kind not in LOOP_EVENT_KINDS:
            raise IntegrityError(f"unsupported loop event kind: {event_kind}")
        with ExclusiveFileLock(self.home / ".loop.lock"):
            existing = self._read_records_unverified()
            self._verify_records(existing)
            previous = existing[-1] if existing else None
            unsigned: dict[str, Any] = {
                "schema_version": AUTONOMOUS_LOOP_SCHEMA_VERSION,
                "event_id": uuid4().hex,
                "sequence": len(existing) + 1,
                "instrument_version": self.instrument_version,
                "protocol_version": self.protocol_version,
                "event_kind": event_kind,
                "occurred_at": utc_now(),
                "observed_at": utc_now(),
                "loop_id": self.loop_id,
                "policy_authorization": self.policy.authorization_label(),
                "observation_ref": observation_ref,
                "candidate_ref": candidate_ref,
                "probation_ref": probation_ref,
                "outcome_ref": outcome_ref,
                "commit_ref": commit_ref,
                "payload": dict(payload),
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

    def _from_dict(self, value: dict[str, Any]) -> AutonomousLoopRecord:
        return AutonomousLoopRecord(
            schema_version=value["schema_version"],
            event_id=value["event_id"],
            sequence=value["sequence"],
            instrument_version=value["instrument_version"],
            protocol_version=value["protocol_version"],
            event_kind=value["event_kind"],
            occurred_at=value["occurred_at"],
            observed_at=value["observed_at"],
            loop_id=value["loop_id"],
            policy_authorization=value["policy_authorization"],
            observation_ref=value.get("observation_ref"),
            candidate_ref=value.get("candidate_ref"),
            probation_ref=value.get("probation_ref"),
            outcome_ref=value.get("outcome_ref"),
            commit_ref=value.get("commit_ref"),
            payload=value["payload"],
            previous_integrity_hash=value.get("previous_integrity_hash"),
            integrity_hash=value["integrity_hash"],
        )

    def _read_records_unverified(self) -> list[AutonomousLoopRecord]:
        records: list[AutonomousLoopRecord] = []
        with self.log_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise IntegrityError("invalid JSONL line in loop log") from exc
                if not isinstance(value, dict):
                    raise IntegrityError("loop log line is not an object")
                records.append(self._from_dict(value))
        return records

    def _verify_records(self, records: list[AutonomousLoopRecord]) -> None:
        previous_hash: str | None = None
        for record in records:
            if record.previous_integrity_hash != previous_hash:
                raise IntegrityError("loop log hash chain is broken")
            unsigned = {
                "schema_version": record.schema_version,
                "event_id": record.event_id,
                "sequence": record.sequence,
                "instrument_version": record.instrument_version,
                "protocol_version": record.protocol_version,
                "event_kind": record.event_kind,
                "occurred_at": record.occurred_at,
                "observed_at": record.observed_at,
                "loop_id": record.loop_id,
                "policy_authorization": record.policy_authorization,
                "observation_ref": record.observation_ref,
                "candidate_ref": record.candidate_ref,
                "probation_ref": record.probation_ref,
                "outcome_ref": record.outcome_ref,
                "commit_ref": record.commit_ref,
                "payload": record.payload,
                "previous_integrity_hash": record.previous_integrity_hash,
            }
            if record.integrity_hash != sha256_hex(canonical_json_bytes(unsigned)):
                raise IntegrityError("loop log record hash mismatch")
            previous_hash = record.integrity_hash

    def _record_by_id(self, event_id: str) -> AutonomousLoopRecord:
        for record in self.records():
            if record.event_id == event_id:
                return record
        raise IntegrityError(f"loop record not found: {event_id}")

    def records(self) -> tuple[AutonomousLoopRecord, ...]:
        records = self._read_records_unverified()
        self._verify_records(records)
        return tuple(records)

    def verify(self) -> bool:
        self._verify_records(self._read_records_unverified())
        return True

    # -- consolidation store ------------------------------------------------

    def _read_consolidation_unverified(self) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        with self.consolidation_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise IntegrityError("invalid JSONL line in consolidation store") from exc
                if not isinstance(value, dict):
                    raise IntegrityError("consolidation line is not an object")
                lines.append(value)
        return lines

    def consolidation_records(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._read_consolidation_unverified())

    def _append_consolidation(self, value: Mapping[str, Any]) -> None:
        with ExclusiveFileLock(self.home / ".consolidation.lock"):
            with self.consolidation_path.open("ab") as handle:
                handle.write(canonical_json_bytes(dict(value)) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())

    # -- state machine stages -----------------------------------------------

    def observe(
        self,
        *,
        signal_kind: str,
        source: str,
        observation: Mapping[str, Any],
    ) -> AutonomousLoopRecord:
        """Append-only entry of one external signal/observation."""
        if not isinstance(signal_kind, str) or not signal_kind:
            raise IntegrityError("signal_kind must be a non-empty string")
        if not isinstance(source, str) or not source:
            raise IntegrityError("source must be a non-empty string")
        return self._append(
            event_kind="observe",
            payload={
                "signal_kind": signal_kind,
                "source": source,
                "observation": dict(observation),
            },
        )

    def propose(
        self,
        *,
        candidate: CandidateRepair,
        observation_ref: str | None = None,
    ) -> AutonomousLoopRecord:
        """Policy-gated proposal of a scoped, reversible candidate."""
        _validate_candidate(candidate, self.policy)
        return self._append(
            event_kind="candidate",
            observation_ref=observation_ref,
            payload={
                "candidate_id": candidate.candidate_id,
                "scope": candidate.scope,
                "effect_kind": candidate.effect_kind,
                "reversible": candidate.reversible,
                "touches_life_core": candidate.touches_life_core,
                "policy_gate": (
                    "authorized_external"
                    if candidate.effect_kind == "external"
                    else "authorized_in_memory"
                ),
                "plan": dict(candidate.plan),
            },
        )

    def probation(
        self,
        *,
        candidate: CandidateRepair,
        probe: Probe,
        workspace: Any,
        observation_ref: str | None = None,
        candidate_ref: str | None = None,
    ) -> AutonomousLoopRecord:
        """Apply the candidate reversibly and probe within a bounded window."""
        _validate_candidate(candidate, self.policy)
        started = time.monotonic()
        apply_error: str | None = None
        try:
            candidate.apply(workspace)
        except Exception as exc:  # noqa: BLE001 - recorded as probation evidence
            apply_error = f"{type(exc).__name__}: {exc}"
        probe_runs: list[dict[str, Any]] = []
        window_exhausted = False
        if apply_error is None:
            for run_index in range(1, self.policy.max_probe_runs + 1):
                if time.monotonic() - started >= self.policy.max_probation_seconds:
                    window_exhausted = True
                    break
                run_started = time.monotonic()
                passed: bool | None = None
                evidence: dict[str, Any] = {}
                error: str | None = None
                try:
                    result = probe()
                    passed, evidence = bool(result[0]), dict(result[1])
                except Exception as exc:  # noqa: BLE001 - recorded as probe evidence
                    error = f"{type(exc).__name__}: {exc}"
                probe_runs.append(
                    {
                        "run": run_index,
                        "passed": passed,
                        "error": error,
                        "evidence": evidence,
                        "elapsed_seconds": round(
                            time.monotonic() - run_started, 6
                        ),
                    }
                )
                if passed is True:
                    break
        return self._append(
            event_kind="probation",
            observation_ref=observation_ref,
            candidate_ref=candidate_ref,
            payload={
                "candidate_id": candidate.candidate_id,
                "window_max_runs": self.policy.max_probe_runs,
                "window_max_seconds": self.policy.max_probation_seconds,
                "applied": apply_error is None,
                "apply_error": apply_error,
                "probe_runs": probe_runs,
                "window_exhausted": window_exhausted,
            },
        )

    def outcome(
        self,
        *,
        candidate_ref: str | None = None,
        probation_ref: str | None = None,
    ) -> AutonomousLoopRecord:
        """Evaluate probation evidence; only passed/failed/inconclusive."""
        probation_record = self._record_by_id(probation_ref)
        payload = probation_record.payload
        probe_runs = payload.get("probe_runs")
        if not isinstance(probe_runs, list):
            raise IntegrityError("probation record has no probe runs")
        verdict, reason = _decide_outcome(
            probe_runs,
            int(payload.get("window_max_runs", 0)),
            bool(payload.get("window_exhausted", False)),
        )
        if verdict not in ALLOWED_OUTCOMES:
            raise IntegrityError(f"unexpected outcome verdict: {verdict}")
        return self._append(
            event_kind="outcome",
            candidate_ref=candidate_ref,
            probation_ref=probation_ref,
            payload={
                "candidate_id": payload.get("candidate_id"),
                "outcome": verdict,
                "verdict_reason": reason,
                "probe_run_summary": {
                    "total": len(probe_runs),
                    "passed": sum(1 for run in probe_runs if run.get("passed") is True),
                    "failed": sum(1 for run in probe_runs if run.get("passed") is False),
                    "errored": sum(1 for run in probe_runs if run.get("error")),
                },
            },
        )

    def settle(
        self,
        *,
        candidate: CandidateRepair,
        workspace: Any,
        candidate_ref: str | None = None,
        outcome_ref: str | None = None,
    ) -> AutonomousLoopRecord:
        """Commit on passed; roll back to prior state otherwise."""
        outcome_record = self._record_by_id(outcome_ref)
        verdict = outcome_record.payload.get("outcome")
        if verdict not in ALLOWED_OUTCOMES:
            raise IntegrityError(f"invalid outcome verdict: {verdict!r}")
        if verdict == "passed":
            self._pass_counts[candidate.candidate_id] = (
                self._pass_counts.get(candidate.candidate_id, 0) + 1
            )
            return self._append(
                event_kind="commit",
                candidate_ref=candidate_ref,
                outcome_ref=outcome_ref,
                payload={
                    "candidate_id": candidate.candidate_id,
                    "committed": True,
                    "pass_count": self._pass_counts[candidate.candidate_id],
                    "promotion_passes_required": self.policy.promotion_passes_required,
                },
            )
        reverted = False
        revert_error: str | None = None
        try:
            candidate.revert(workspace)
            reverted = True
        except Exception as exc:  # noqa: BLE001 - recorded as rollback evidence
            revert_error = f"{type(exc).__name__}: {exc}"
        return self._append(
            event_kind="rollback",
            candidate_ref=candidate_ref,
            outcome_ref=outcome_ref,
            payload={
                "candidate_id": candidate.candidate_id,
                "reason": verdict,
                "reverted": reverted,
                "revert_error": revert_error,
                "destructive_global_ops": False,
            },
        )

    def consolidate(
        self,
        *,
        candidate: CandidateRepair,
        commit_ref: str | None = None,
        procedure: Mapping[str, Any],
    ) -> AutonomousLoopRecord | None:
        """Promote a committed, repeatedly-passing fix to the durable store.

        Appends the reusable procedure to ``consolidation.jsonl``; the loop
        never changes production policy automatically.
        """
        if not isinstance(procedure, Mapping) or not procedure.get("procedure_id"):
            raise IntegrityError("procedure needs a non-empty procedure_id")
        if candidate.candidate_id in self._consolidated:
            return None
        pass_count = self._pass_counts.get(candidate.candidate_id, 0)
        if pass_count < self.policy.promotion_passes_required:
            return None
        store_line = {
            "schema": CONSOLIDATION_SCHEMA_VERSION,
            "procedure_id": procedure["procedure_id"],
            "candidate_id": candidate.candidate_id,
            "scope": candidate.scope,
            "effect_kind": candidate.effect_kind,
            "pass_count": pass_count,
            "promoted_at": utc_now(),
            "procedure": dict(procedure),
        }
        self._append_consolidation(store_line)
        self._consolidated.add(candidate.candidate_id)
        record = self._append(
            event_kind="consolidate",
            candidate_ref=None,
            commit_ref=commit_ref,
            payload={
                "procedure_id": procedure["procedure_id"],
                "candidate_id": candidate.candidate_id,
                "pass_count": pass_count,
                "promotion_passes_required": self.policy.promotion_passes_required,
                "procedure": dict(procedure),
            },
        )
        return record

    def run_cycle(
        self,
        *,
        signal_kind: str,
        source: str,
        observation: Mapping[str, Any],
        candidate: CandidateRepair,
        probe: Probe,
        workspace: Any,
        procedure: Mapping[str, Any] | None = None,
    ) -> AutonomousLoopRecord:
        """Run one full observe->candidate->probation->outcome->settle cycle."""
        observation_record = self.observe(
            signal_kind=signal_kind, source=source, observation=observation
        )
        candidate_record = self.propose(
            candidate=candidate, observation_ref=observation_record.event_id
        )
        probation_record = self.probation(
            candidate=candidate,
            probe=probe,
            workspace=workspace,
            observation_ref=observation_record.event_id,
            candidate_ref=candidate_record.event_id,
        )
        outcome_record = self.outcome(
            candidate_ref=candidate_record.event_id,
            probation_ref=probation_record.event_id,
        )
        terminal = self.settle(
            candidate=candidate,
            workspace=workspace,
            candidate_ref=candidate_record.event_id,
            outcome_ref=outcome_record.event_id,
        )
        if terminal.event_kind == "commit" and procedure is not None:
            self.consolidate(
                candidate=candidate,
                commit_ref=terminal.event_id,
                procedure=procedure,
            )
        return terminal


# -- in-memory smoke fixtures (direct run, not a test suite) -----------------


@dataclass
class _SmokeWorkspace:
    broken: bool = True
    fixed: bool = False
    probe_calls: int = 0


def _make_probe(workspace: _SmokeWorkspace) -> Probe:
    def probe() -> tuple[bool, dict[str, Any]]:
        workspace.probe_calls += 1
        return (
            not workspace.broken,
            {
                "phase": "probe",
                "broken": workspace.broken,
                "fixed": workspace.fixed,
                "calls": workspace.probe_calls,
            },
        )

    return probe


def _repair_candidate(candidate_id: str) -> CandidateRepair:
    def apply(workspace: _SmokeWorkspace) -> None:
        workspace.broken = False
        workspace.fixed = True

    def revert(workspace: _SmokeWorkspace) -> None:
        workspace.broken = True
        workspace.fixed = False

    return CandidateRepair(
        candidate_id=candidate_id,
        scope="smoke.recurring-defect",
        effect_kind="in_memory",
        plan={
            "kind": "smoke-repair",
            "applies": "clears the defect flag on the caller-owned workspace",
            "reverts": "restores the defect flag",
        },
        apply=apply,
        revert=revert,
    )


def _mis_scoped_candidate(candidate_id: str) -> CandidateRepair:
    def apply(workspace: _SmokeWorkspace) -> None:
        workspace.fixed = True  # repairs the wrong thing; defect remains

    def revert(workspace: _SmokeWorkspace) -> None:
        workspace.fixed = False

    return CandidateRepair(
        candidate_id=candidate_id,
        scope="smoke.mis-scoped-repair",
        effect_kind="in_memory",
        plan={
            "kind": "smoke-repair",
            "applies": "clears an unrelated flag; the defect remains",
            "reverts": "restores the unrelated flag",
        },
        apply=apply,
        revert=revert,
    )


_SMOKE_PROCEDURE: dict[str, Any] = {
    "procedure_id": "proc-smoke-repair-001",
    "kind": "reusable-repair",
    "scope": "smoke.recurring-defect",
    "steps": [
        "observe probe failure",
        "apply scoped reversible repair",
        "probe within the bounded probation window",
        "commit on pass",
    ],
}


def _smoke_passed(loop: AutonomousLoop) -> list[AutonomousLoopRecord]:
    """Probe fails once, then passes after the candidate; two committed cycles."""
    candidate = _repair_candidate("cand-smoke-001")
    for cycle in (1, 2):
        workspace = _SmokeWorkspace(broken=True)
        probe = _make_probe(workspace)
        passed, evidence = probe()  # fails once: this failure is the signal
        loop.run_cycle(
            signal_kind="probe_failure",
            source="smoke-fixture-passed",
            observation={
                "cycle": cycle,
                "probe_failed": not passed,
                "evidence": evidence,
            },
            candidate=candidate,
            probe=probe,
            workspace=workspace,
            procedure=_SMOKE_PROCEDURE,
        )
    return list(loop.records())


def _smoke_failed(loop: AutonomousLoop) -> list[AutonomousLoopRecord]:
    """Candidate does not fix the defect; outcome=failed, rollback, no consolidation."""
    candidate = _mis_scoped_candidate("cand-smoke-fail-001")
    workspace = _SmokeWorkspace(broken=True)
    probe = _make_probe(workspace)
    passed, evidence = probe()  # fails once: this failure is the signal
    loop.run_cycle(
        signal_kind="probe_failure",
        source="smoke-fixture-failed",
        observation={
            "cycle": 1,
            "probe_failed": not passed,
            "evidence": evidence,
        },
        candidate=candidate,
        probe=probe,
        workspace=workspace,
        procedure=_SMOKE_PROCEDURE,
    )
    return list(loop.records())


def _smoke_expectations(
    fixture: str,
    records: list[AutonomousLoopRecord],
    consolidation_count: int,
) -> tuple[bool, str]:
    kinds = [record.event_kind for record in records]
    if fixture == "passed":
        expected = {"observe", "candidate", "probation", "outcome", "commit", "consolidate"}
        if set(kinds) != expected:
            return False, f"passed fixture kinds mismatch: {kinds}"
        if consolidation_count != 1:
            return False, f"passed fixture expected 1 consolidation line, got {consolidation_count}"
        return True, "passed fixture: full cycle with commit and consolidation"
    expected = {"observe", "candidate", "probation", "outcome", "rollback"}
    if set(kinds) != expected:
        return False, f"failed fixture kinds mismatch: {kinds}"
    if consolidation_count != 0:
        return False, f"failed fixture expected no consolidation, got {consolidation_count}"
    return True, "failed fixture: rollback with no consolidation"


def run_smoke(
    *,
    fixture: str,
    output: Path,
    promotion_passes: int = DEFAULT_PROMOTION_PASSES_REQUIRED,
) -> int:
    """Run one smoke fixture and write the JSONL cycle records; return exit code."""
    home = Path(tempfile.mkdtemp(prefix="agentic-evo-loop-smoke-"))
    policy = LoopPolicy(promotion_passes_required=promotion_passes)
    loop = AutonomousLoop.create(
        home,
        instrument_version=SMOKE_INSTRUMENT_VERSION,
        protocol_version=AUTONOMOUS_LOOP_PROTOCOL_VERSION,
        policy=policy,
    )
    records = _smoke_passed(loop) if fixture == "passed" else _smoke_failed(loop)
    loop.verify()
    consolidation_count = len(loop.consolidation_records())
    lines = [
        json.dumps(
            asdict(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for record in records
    ]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        print(line, flush=True)
    ok, message = _smoke_expectations(
        fixture, records, consolidation_count
    )
    kinds = ",".join(record.event_kind for record in records) if records else ""
    print(
        (
            f"smoke fixture={fixture} exit={0 if ok else 1} "
            f"kinds={kinds} consolidation_store_lines={consolidation_count} "
            f"loop_home={home} check={message}"
        ),
        file=sys.stderr,
        flush=True,
    )
    return 0 if ok else 1


def smoke_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentic-evo autonomous-loop-smoke",
        description="Run an in-memory autonomous-loop smoke fixture and write JSONL records.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", choices=("passed", "failed"), default="passed")
    parser.add_argument(
        "--promotion-passes",
        type=int,
        default=DEFAULT_PROMOTION_PASSES_REQUIRED,
    )
    arguments = parser.parse_args(argv)
    return run_smoke(
        fixture=arguments.fixture,
        output=arguments.output,
        promotion_passes=arguments.promotion_passes,
    )


if __name__ == "__main__":
    raise SystemExit(smoke_main())
