"""Experience -> CAMU ingest tests (MC-2: real evidence grounding)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from agentic_evo.errors import MemoryRecordError
from agentic_evo.experience_memory_ingest import (
    build_camu_from_session,
    collect_session_evidence,
    estimate_uncertainty,
    ingest_session,
)
from agentic_evo.memory_store import MemoryStore


def _record(seq: int, kind: str, session: str = "ses_x", surface: str = "opencode") -> SimpleNamespace:
    return SimpleNamespace(
        sequence=seq,
        integrity_hash=f"{seq:064d}",
        event_kind=kind,
        session_id=session,
        execution_surface=surface,
    )


class ExperienceMemoryIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.store = MemoryStore.create(root / "memory" / "camus.jsonl")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_collect_filters_by_session_and_sorts(self) -> None:
        records = [
            _record(5, "message_updated", session="ses_b"),
            _record(1, "session_start", session="ses_a"),
            _record(3, "file_edited", session="ses_a"),
            _record(2, "message_updated", session="ses_a"),
        ]
        collected = collect_session_evidence(records, "ses_a")
        self.assertEqual([r.sequence for r in collected], [1, 2, 3])

    def test_build_camu_grounds_real_hashes(self) -> None:
        records = [
            _record(10, "session_start", session="ses_1"),
            _record(11, "message_updated", session="ses_1"),
            _record(12, "file_edited", session="ses_1"),
        ]
        camu = build_camu_from_session(
            records,
            task_class="off_by_one_window",
            task_prompt="Fix the bug in task.py so test_task.py passes",
            session_id="ses_1",
        )
        self.assertEqual(len(camu["G"]["evidence_refs"]), 3)
        self.assertEqual(camu["G"]["evidence_refs"][0]["hash"], f"{10:064d}")
        self.assertEqual(camu["A"], {"task_class": "off_by_one_window"})
        self.assertIn("ses_1", camu["I"]["description"])
        self.assertEqual(camu["I"]["domain"], "executable_skill")
        self.assertEqual(camu["P"]["status"], "pending")

    def test_ingest_writes_and_verifies(self) -> None:
        records = [
            _record(1, "session_start", session="ses_2"),
            _record(2, "message_updated", session="ses_2"),
            _record(3, "message_part_updated", session="ses_2"),
            _record(4, "message_part_updated", session="ses_2"),
            _record(5, "file_edited", session="ses_2"),
        ]
        camu_id = ingest_session(
            self.store,
            records,
            task_class="duration_parser",
            task_prompt="Fix the bug in parser.py so test_parser.py passes",
            session_id="ses_2",
            outcome_matched=True,
        )
        entry = self.store.get(camu_id)
        self.assertEqual(entry["record"]["P"]["status"], "verified")
        self.assertEqual(len(entry["record"]["E"]["support"]), 1)

    def test_ingest_without_outcome_keeps_pending(self) -> None:
        records = [_record(1, "session_start", session="ses_3")]
        camu_id = ingest_session(
            self.store,
            records,
            task_class="unknown",
            task_prompt="observed but outcome unknown",
            session_id="ses_3",
            outcome_matched=False,
        )
        self.assertEqual(self.store.get(camu_id)["record"]["P"]["status"], "pending")

    def test_empty_session_raises(self) -> None:
        with self.assertRaises(MemoryRecordError):
            build_camu_from_session(
                [],
                task_class="x",
                task_prompt="y",
                session_id="ses_none",
            )

    def test_estimate_uncertainty(self) -> None:
        rich = [
            _record(1, "session_start"),
            _record(2, "message_updated"),
            _record(3, "message_part_updated"),
            _record(4, "message_part_updated"),
            _record(5, "file_edited"),
        ]
        thin = [_record(1, "session_start")]
        self.assertEqual(estimate_uncertainty(rich), 0.25)
        self.assertEqual(estimate_uncertainty(thin), 0.7)


if __name__ == "__main__":
    unittest.main()
