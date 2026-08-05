"""Memory-to-Capability Compiler tests (first closed loop, slice MC-1)."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agentic_evo.errors import MemoryIntegrityError, MemoryRecordError
from agentic_evo.memory_capability_compiler import (
    CapabilityRegistry,
    MemoryCapabilityCompiler,
    apply_capability,
    make_capability_card,
    matches_trigger,
    successful_camu_evaluator,
    verify_behavior_change,
)
from agentic_evo.memory_store import MemoryStore


def _valid_camu(**overrides: object) -> dict:
    record = {
        "G": {
            "evidence_refs": [{"sequence": 1, "hash": "a" * 64}],
            "provenance": (
                "reproduce the failure with a failing unit test\n"
                "change the loop range to include the final start index\n"
                "run the full test suite and confirm all windows returned"
            ),
            "collected_at": "2026-08-05T18:00:00+08:00",
        },
        "A": {"problem_class": "off_by_one_window"},
        "I": {
            "domain": "executable_skill",
            "description": "how to fix a sliding-window off-by-one",
        },
        "P": {
            "condition": "a task exhibits the sliding-window off-by-one signature",
            "expected": "the fix returns every window including the last",
            "counterfactual": "without this memory the last window is dropped",
            "status": "pending",
        },
        "E": {"uncertainty": 0.2},
    }
    record.update(overrides)
    return record


class _FakeSolver:
    """Deterministic solver: baseline never fixes; execute uses provided steps."""

    def execute(self, steps: list[str], context: dict) -> dict:
        return {
            "fixed": True,
            "used_steps": len(steps),
            "first_step": steps[0] if steps else None,
            "context": context,
        }

    def baseline(self, context: dict) -> dict:
        return {"fixed": False, "reason": "no capability available", "context": context}


class CompilerClosedLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.store_path = root / "memory" / "camus.jsonl"
        self.reg_path = root / "capabilities" / "capabilities.jsonl"
        self.store = MemoryStore.create(self.store_path)
        self.registry = CapabilityRegistry(self.reg_path)
        self.compiler = MemoryCapabilityCompiler(self.registry, min_support=1)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _seed_verified_skill(self, *, provenance_solution: list[str] | None = None) -> str:
        overrides: dict = {}
        if provenance_solution is not None:
            overrides["G"] = {
                "evidence_refs": [{"sequence": 1, "hash": "a" * 64}],
                "provenance": "\n".join(provenance_solution),
                "collected_at": "2026-08-05T18:00:00+08:00",
            }
        camu_id = self.store.add_camu(_valid_camu(**overrides))
        self.store.record_outcome(camu_id, {"ok": True}, matched=True)
        return camu_id

    def test_compile_produces_card_from_verified_experience(self) -> None:
        camu_id = self._seed_verified_skill()
        context = {"problem_class": "off_by_one_window"}
        cards = self.compiler.compile(self.store, context)
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["domain"], "executable_skill")
        self.assertEqual(card["trigger"], {"problem_class": "off_by_one_window"})
        self.assertGreaterEqual(len(card["procedure"]), 3)
        self.assertIn(camu_id, card["evidence_refs"])
        self.assertEqual(card["status"], "candidate")

    def test_unverified_experience_does_not_compile(self) -> None:
        self.store.add_camu(_valid_camu())
        context = {"problem_class": "off_by_one_window"}
        cards = self.compiler.compile(self.store, context)
        self.assertEqual(cards, [])

    def test_register_and_verify_chain(self) -> None:
        camu_id = self._seed_verified_skill()
        cards = self.compiler.compile(self.store, {"problem_class": "off_by_one_window"})
        self.assertEqual(len(cards), 1)
        card_id = self.registry.register(cards[0])
        self.assertEqual(self.registry.count(), 1)
        self.assertTrue(self.registry.verify_chain())
        self.assertEqual(self.registry.get(card_id)["id"], cards[0]["id"])

    def test_tamper_detected(self) -> None:
        camu_id = self._seed_verified_skill()
        cards = self.compiler.compile(self.store, {"problem_class": "off_by_one_window"})
        self.registry.register(cards[0])
        lines = self.reg_path.read_text(encoding="utf-8").strip().splitlines()
        last = json.loads(lines[-1])
        last["card"]["procedure"][0] = "tampered step"
        self.reg_path.write_text(
            "\n".join(lines[:-1]) + "\n" + json.dumps(last, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(MemoryIntegrityError):
            self.registry.verify_chain()

    def test_duplicate_register_rejected(self) -> None:
        camu_id = self._seed_verified_skill()
        cards = self.compiler.compile(self.store, {"problem_class": "off_by_one_window"})
        self.registry.register(cards[0])
        with self.assertRaises(MemoryRecordError):
            self.registry.register(cards[0])

    def test_apply_uses_card_when_trigger_matches(self) -> None:
        camu_id = self._seed_verified_skill()
        cards = self.compiler.compile(self.store, {"problem_class": "off_by_one_window"})
        card = cards[0]
        solver = _FakeSolver()
        result = apply_capability(card, {"problem_class": "off_by_one_window"}, solver)
        self.assertTrue(result["used_capability"])
        self.assertTrue(result["result"]["fixed"])
        self.assertGreaterEqual(result["result"]["used_steps"], 3)

    def test_apply_falls_back_when_trigger_misses(self) -> None:
        camu_id = self._seed_verified_skill()
        cards = self.compiler.compile(self.store, {"problem_class": "off_by_one_window"})
        solver = _FakeSolver()
        result = apply_capability(card=cards[0], task_context={"other": 1}, solver=solver)
        self.assertFalse(result["used_capability"])
        self.assertFalse(result["result"]["fixed"])

    def test_verify_behavior_change_operationalizes_memory_definition(self) -> None:
        camu_id = self._seed_verified_skill()
        cards = self.compiler.compile(self.store, {"problem_class": "off_by_one_window"})
        solver = _FakeSolver()
        check = verify_behavior_change(
            cards[0], {"problem_class": "off_by_one_window"}, solver
        )
        self.assertTrue(check["with_card"]["used_capability"])
        self.assertFalse(check["baseline"]["result"]["fixed"])
        self.assertTrue(check["changed"])

    def test_compile_and_register_end_to_end(self) -> None:
        camu_id = self._seed_verified_skill()
        cards = self.compiler.compile_and_register(
            self.store, {"problem_class": "off_by_one_window"}
        )
        self.assertEqual(len(cards), 1)
        self.assertEqual(self.registry.count(), 1)
        registered = self.registry.list()[0]["card"]
        self.assertEqual(registered["procedure"], cards[0]["procedure"])

    def test_matches_trigger_helpers(self) -> None:
        card = make_capability_card(
            domain="executable_skill",
            trigger={"problem_class": "off_by_one_window"},
            procedure=["a", "b"],
            expected_outcome={"description": "ok"},
            evidence_refs=["x" * 64],
        )
        self.assertTrue(matches_trigger(card, {"problem_class": "off_by_one_window"}))
        self.assertFalse(matches_trigger(card, {"problem_class": "other"}))
        self.assertFalse(matches_trigger(card, {}))

    def test_successful_evaluator_requires_verified(self) -> None:
        camu_id = self.store.add_camu(_valid_camu())
        context = {"problem_class": "off_by_one_window"}
        self.assertEqual(self.store.recall(context, evaluator=successful_camu_evaluator), [])
        self.store.record_outcome(camu_id, {"ok": True}, matched=True)
        self.assertEqual(
            self.store.recall(context, evaluator=successful_camu_evaluator), [camu_id]
        )


if __name__ == "__main__":
    unittest.main()
