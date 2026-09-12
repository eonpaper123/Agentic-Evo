"""Caller-owned real-task autonomous-loop integration demo (Slice C).

This module is the *caller* of the autonomous-loop engine for one real
(synthetic-but-real) defect repair flow:

- ``DefectWorkspace.create``  -> writes a real ``util.py`` with a planted
  off-by-one defect plus a real failing unittest into a caller-owned dir;
- ``build_candidates``        -> ordered, reversible text-patch ``CandidateRepair``
  objects (wrong fix first, then the correct fix);
- ``run_loop_demo``           -> drives one full
  observe -> candidate -> probation -> outcome -> commit|rollback -> consolidate
  cycle against the real workspace using ``AutonomousLoop`` and returns a JSON
  report.

Pure stdlib and caller-owned: imports only the ``autonomous_loop`` engine and
``_util`` helpers; never imports trusted/body/kernel/runtime/witness.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable, Mapping

from .autonomous_loop import (
    AUTONOMOUS_LOOP_PROTOCOL_VERSION,
    AutonomousLoop,
    CandidateRepair,
    LoopPolicy,
)


LOOP_DEMO_INSTRUMENT_VERSION = "agentic-evo-loop-demo-1"
LOOP_DEMO_DEFECTS = frozenset({"off_by_one"})
TEST_RUN_TIMEOUT_SECONDS = 60

_MODULE_FILENAME = "util.py"
_TEST_FILENAME = "test_util.py"

# Deterministic demo module: ``window(values, n)`` with a planted off-by-one
# defect in the range bound (drops the final window).
_MODULE_SOURCE = '''"""Deterministic demo module with a planted off-by-one defect (Slice C)."""


def window(values, n):
    """Return the length-n sliding windows of ``values``; planted off-by-one."""
    if n <= 0:
        raise ValueError("n must be positive")
    return [values[i : i + n] for i in range(len(values) - n)]
'''

# Real unittest: fails on the buggy module, passes on the correct module.
_TEST_SOURCE = '''"""Real unittest for the demo module; fails on the planted defect."""

import unittest

import util


class WindowTests(unittest.TestCase):
    def test_window_returns_all_full_windows(self) -> None:
        self.assertEqual(
            util.window([1, 2, 3, 4, 5], 3),
            [[1, 2, 3], [2, 3, 4], [3, 4, 5]],
        )

    def test_window_single_element_windows(self) -> None:
        self.assertEqual(util.window([1, 2, 3], 1), [[1], [2], [3]])

    def test_window_rejects_non_positive_size(self) -> None:
        with self.assertRaises(ValueError):
            util.window([1, 2, 3], 0)


if __name__ == "__main__":
    unittest.main()
'''

_BUGGY_LINE = "    return [values[i : i + n] for i in range(len(values) - n)]"
# The wrong fix has a distinct line length from both the buggy line and the
# correct line so rapid same-second patches can never be masked by stale
# ``__pycache__`` bytecode (pyc validity is mtime-seconds + size).
_WRONG_FIX_LINE = (
    "    return [values[i : i + n] for i in range(len(values))]"
)
_CORRECT_FIX_LINE = (
    "    return [values[i : i + n] for i in range(len(values) - n + 1)]"
)

_WRONG_CANDIDATE_ID = "cand-window-wrong-fix"
_CORRECT_CANDIDATE_ID = "cand-window-correct-fix"
_CANDIDATE_SCOPE = "workspace.util_patch"


def _patch_apply(target_line: str) -> Callable[[DefectWorkspace], None]:
    """Build an idempotent text-swap apply for one target line."""

    def apply(workspace: DefectWorkspace) -> None:
        current = workspace.module_path.read_bytes()
        text = current.decode("utf-8")
        if target_line in text:
            return  # already in the target state; idempotent re-apply
        if _BUGGY_LINE not in text:
            raise RuntimeError(
                f"cannot locate the planted defect line in {workspace.module_path}"
            )
        workspace.module_path.write_bytes(
            text.replace(_BUGGY_LINE, target_line, 1).encode("utf-8")
        )

    return apply


def _patch_revert() -> Callable[[DefectWorkspace], None]:
    """Build a revert that restores the exact original module bytes."""

    def revert(workspace: DefectWorkspace) -> None:
        workspace.module_path.write_bytes(workspace.original_source)

    return revert


@dataclass
class DefectWorkspace:
    """Caller-owned real workspace: a module with a planted defect + real test.

    ``run_tests()`` executes the current interpreter's ``python -m unittest
    test_util -v`` inside the workspace directory and returns
    ``(passed, exit_code, stdout, stderr)``.
    """

    root: Path
    defect: str
    module_path: Path
    test_path: Path
    original_source: bytes

    @classmethod
    def create(
        cls,
        root: Path | str,
        defect: str = "off_by_one",
    ) -> "DefectWorkspace":
        """Write the deterministic demo module + failing unittest into ``root``."""
        root = Path(root)
        if defect not in LOOP_DEMO_DEFECTS:
            raise ValueError(f"unsupported demo defect: {defect!r}")
        root.mkdir(parents=True, exist_ok=True)
        module_path = root / _MODULE_FILENAME
        test_path = root / _TEST_FILENAME
        module_bytes = _MODULE_SOURCE.encode("utf-8")
        module_path.write_bytes(module_bytes)
        test_path.write_bytes(_TEST_SOURCE.encode("utf-8"))
        return cls(
            root=root,
            defect=defect,
            module_path=module_path,
            test_path=test_path,
            original_source=module_bytes,
        )

    def run_tests(self) -> tuple[bool, int, str, str]:
        """Run the real unittest in this workspace with the current interpreter.

        ``__pycache__`` is purged first so the probe always compiles the
        patched source and never trusts stale bytecode (Windows pyc validity
        is second-granularity mtime + size, which can mask same-second,
        same-size patches).
        """
        shutil.rmtree(self.root / "__pycache__", ignore_errors=True)
        process = subprocess.run(
            [sys.executable, "-m", "unittest", "test_util", "-v"],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=TEST_RUN_TIMEOUT_SECONDS,
        )
        return (
            process.returncode == 0,
            process.returncode,
            process.stdout,
            process.stderr,
        )


def build_candidates(workspace: DefectWorkspace) -> list[CandidateRepair]:
    """Return the ordered, reversible patch candidates for the workspace.

    candidate-1 is a wrong fix that still fails the real test; candidate-2 is
    the correct fix that passes. Both are reversible text-file swaps with
    byte-identical restore (revert writes back the exact original bytes).
    """

    def candidate(
        candidate_id: str,
        target_line: str,
        description: str,
    ) -> CandidateRepair:
        return CandidateRepair(
            candidate_id=candidate_id,
            scope=_CANDIDATE_SCOPE,
            effect_kind="external",
            plan={
                "kind": "text_patch",
                "defect": workspace.defect,
                "module": workspace.module_path.name,
                "applies": description,
                "reverts": "restores the exact original module bytes",
            },
            apply=_patch_apply(target_line),
            revert=_patch_revert(),
            reversible=True,
            touches_life_core=False,
        )

    return [
        candidate(
            _WRONG_CANDIDATE_ID,
            _WRONG_FIX_LINE,
            "replaces the planted line with an off-by-one fix that is still wrong",
        ),
        candidate(
            _CORRECT_CANDIDATE_ID,
            _CORRECT_FIX_LINE,
            "replaces the planted line with the correct off-by-one fix",
        ),
    ]


def _make_probe(workspace: DefectWorkspace) -> Callable[[], tuple[bool, Mapping[str, Any]]]:
    """Probe: run the real test suite in the workspace and report evidence."""

    def probe() -> tuple[bool, Mapping[str, Any]]:
        passed, exit_code, stdout, stderr = workspace.run_tests()
        return passed, {
            "passed": passed,
            "exit_code": exit_code,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-2000:],
        }

    return probe


def _observe(loop: AutonomousLoop, workspace: DefectWorkspace) -> Any:
    passed, exit_code, stdout, stderr = workspace.run_tests()
    return loop.observe(
        signal_kind="test_pass" if passed else "test_failure",
        source="defect_workspace",
        observation={
            "defect": workspace.defect,
            "test_passed": passed,
            "exit_code": exit_code,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-2000:],
        },
    )


def _run_cycle(
    loop: AutonomousLoop,
    workspace: DefectWorkspace,
    candidate: CandidateRepair,
    observation_ref: str,
    probe: Callable[[], tuple[bool, Mapping[str, Any]]],
) -> tuple[Any, Any, Any, Any]:
    """propose -> probation -> outcome -> settle; return the stage records."""
    candidate_record = loop.propose(
        candidate=candidate,
        observation_ref=observation_ref,
    )
    probation_record = loop.probation(
        candidate=candidate,
        probe=probe,
        workspace=workspace,
        observation_ref=observation_ref,
        candidate_ref=candidate_record.event_id,
    )
    outcome_record = loop.outcome(
        candidate_ref=candidate_record.event_id,
        probation_ref=probation_record.event_id,
    )
    terminal = loop.settle(
        candidate=candidate,
        workspace=workspace,
        candidate_ref=candidate_record.event_id,
        outcome_ref=outcome_record.event_id,
    )
    return candidate_record, probation_record, outcome_record, terminal


def _build_procedure(
    workspace: DefectWorkspace,
    candidate: CandidateRepair,
) -> dict[str, Any]:
    return {
        "procedure_id": f"proc-{candidate.candidate_id}",
        "kind": "reusable-repair",
        "scope": candidate.scope,
        "defect": workspace.defect,
        "steps": [
            "observe the real failing unittest in the workspace",
            "apply the scoped reversible text patch",
            "probe by running the real unittest within the bounded window",
            "commit on pass",
            "rerun the fixed module through the loop to promotion passes",
            "consolidate the reusable procedure",
        ],
    }


def run_loop_demo(
    workspace: DefectWorkspace,
    loop_home: Path | str,
    *,
    policy: LoopPolicy | None = None,
    loop_id: str | None = None,
) -> dict[str, Any]:
    """Drive one full real-task autonomous-loop cycle and return a JSON report.

    The demo policy authorizes external effects (the candidates patch real
    files in the caller-owned workspace). ``policy`` may override it.
    """
    loop_home = Path(loop_home)
    if policy is None:
        policy = LoopPolicy(authorize_external_effects=True)
    loop = AutonomousLoop.create(
        loop_home,
        instrument_version=LOOP_DEMO_INSTRUMENT_VERSION,
        protocol_version=AUTONOMOUS_LOOP_PROTOCOL_VERSION,
        loop_id=loop_id,
        policy=policy,
    )
    candidates = build_candidates(workspace)
    probe = _make_probe(workspace)
    outcome_counts: dict[str, int] = {"passed": 0, "failed": 0, "inconclusive": 0}
    candidate_outcomes: dict[str, str] = {}

    initial_passed, _, _, _ = workspace.run_tests()
    if initial_passed:
        raise ValueError("workspace is not in the expected failing state")
    observation = _observe(loop, workspace)

    passing_candidate: CandidateRepair | None = None
    first_commit_ref: str | None = None
    for candidate in candidates:
        _, _, outcome_record, terminal = _run_cycle(
            loop,
            workspace,
            candidate,
            observation.event_id,
            probe,
        )
        verdict = outcome_record.payload["outcome"]
        outcome_counts[verdict] += 1
        candidate_outcomes[candidate.candidate_id] = verdict
        if terminal.event_kind == "rollback":
            if workspace.module_path.read_bytes() != workspace.original_source:
                raise RuntimeError(
                    f"rollback did not restore original bytes for {candidate.candidate_id}"
                )
        else:
            passing_candidate = candidate
            first_commit_ref = terminal.event_id
            break

    if passing_candidate is not None and first_commit_ref is not None:
        procedure = _build_procedure(workspace, passing_candidate)
        promotion_ready = loop.policy.promotion_passes_required <= 1
        commit_ref = first_commit_ref
        if not promotion_ready:
            # Run the passing candidate again through the loop to reach
            # promotion_passes_required, then consolidate.
            second_observation = _observe(loop, workspace)
            _, _, outcome_record, terminal = _run_cycle(
                loop,
                workspace,
                passing_candidate,
                second_observation.event_id,
                probe,
            )
            verdict = outcome_record.payload["outcome"]
            outcome_counts[verdict] += 1
            if terminal.event_kind == "commit":
                commit_ref = terminal.event_id
        loop.consolidate(
            candidate=passing_candidate,
            commit_ref=commit_ref,
            procedure=procedure,
        )

    loop.verify()
    final_passed, final_exit_code, _, _ = workspace.run_tests()
    consolidation_records = loop.consolidation_records()
    consolidated = bool(consolidation_records)
    if consolidated:
        final_status = "consolidated"
    elif final_passed:
        final_status = "fixed_unconsolidated"
    else:
        final_status = "unfixed"

    return {
        "workspace": str(workspace.root),
        "loop_home": str(loop_home),
        "events_path": str(loop.log_path),
        "consolidation_path": str(loop.consolidation_path),
        "defect": workspace.defect,
        "candidate_ids": [candidate.candidate_id for candidate in candidates],
        "final_module_passed": final_passed,
        "final_test_exit_code": final_exit_code,
        "final_status": final_status,
        "outcome_counts": outcome_counts,
        "candidate_outcomes": candidate_outcomes,
        "consolidation_count": len(consolidation_records),
        "event_kinds": [record.event_kind for record in loop.records()],
    }
