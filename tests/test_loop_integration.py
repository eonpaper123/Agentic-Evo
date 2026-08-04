from __future__ import annotations

import ast
from pathlib import Path
import tempfile
import unittest

from agentic_evo.autonomous_loop import (
    AUTONOMOUS_LOOP_PROTOCOL_VERSION,
    AutonomousLoop,
    CandidateRepair,
    LoopPolicy,
)
from agentic_evo.errors import InvalidCandidateError, PolicyGateError
from agentic_evo.loop_integration import (
    DefectWorkspace,
    build_candidates,
    run_loop_demo,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_CALLER_IMPORTS = frozenset(
    {"trusted", "body", "kernel", "runtime", "witness"}
)
DEMO_INSTRUMENT_VERSION = "agentic-evo-loop-demo-1"

# Allowed predecessor event kinds for the loop state machine (Slice C flow).
# One observation may drive several candidate attempts in order, so a
# ``candidate`` may follow a ``rollback`` of the previous candidate.
_ALLOWED_PREDECESSORS = {
    "candidate": {"observe", "rollback"},
    "probation": {"candidate"},
    "outcome": {"probation"},
    "commit": {"outcome"},
    "rollback": {"outcome"},
    "consolidate": {"commit"},
    "observe": {"rollback", "commit"},
}


def _demo_policy() -> LoopPolicy:
    return LoopPolicy(authorize_external_effects=True)


class LoopIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _fresh_workspace(self, name: str = "ws") -> DefectWorkspace:
        return DefectWorkspace.create(self.root / name)

    # -- workspace creation --------------------------------------------------

    def test_workspace_failing_test_fails_and_correct_patch_passes(self) -> None:
        workspace = self._fresh_workspace()
        self.assertTrue(workspace.module_path.is_file())
        self.assertTrue(workspace.test_path.is_file())

        passed, exit_code, stdout, stderr = workspace.run_tests()
        self.assertFalse(passed)
        self.assertNotEqual(exit_code, 0)
        self.assertIn("FAILED", stdout + stderr)

        candidates = build_candidates(workspace)
        correct = [
            candidate
            for candidate in candidates
            if candidate.candidate_id == "cand-window-correct-fix"
        ]
        self.assertEqual(len(correct), 1)
        correct[0].apply(workspace)
        passed, exit_code, stdout, stderr = workspace.run_tests()
        self.assertTrue(passed)
        self.assertEqual(exit_code, 0)
        self.assertIn("OK", stdout + stderr)

    # -- full demo cycle -----------------------------------------------------

    def test_full_demo_cycle_final_module_passes_and_chain_verifies(self) -> None:
        workspace = self._fresh_workspace()
        loop_home = self.root / "loop-home"
        report = run_loop_demo(workspace, loop_home)

        self.assertTrue(report["final_module_passed"])
        self.assertEqual(report["final_status"], "consolidated")
        self.assertGreaterEqual(report["consolidation_count"], 1)
        self.assertEqual(
            report["candidate_outcomes"]["cand-window-wrong-fix"],
            "failed",
        )
        self.assertEqual(
            report["candidate_outcomes"]["cand-window-correct-fix"],
            "passed",
        )
        self.assertGreaterEqual(report["outcome_counts"]["failed"], 1)
        self.assertGreaterEqual(report["outcome_counts"]["passed"], 2)
        self.assertIn(
            "range(len(values) - n + 1)",
            workspace.module_path.read_text(encoding="utf-8"),
        )

        loop = AutonomousLoop.load(loop_home)
        self.assertTrue(loop.verify())
        records = loop.records()
        by_id = {record.event_id: record for record in records}
        for record in records:
            if record.observation_ref is not None:
                self.assertEqual(
                    by_id[record.observation_ref].event_kind,
                    "observe",
                )
            if record.candidate_ref is not None:
                self.assertEqual(
                    by_id[record.candidate_ref].event_kind,
                    "candidate",
                )
            if record.probation_ref is not None:
                self.assertEqual(
                    by_id[record.probation_ref].event_kind,
                    "probation",
                )
            if record.outcome_ref is not None:
                self.assertEqual(
                    by_id[record.outcome_ref].event_kind,
                    "outcome",
                )
            if record.commit_ref is not None:
                self.assertEqual(
                    by_id[record.commit_ref].event_kind,
                    "commit",
                )

        kinds = [record.event_kind for record in records]
        self.assertEqual(kinds[0], "observe")
        self.assertIn("rollback", kinds)
        self.assertIn("commit", kinds)
        self.assertEqual(kinds[-1], "consolidate")
        for previous, current in zip(kinds, kinds[1:]):
            self.assertIn(
                previous,
                _ALLOWED_PREDECESSORS[current],
                f"invalid transition {previous} -> {current}",
            )

        consolidation = loop.consolidation_records()
        self.assertGreaterEqual(len(consolidation), 1)
        self.assertEqual(
            consolidation[0]["candidate_id"],
            "cand-window-correct-fix",
        )
        self.assertTrue(consolidation[0]["procedure"].get("procedure_id"))

    # -- failed-candidate rollback -------------------------------------------

    def test_failed_candidate_rollback_restores_original_bytes(self) -> None:
        workspace = self._fresh_workspace()
        original = workspace.module_path.read_bytes()
        loop_home = self.root / "loop-home"
        loop = AutonomousLoop.create(
            loop_home,
            instrument_version=DEMO_INSTRUMENT_VERSION,
            protocol_version=AUTONOMOUS_LOOP_PROTOCOL_VERSION,
            policy=_demo_policy(),
        )
        bad = build_candidates(workspace)[0]
        self.assertEqual(bad.candidate_id, "cand-window-wrong-fix")

        passed, _, _, _ = workspace.run_tests()
        observation = loop.observe(
            signal_kind="test_failure",
            source="defect_workspace",
            observation={"defect": workspace.defect, "test_passed": passed},
        )

        def probe() -> tuple[bool, dict[str, object]]:
            probe_passed, exit_code, _, _ = workspace.run_tests()
            return probe_passed, {"exit_code": exit_code, "passed": probe_passed}

        candidate_record = loop.propose(
            candidate=bad,
            observation_ref=observation.event_id,
        )
        probation_record = loop.probation(
            candidate=bad,
            probe=probe,
            workspace=workspace,
            observation_ref=observation.event_id,
            candidate_ref=candidate_record.event_id,
        )
        outcome_record = loop.outcome(
            candidate_ref=candidate_record.event_id,
            probation_ref=probation_record.event_id,
        )
        self.assertEqual(outcome_record.payload["outcome"], "failed")
        terminal = loop.settle(
            candidate=bad,
            workspace=workspace,
            candidate_ref=candidate_record.event_id,
            outcome_ref=outcome_record.event_id,
        )
        self.assertEqual(terminal.event_kind, "rollback")
        self.assertTrue(terminal.payload["reverted"])
        self.assertEqual(workspace.module_path.read_bytes(), original)

    # -- rerunnable ----------------------------------------------------------

    def test_rerunnable_same_final_outcome_on_fresh_dirs(self) -> None:
        first_workspace = self._fresh_workspace("ws-a")
        first_report = run_loop_demo(first_workspace, self.root / "loop-a")
        second_workspace = self._fresh_workspace("ws-b")
        second_report = run_loop_demo(second_workspace, self.root / "loop-b")
        for key in (
            "final_module_passed",
            "final_status",
            "defect",
            "consolidation_count",
        ):
            self.assertEqual(first_report[key], second_report[key], key)
        self.assertEqual(
            first_report["outcome_counts"],
            second_report["outcome_counts"],
        )
        self.assertTrue(first_report["final_module_passed"])

    # -- policy gate ---------------------------------------------------------

    def test_policy_gate_refuses_life_core_scope_and_unauthorized_external(
        self,
    ) -> None:
        loop = AutonomousLoop.create(
            self.root / "loop-home",
            instrument_version=DEMO_INSTRUMENT_VERSION,
            protocol_version=AUTONOMOUS_LOOP_PROTOCOL_VERSION,
        )

        def apply(_workspace: object) -> None:
            return None

        def revert(_workspace: object) -> None:
            return None

        life_core_candidate = CandidateRepair(
            candidate_id="cand-life-core-scope",
            scope="who",
            effect_kind="in_memory",
            plan={"kind": "refused"},
            apply=apply,
            revert=revert,
        )
        with self.assertRaises(InvalidCandidateError):
            loop.propose(candidate=life_core_candidate)

        external_candidate = CandidateRepair(
            candidate_id="cand-unauthorized-external",
            scope="workspace.util_patch",
            effect_kind="external",
            plan={"kind": "refused"},
            apply=apply,
            revert=revert,
        )
        with self.assertRaises(PolicyGateError):
            loop.propose(candidate=external_candidate)

    # -- caller-owned import boundary (AST check) ----------------------------

    def test_caller_owned_import_boundary(self) -> None:
        source_path = (
            REPO_ROOT / "src" / "agentic_evo" / "loop_integration.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        self.assertTrue(imported)
        for name in imported:
            top_level = name.split(".")[0]
            self.assertNotIn(
                top_level,
                FORBIDDEN_CALLER_IMPORTS,
                f"{name} crosses the caller-owned import boundary",
            )


if __name__ == "__main__":
    unittest.main()
