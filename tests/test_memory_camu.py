"""CAMU memory store tests (slice B)."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
import inspect
import json
from pathlib import Path
import tempfile
import types
import unittest

import agentic_evo.memory_store as memory_store_module
from agentic_evo.errors import MemoryIntegrityError, MemoryRecordError
from agentic_evo.memory_store import MemoryStore


def _valid_camu(**overrides: object) -> dict:
    record = {
        "G": {
            "evidence_refs": [{"sequence": 1, "hash": "a" * 64}],
            "provenance": "slice-b test fixture",
            "collected_at": "2026-08-04T18:00:00+08:00",
        },
        "A": {"domain": "codex", "when": "compile_error"},
        "I": {"domain": "tool_selection", "description": "prefer the verified fix"},
        "P": {
            "condition": "a compile_error occurs",
            "expected": "the fix reduces error count",
            "counterfactual": "without this memory the fix is not attempted",
            "status": "pending",
        },
        "E": {"uncertainty": 0.5},
    }
    record.update(overrides)
    return record


class MemoryStoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "memory" / "camus.jsonl"
        self.store = MemoryStore.create(self.path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_add_requires_every_contract_field(self) -> None:
        for field in ("G", "A", "I", "P", "E"):
            record = _valid_camu()
            del record[field]
            with self.assertRaises(MemoryRecordError):
                self.store.add_camu(record)

    def test_add_rejects_unknown_top_level_fields(self) -> None:
        record = _valid_camu()
        record["X"] = "extra"
        with self.assertRaises(MemoryRecordError):
            self.store.add_camu(record)

    def test_add_requires_required_subfields(self) -> None:
        missing_influence_domain = _valid_camu()
        del missing_influence_domain["I"]["domain"]
        with self.assertRaises(MemoryRecordError):
            self.store.add_camu(missing_influence_domain)
        missing_prediction_condition = _valid_camu()
        del missing_prediction_condition["P"]["condition"]
        with self.assertRaises(MemoryRecordError):
            self.store.add_camu(missing_prediction_condition)
        missing_uncertainty = _valid_camu()
        del missing_uncertainty["E"]["uncertainty"]
        with self.assertRaises(MemoryRecordError):
            self.store.add_camu(missing_uncertainty)
        missing_provenance = _valid_camu()
        del missing_provenance["G"]["provenance"]
        with self.assertRaises(MemoryRecordError):
            self.store.add_camu(missing_provenance)

    def test_unknown_influence_domain_rejected(self) -> None:
        record = _valid_camu()
        record["I"] = {"domain": "telepathy", "description": "not in the contract"}
        with self.assertRaises(MemoryRecordError):
            self.store.add_camu(record)

    def test_non_pending_initial_status_rejected(self) -> None:
        record = _valid_camu()
        record["P"]["status"] = "verified"
        with self.assertRaises(MemoryRecordError):
            self.store.add_camu(record)

    def test_bad_evidence_refs_shape_rejected(self) -> None:
        bad_refs = [
            "not-a-list",
            [{"sequence": "1", "hash": "a" * 64}],
            [{"sequence": 1}],
            [{"sequence": 0, "hash": "a" * 64}],
            [{"sequence": 1, "hash": "not-hex"}],
            [{"sequence": 1, "hash": "A" * 64}],
        ]
        for refs in bad_refs:
            record = _valid_camu()
            record["G"]["evidence_refs"] = refs
            with self.assertRaises(MemoryRecordError):
                self.store.add_camu(record)

    def test_valid_evidence_refs_accepted_and_preserved(self) -> None:
        record = _valid_camu()
        record["G"]["evidence_refs"] = [
            {"sequence": 1, "hash": "a" * 64},
            {"sequence": 2, "hash": "b" * 64},
        ]
        camu_id = self.store.add_camu(record)
        stored = self.store.get(camu_id)["record"]
        self.assertEqual(stored["G"]["evidence_refs"], record["G"]["evidence_refs"])

    def test_content_address_is_deterministic(self) -> None:
        first_id = self.store.add_camu(_valid_camu())
        other = MemoryStore.create(self.path.with_name("other.jsonl"))
        second_id = other.add_camu(_valid_camu())
        self.assertEqual(first_id, second_id)

    def test_duplicate_content_address_rejected(self) -> None:
        camu_id = self.store.add_camu(_valid_camu())
        with self.assertRaises(MemoryRecordError):
            self.store.add_camu(_valid_camu())
        self.assertEqual(self.store.count(), 1)
        self.assertIsNotNone(self.store.get(camu_id))

    def test_hash_chain_integrity(self) -> None:
        first_id = self.store.add_camu(_valid_camu())
        second_id = self.store.add_camu(
            _valid_camu(A={"domain": "codex", "when": "test_fail"})
        )
        self.assertTrue(self.store.verify_chain())
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(
            [item["id"] for item in self.store.list()], [first_id, second_id]
        )
        raw_lines = [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([line["kind"] for line in raw_lines], ["meta", "camu", "camu"])
        self.assertEqual([line["sequence"] for line in raw_lines], [0, 1, 2])
        self.assertEqual(
            raw_lines[1]["previous_integrity_hash"], raw_lines[0]["integrity_hash"]
        )
        self.assertEqual(
            raw_lines[2]["previous_integrity_hash"], raw_lines[1]["integrity_hash"]
        )

    def test_tamper_detection_structured_edit(self) -> None:
        camu_id = self.store.add_camu(_valid_camu())
        lines = self.path.read_text(encoding="utf-8").splitlines()
        parsed = json.loads(lines[1])
        parsed["record"]["I"]["description"] = "tampered"
        lines[1] = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(MemoryIntegrityError):
            self.store.verify_chain()
        with self.assertRaises(MemoryIntegrityError):
            self.store.get(camu_id)
        with self.assertRaises(MemoryIntegrityError):
            MemoryStore.load(self.path)

    def test_tamper_detection_byte_flip(self) -> None:
        self.store.add_camu(_valid_camu())
        data = bytearray(self.path.read_bytes())
        data[len(data) // 2] ^= 0x01
        self.path.write_bytes(bytes(data))
        with self.assertRaises(MemoryIntegrityError):
            self.store.verify_chain()
        with self.assertRaises(MemoryIntegrityError):
            self.store.recall({"domain": "codex", "when": "compile_error"})

    def test_tampered_history_cannot_be_extended(self) -> None:
        self.store.add_camu(_valid_camu())
        lines = self.path.read_text(encoding="utf-8").splitlines()
        parsed = json.loads(lines[1])
        parsed["record"]["P"]["condition"] = "rewritten"
        lines[1] = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(MemoryIntegrityError):
            self.store.add_camu(_valid_camu())
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 2)

    def test_get_unknown_id_raises(self) -> None:
        with self.assertRaises(MemoryRecordError):
            self.store.get("0" * 64)

    def test_load_missing_store_raises(self) -> None:
        with self.assertRaises(MemoryIntegrityError):
            MemoryStore.load(Path(self.tempdir.name) / "nope" / "camus.jsonl")

    def test_create_existing_store_raises(self) -> None:
        with self.assertRaises(MemoryRecordError):
            MemoryStore.create(self.path)


class MemoryStoreUseOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "camus.jsonl"
        self.store = MemoryStore.create(self.path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_record_use_appends_and_never_rewrites(self) -> None:
        camu_id = self.store.add_camu(_valid_camu())
        before = self.path.read_bytes()
        first = self.store.record_use(
            camu_id, context={"session": "s1"}, observed="compile_error"
        )
        after_first = self.path.read_bytes()
        self.assertTrue(after_first.startswith(before), "store must only append")
        self.store.record_use(camu_id, context={"session": "s2"}, observed="test_fail")
        effective = self.store.get(camu_id)["record"]
        use_log = effective["E"]["use_log"]
        self.assertEqual(len(use_log), 2)
        self.assertEqual(use_log[0]["context"], {"session": "s1"})
        self.assertEqual(use_log[0]["observed"], "compile_error")
        self.assertIsNone(use_log[0]["matched"])
        self.assertEqual(use_log[1]["context"], {"session": "s2"})
        self.assertEqual(first["record"]["E"]["use_log"][0]["context"], {"session": "s1"})
        original_lines = before.splitlines(keepends=True)
        current_lines = self.path.read_bytes().splitlines(keepends=True)
        self.assertEqual(current_lines[1], original_lines[1], "camu line must be byte-identical")

    def test_record_use_unknown_id_raises(self) -> None:
        with self.assertRaises(MemoryRecordError):
            self.store.record_use("0" * 64, context={"session": "s1"})

    def test_record_outcome_flips_pending_to_verified(self) -> None:
        camu_id = self.store.add_camu(_valid_camu())
        result = self.store.record_outcome(camu_id, observed="error count dropped", matched=True)
        self.assertEqual(result["record"]["P"]["status"], "verified")
        self.assertEqual(len(result["record"]["E"]["support"]), 1)
        self.assertEqual(result["record"]["E"]["support"][0]["observed"], "error count dropped")
        self.assertEqual(result["record"]["E"]["oppose"], [])

    def test_record_outcome_flips_pending_to_contradicted(self) -> None:
        camu_id = self.store.add_camu(_valid_camu())
        result = self.store.record_outcome(camu_id, observed="no change", matched=False)
        self.assertEqual(result["record"]["P"]["status"], "contradicted")
        self.assertEqual(len(result["record"]["E"]["oppose"]), 1)
        self.assertEqual(result["record"]["E"]["oppose"][0]["observed"], "no change")
        self.assertEqual(result["record"]["E"]["support"], [])

    def test_record_outcome_mixed_evidence_returns_to_pending(self) -> None:
        camu_id = self.store.add_camu(_valid_camu())
        self.store.record_outcome(camu_id, observed="hit", matched=True)
        result = self.store.record_outcome(camu_id, observed="miss", matched=False)
        self.assertEqual(result["record"]["P"]["status"], "pending")
        self.assertEqual(len(result["record"]["E"]["support"]), 1)
        self.assertEqual(len(result["record"]["E"]["oppose"]), 1)

    def test_record_outcome_requires_bool_matched(self) -> None:
        camu_id = self.store.add_camu(_valid_camu())
        with self.assertRaises(MemoryRecordError):
            self.store.record_outcome(camu_id, observed="x", matched="yes")

    def test_statuses_persist_across_reload(self) -> None:
        verified_id = self.store.add_camu(_valid_camu())
        contradicted_id = self.store.add_camu(
            _valid_camu(A={"domain": "codex", "when": "test_fail"})
        )
        self.store.record_outcome(verified_id, observed="ok", matched=True)
        self.store.record_outcome(contradicted_id, observed="no", matched=False)
        reloaded = MemoryStore.load(self.path)
        self.assertTrue(reloaded.verify_chain())
        self.assertEqual(reloaded.get(verified_id)["record"]["P"]["status"], "verified")
        self.assertEqual(
            reloaded.get(contradicted_id)["record"]["P"]["status"], "contradicted"
        )


class MemoryStoreRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = MemoryStore.create(Path(self.tempdir.name) / "camus.jsonl")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_default_evaluator_matches_and_non_matches(self) -> None:
        match_id = self.store.add_camu(_valid_camu())
        other_id = self.store.add_camu(
            _valid_camu(A={"domain": "codex", "when": "test_fail"})
        )
        hits = self.store.recall(
            {"domain": "codex", "when": "compile_error", "file": "main.py"}
        )
        self.assertEqual(hits, [match_id])
        self.assertNotIn(other_id, hits)
        self.assertEqual(self.store.recall({"domain": "codex"}), [])
        self.assertEqual(self.store.recall("a plain string context"), [])

    def test_default_evaluator_is_a_shallow_predicate_matcher(self) -> None:
        self.store.add_camu(_valid_camu())
        hits = self.store.recall({"domain": "codex", "when": "compile_error"})
        self.assertEqual(len(hits), 1)
        no_hits = self.store.recall(
            {"domain": "codex", "when": "compile_error", "when_extra": 1}
        )
        self.assertEqual(no_hits, hits, "extra context keys do not change the match")

    def test_custom_evaluator_is_pluggable(self) -> None:
        first_id = self.store.add_camu(_valid_camu())
        second_id = self.store.add_camu(
            _valid_camu(A={"domain": "codex", "when": "test_fail"})
        )

        def evaluator(record, context):
            return (
                context.get("phase") == "fix"
                and record["I"]["domain"] == "tool_selection"
            )

        self.assertEqual(
            set(self.store.recall({"phase": "fix"}, evaluator=evaluator)),
            {first_id, second_id},
        )
        self.assertEqual(
            self.store.recall({"phase": "observe"}, evaluator=evaluator), []
        )

    def test_recall_evaluator_failure_is_surfaced(self) -> None:
        self.store.add_camu(_valid_camu())

        def broken_evaluator(record, context):
            raise RuntimeError("boom")

        with self.assertRaises(MemoryRecordError):
            self.store.recall({"phase": "fix"}, evaluator=broken_evaluator)


class MemoryStoreConsolidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "camus.jsonl"
        self.store = MemoryStore.create(self.path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_consolidate_marks_overdue_without_deleting(self) -> None:
        fresh_id = self.store.add_camu(_valid_camu())
        verified_id = self.store.add_camu(
            _valid_camu(A={"domain": "codex", "when": "test_fail"})
        )
        self.store.record_outcome(verified_id, observed="ok", matched=True)
        future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        summary = self.store.consolidate(now=future)
        self.assertEqual(summary["scanned"], 2)
        self.assertEqual(summary["overdue"], 1)
        self.assertEqual(self.store.count(), 2, "consolidate must never delete")
        self.assertEqual(
            self.store.get(fresh_id)["record"]["P"]["status"], "overdue"
        )
        self.assertEqual(
            self.store.get(verified_id)["record"]["P"]["status"], "verified"
        )

    def test_consolidate_skips_recent_and_non_pending_with_default_ttl(self) -> None:
        self.store.add_camu(_valid_camu())
        self.assertEqual(self.store.consolidate()["overdue"], 0)
        self.assertEqual(self.store.consolidate()["scanned"], 1)

    def test_consolidate_overdue_survives_reload_and_chain(self) -> None:
        camu_id = self.store.add_camu(_valid_camu())
        future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        self.store.consolidate(now=future)
        reloaded = MemoryStore.load(self.path)
        self.assertTrue(reloaded.verify_chain())
        self.assertEqual(
            reloaded.get(camu_id)["record"]["P"]["status"], "overdue"
        )

    def test_consolidate_accepts_iso_and_datetime_now(self) -> None:
        self.store.add_camu(_valid_camu())
        future_iso = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        self.assertEqual(self.store.consolidate(now=future_iso)["overdue"], 1)
        with self.assertRaises(MemoryRecordError):
            self.store.consolidate(now="not-a-timestamp")


class MemoryStoreBoundaryTests(unittest.TestCase):
    def test_caller_owned_import_boundary(self) -> None:
        forbidden = ("trusted", "body", "kernel", "runtime", "witness")
        source = inspect.getsource(memory_store_module)
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        hit = sorted(imported & set(forbidden))
        self.assertEqual(hit, [], f"memory_store imports a forbidden domain: {hit}")
        for name, value in vars(memory_store_module).items():
            if isinstance(value, types.ModuleType):
                module_name = value.__name__
                for domain in forbidden:
                    self.assertFalse(
                        module_name == f"agentic_evo.{domain}"
                        or module_name.startswith(f"agentic_evo.{domain}."),
                        f"memory_store namespace pulled in {module_name}",
                    )
