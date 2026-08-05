"""MC-6 tests: capability outcome tracking + measurement report."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agentic_evo.errors import MemoryRecordError
from agentic_evo.memory_capability_compiler import (
    CapabilityRegistry,
    MemoryCapabilityCompiler,
)
from agentic_evo.memory_capability_report import (
    CapabilityOutcomeLog,
    build_capability_report,
    render_report_text,
)
from agentic_evo.memory_store import MemoryStore


CARD_ID = "0" * 64


def _verified_camu(store: MemoryStore, task_class: str) -> str:
    """Add a CAMU and mark it verified through store bookkeeping."""
    camu = {
        "G": {
            "evidence_refs": [],
            "provenance": "fix it\ndo the right thing",
            "collected_at": "2026-08-06T01:00:00+08:00",
        },
        "A": {"task_class": task_class},
        "I": {"domain": "executable_skill", "description": "verified fix"},
        "P": {
            "condition": f"a task matching {task_class} occurs",
            "expected": f"apply the {task_class} fix",
            "counterfactual": "re-derive from scratch",
            "status": "pending",
        },
        "E": {"uncertainty": 0.25},
    }
    camu_id = store.add_camu(camu)
    store.record_outcome(camu_id, {"ok": True}, matched=True)
    return camu_id


def _register_card(registry: CapabilityRegistry, store: MemoryStore, task_class: str) -> dict:
    _verified_camu(store, task_class)
    compiler = MemoryCapabilityCompiler(registry)
    cards = compiler.compile_and_register(store, {"task_class": task_class})
    assert cards, "expected a compiled card"
    return cards[0]


class OutcomeLogTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.log = CapabilityOutcomeLog(self.root / "outcomes.jsonl")
        self.store = MemoryStore.create(self.root / "camus.jsonl")
        self.registry = CapabilityRegistry(self.root / "capabilities.jsonl")

    def test_use_outcome_chain_verifies(self) -> None:
        use_id = self.log.record_use(
            CARD_ID, {"task_class": "t"}, session_id="s1", provenance="test"
        )
        outcome_id = self.log.record_outcome(
            CARD_ID, ok=True, session_id="s1", details={"task_class": "t"}
        )
        self.assertNotEqual(use_id, outcome_id)
        self.assertEqual(self.log.count(), 2)
        self.assertTrue(self.log.verify_chain())

    def test_rejects_bad_card_id(self) -> None:
        with self.assertRaises(MemoryRecordError):
            self.log.record_use("not-a-hash", {})
        with self.assertRaises(MemoryRecordError):
            self.log.record_outcome("not-a-hash", ok=True)

    def test_tamper_detected(self) -> None:
        self.log.record_use(CARD_ID, {"k": "v"})
        self.log.record_outcome(CARD_ID, ok=True)
        raw = self.log.path.read_text(encoding="utf-8")
        raw = raw.replace('"ok": true', '"ok": false', 1)
        self.log.path.write_text(raw, encoding="utf-8")
        with self.assertRaises(Exception):
            self.log.verify_chain()

    def test_per_card_stats(self) -> None:
        card = _register_card(self.registry, self.store, "off_by_one_window")
        card_id = card["id"]
        self.log.record_use(
            card_id, {"task_class": "off_by_one_window"}, session_id="s1"
        )
        self.log.record_outcome(
            card_id, ok=True, session_id="s1", details={"task_class": "off_by_one_window"}
        )
        self.log.record_outcome(
            card_id, ok=False, session_id="s2", details={"task_class": "off_by_one_window"}
        )
        report = build_capability_report(self.registry, self.log)
        self.assertTrue(report["summary"]["chain_verified"])
        self.assertEqual(report["summary"]["cards"], 1)
        self.assertEqual(report["summary"]["uses"], 1)
        self.assertEqual(report["summary"]["outcomes"], 2)
        self.assertEqual(report["summary"]["ok"], 1)
        self.assertEqual(report["summary"]["overall_success_rate"], 0.5)
        stats = report["cards"][0]
        self.assertEqual(stats["matched_outcomes"], 1)
        self.assertEqual(stats["ok"], 1)
        self.assertEqual(stats["fail"], 1)

    def test_task_class_delta(self) -> None:
        card = _register_card(self.registry, self.store, "duration_parser")
        card_id = card["id"]
        self.log.record_use(
            card_id, {"task_class": "duration_parser"}, session_id="s1"
        )
        self.log.record_outcome(
            card_id, ok=True, session_id="s1", details={"task_class": "duration_parser"}
        )
        self.log.record_outcome(
            card_id, ok=False, session_id="s2", details={"task_class": "duration_parser"}
        )
        report = build_capability_report(self.registry, self.log)
        rows = {r["task_class"]: r for r in report["by_task_class"]}
        row = rows["duration_parser"]
        self.assertEqual(row["with_card"]["sessions"], 1)
        self.assertEqual(row["with_card"]["ok"], 1)
        self.assertEqual(row["without_card"]["sessions"], 1)
        self.assertEqual(row["without_card"]["ok"], 0)
        self.assertEqual(row["delta"], 1.0)

    def test_render_text_has_sections(self) -> None:
        card = _register_card(self.registry, self.store, "median_even_length")
        self.log.record_use(
            card["id"], {"task_class": "median_even_length"}, session_id="s1"
        )
        text = render_report_text(build_capability_report(self.registry, self.log))
        self.assertIn("Per-card", text)
        self.assertIn("Per-task-class", text)
        self.assertIn(card["id"][:12], text)
        self.assertIn("uses=1", text)

    def test_empty_report(self) -> None:
        report = build_capability_report(self.registry, self.log)
        self.assertEqual(report["cards"], [])
        self.assertEqual(report["summary"]["cards"], 0)


if __name__ == "__main__":
    unittest.main()
