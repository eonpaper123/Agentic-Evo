from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agentic_evo.body import BodyStore
from agentic_evo.errors import IntegrityError, InvalidBodyError, SensitiveContentError
from agentic_evo.evidence import EvidenceLedger


class EvidenceLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "evidence"
        self.ledger = EvidenceLedger.create(
            self.path,
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_append_only_chain_preserves_order_and_verifies(self) -> None:
        first = self.ledger.append(
            event_kind="genesis",
            root_commitment="root-a",
            head_before=None,
            head_after="a" * 64,
            source_kind="research_instrument",
            author_kind="research_instrument",
            payload={"schema": "body-v1"},
        )
        second = self.ledger.append(
            event_kind="session_start",
            root_commitment="root-a",
            head_before="a" * 64,
            head_after="a" * 64,
            source_kind="execution_surface",
            author_kind="normal_host_interaction",
            execution_surface="codex",
            session_id="session-a",
            project_environment="project-a",
            payload={"model": "model-a"},
        )

        records = self.ledger.records()
        self.assertEqual([record.sequence for record in records], [1, 2])
        self.assertEqual(first.previous_integrity_hash, None)
        self.assertEqual(second.previous_integrity_hash, first.integrity_hash)
        self.assertTrue(self.ledger.verify())
        self.assertFalse(hasattr(self.ledger, "delete"))
        self.assertFalse(hasattr(self.ledger, "update"))

    def test_tampering_with_a_record_breaks_the_integrity_chain(self) -> None:
        self.ledger.append(
            event_kind="genesis",
            root_commitment="root-a",
            head_before=None,
            head_after="a" * 64,
            source_kind="research_instrument",
            author_kind="research_instrument",
            payload={},
        )
        self.ledger.append(
            event_kind="session_start",
            root_commitment="root-a",
            head_before="a" * 64,
            head_after="a" * 64,
            source_kind="execution_surface",
            author_kind="normal_host_interaction",
            payload={},
        )

        log_path = self.path / "events.jsonl"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["event_kind"] = "rewritten-genesis"
        lines[0] = json.dumps(first, ensure_ascii=False, sort_keys=True)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with self.assertRaises(IntegrityError):
            self.ledger.verify()
        with self.assertRaises(IntegrityError):
            self.ledger.records()

    def test_tampered_history_cannot_be_extended_with_a_new_valid_tail(self) -> None:
        self.ledger.append(
            event_kind="genesis",
            root_commitment="root-a",
            head_before=None,
            head_after="a" * 64,
            source_kind="research_instrument",
            author_kind="research_instrument",
            payload={},
        )
        log_path = self.path / "events.jsonl"
        record = json.loads(log_path.read_text(encoding="utf-8"))
        record["event_kind"] = "rewritten-genesis"
        log_path.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with self.assertRaises(IntegrityError):
            self.ledger.append(
                event_kind="session_start",
                root_commitment="root-a",
                head_before="a" * 64,
                head_after="a" * 64,
                source_kind="execution_surface",
                author_kind="normal_host_interaction",
                payload={},
            )

        self.assertEqual(len(log_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_sensitive_keys_are_rejected_recursively(self) -> None:
        with self.assertRaises(SensitiveContentError):
            self.ledger.append(
                event_kind="tool_result",
                root_commitment="root-a",
                head_before="a" * 64,
                head_after="a" * 64,
                source_kind="execution_surface",
                author_kind="normal_host_interaction",
                payload={"nested": {"access_token": "do-not-store"}},
            )

        self.assertEqual(self.ledger.records(), ())

    def test_instrument_and_human_learning_intervention_remain_distinct(self) -> None:
        self.ledger.append(
            event_kind="instrument_changed",
            root_commitment="root-a",
            head_before="a" * 64,
            head_after="a" * 64,
            source_kind="research_instrument",
            author_kind="research_instrument",
            payload={"new_instrument_version": "instrument-test-v2"},
        )
        self.ledger.append(
            event_kind="body_changed",
            root_commitment="root-a",
            head_before="a" * 64,
            head_after="b" * 64,
            source_kind="body",
            author_kind="human_learning_intervention",
            human_intervention_kind="selected_successor",
            payload={},
        )

        first, second = self.ledger.records()
        self.assertEqual(first.author_kind, "research_instrument")
        self.assertEqual(second.author_kind, "human_learning_intervention")
        self.assertEqual(second.human_intervention_kind, "selected_successor")

    def test_degenerate_body_path_is_rejected_as_domain_error(self) -> None:
        store = BodyStore(Path(self.tempdir.name) / "body")

        with self.assertRaises(InvalidBodyError):
            store.commit(
                root="root-a",
                parent_head=None,
                files={".": "invalid"},
                author_kind="research_instrument",
            )

    def test_body_logical_path_has_a_portable_byte_bound(self) -> None:
        store = BodyStore(Path(self.tempdir.name) / "body")
        oversized_path = f"{'nested/' * 80}file.md"

        with self.assertRaises(InvalidBodyError):
            store.commit(
                root="root-a",
                parent_head=None,
                files={oversized_path: "invalid"},
                author_kind="research_instrument",
            )


if __name__ == "__main__":
    unittest.main()
