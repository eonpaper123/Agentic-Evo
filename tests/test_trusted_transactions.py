from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentic_evo.errors import IntegrityError, RuntimeOffError
from agentic_evo.runtime import DevelopmentalRuntime
from agentic_evo.trusted import TrustedState


class TrustedTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        self.runtime = self._genesis(self.home)
        self.db_path = self.home / "trusted" / "state.sqlite3"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _genesis(home: Path) -> DevelopmentalRuntime:
        return DevelopmentalRuntime.genesis(
            home,
            host_binding="test-host-binding",
            purpose_anchor="Improve the future of the one bound host.",
            initial_body={"entrypoint.md": "Body zero"},
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )

    def _prepare_successor(self, **kwargs):
        return self.runtime._prepare_successor(
            ingress_path="test_instrument",
            expected_authority_epoch=self.runtime.trusted.authority_epoch(),
            **kwargs,
        )

    def _advance_head(self, *, expected_head: str, candidate_head: str):
        manifest = self.runtime.body_store.read_manifest(candidate_head)
        return self.runtime._advance_head(
            expected_head=expected_head,
            candidate_head=candidate_head,
            author_kind=manifest.author_kind,
            ingress_path="test_instrument",
            expected_authority_epoch=self.runtime.trusted.authority_epoch(),
        )

    def _fail_event(self, event_kind: str) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                f"""
                CREATE TRIGGER fail_{event_kind}
                BEFORE INSERT ON events
                WHEN NEW.event_kind = {event_kind!r}
                BEGIN
                    SELECT RAISE(ABORT, 'injected event failure');
                END
                """
            )

    def _trusted_counts(self) -> tuple[int, int, int]:
        with closing(sqlite3.connect(self.db_path)) as connection:
            events = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            checkpoints = connection.execute(
                "SELECT COUNT(*) FROM checkpoints"
            ).fetchone()[0]
            revision = connection.execute(
                "SELECT revision FROM state WHERE id = 1"
            ).fetchone()[0]
        return events, checkpoints, revision

    def test_runtime_has_one_sqlite_truth_and_no_legacy_state_files(self) -> None:
        self.assertTrue(self.db_path.is_file())
        self.assertFalse((self.home / "kernel" / "state.json").exists())
        self.assertFalse((self.home / "runtime" / "state.json").exists())
        self.assertFalse((self.home / "evidence" / "events.jsonl").exists())

        with closing(sqlite3.connect(self.db_path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            state = connection.execute(
                """
                SELECT root, head, authority, revision
                FROM state
                WHERE id = 1
                """
            ).fetchone()

        self.assertEqual(tables, {"state", "events", "sessions", "checkpoints"})
        self.assertEqual(state[:3], (
            self.runtime.status().root,
            self.runtime.status().head,
            "on",
        ))
        self.assertGreaterEqual(state[3], 1)

    def test_failed_wake_leaves_neither_session_nor_evidence(self) -> None:
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        before_counts = self._trusted_counts()
        self._fail_event("session_start")

        with self.assertRaises(sqlite3.DatabaseError):
            self.runtime.wake(
                execution_surface="codex",
                session_id="ghost-session",
                project_environment="project-a",
            )

        reloaded = DevelopmentalRuntime.load(self.home)
        self.assertEqual(reloaded.status(), before_status)
        self.assertEqual(reloaded.evidence.records(), before_records)
        self.assertEqual(self._trusted_counts(), before_counts)

    def test_process_crash_before_checkpoint_rolls_back_the_whole_wake(self) -> None:
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        before_counts = self._trusted_counts()
        script = """
import os
from pathlib import Path
import sys
from unittest.mock import patch

from agentic_evo.runtime import DevelopmentalRuntime
from agentic_evo.trusted import TrustedState

runtime = DevelopmentalRuntime.load(Path(sys.argv[1]))
with patch.object(TrustedState, "_insert_checkpoint", side_effect=lambda *a, **k: os._exit(73)):
    runtime.wake(
        execution_surface="codex",
        session_id="crashed-session",
        project_environment="project-a",
    )
"""
        environment = dict(os.environ)
        source_path = str(Path(__file__).resolve().parents[1] / "src")
        environment["PYTHONPATH"] = source_path
        completed = subprocess.run(
            [sys.executable, "-c", script, str(self.home)],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(completed.returncode, 73, completed.stderr)
        reloaded = DevelopmentalRuntime.load(self.home)
        self.assertEqual(reloaded.status(), before_status)
        self.assertEqual(reloaded.evidence.records(), before_records)
        self.assertEqual(self._trusted_counts(), before_counts)

    def test_failed_head_advance_rolls_back_head_evidence_and_checkpoint(self) -> None:
        before = self.runtime.status()
        candidate = self._prepare_successor(
            expected_parent=before.head,
            files={"entrypoint.md": "Body one"},
            author_kind="research_instrument",
        )
        before_records = self.runtime.evidence.records()
        before_counts = self._trusted_counts()
        self._fail_event("head_advanced")

        with self.assertRaises(sqlite3.DatabaseError):
            self._advance_head(
                expected_head=before.head,
                candidate_head=candidate,
            )

        reloaded = DevelopmentalRuntime.load(self.home)
        self.assertEqual(reloaded.status().head, before.head)
        self.assertEqual(reloaded.evidence.records(), before_records)
        self.assertEqual(self._trusted_counts(), before_counts)
        self.assertEqual(
            reloaded.body_store.read_manifest(candidate).parent_head,
            before.head,
        )

    def test_failed_off_preserves_authority_session_and_history(self) -> None:
        self.runtime.wake(
            execution_surface="codex",
            session_id="live-session",
            project_environment="project-a",
        )
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        before_counts = self._trusted_counts()
        self._fail_event("host_off")

        with self.assertRaises(sqlite3.DatabaseError):
            self.runtime.turn_off()

        reloaded = DevelopmentalRuntime.load(self.home)
        self.assertEqual(reloaded.status(), before_status)
        self.assertEqual(reloaded.evidence.records(), before_records)
        self.assertEqual(self._trusted_counts(), before_counts)

    def test_off_rejects_a_delayed_session_end_without_new_history(self) -> None:
        self.runtime.wake(
            execution_surface="codex",
            session_id="late-session",
            project_environment="project-a",
        )
        self.runtime.turn_off()
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        before_counts = self._trusted_counts()

        with self.assertRaises(RuntimeOffError):
            self.runtime.sleep(
                execution_surface="codex",
                session_id="late-session",
            )

        reloaded = DevelopmentalRuntime.load(self.home)
        self.assertEqual(reloaded.status(), before_status)
        self.assertEqual(reloaded.evidence.records(), before_records)
        self.assertEqual(self._trusted_counts(), before_counts)

    def test_failed_genesis_has_no_birth_and_can_retry(self) -> None:
        genesis_home = self.home / "failed-genesis"

        with patch.object(
            TrustedState,
            "_insert_checkpoint",
            side_effect=RuntimeError("injected before commit"),
        ):
            with self.assertRaises(RuntimeError):
                self._genesis(genesis_home)

        with self.assertRaises(IntegrityError):
            DevelopmentalRuntime.load(genesis_home)

        retry = self._genesis(genesis_home)
        genesis_events = [
            record
            for record in retry.evidence.records()
            if record.event_kind == "genesis"
        ]
        self.assertEqual(len(genesis_events), 1)
        self.assertEqual(retry.status().generation, 0)

    def test_checkpoint_binds_the_identity_anchor(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                "UPDATE state SET who = ? WHERE id = 1",
                ("forged-host-identity",),
            )

        with self.assertRaises(IntegrityError):
            DevelopmentalRuntime.load(self.home)

    def test_checkpoint_chain_matches_state_and_detects_tampering(self) -> None:
        before = self.runtime.status()
        candidate = self._prepare_successor(
            expected_parent=before.head,
            files={"entrypoint.md": "Body one"},
            author_kind="research_instrument",
        )
        self._advance_head(
            expected_head=before.head,
            candidate_head=candidate,
        )
        self.runtime.turn_off()

        with closing(sqlite3.connect(self.db_path)) as connection:
            state = connection.execute(
                """
                SELECT root, head, authority, revision,
                       last_event_sequence, last_event_hash,
                       checkpoint_sequence, checkpoint_hash
                FROM state
                WHERE id = 1
                """
            ).fetchone()
            checkpoints = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT record_json FROM checkpoints ORDER BY sequence"
                )
            ]

        self.assertEqual(
            [record["checkpoint_seq"] for record in checkpoints],
            list(range(1, len(checkpoints) + 1)),
        )
        self.assertIsNone(checkpoints[0]["previous_checkpoint_hash"])
        for previous, current in zip(checkpoints, checkpoints[1:]):
            self.assertEqual(
                current["previous_checkpoint_hash"],
                previous["checkpoint_hash"],
            )
        tail = checkpoints[-1]
        self.assertEqual(
            (
                tail["root"],
                tail["head"],
                tail["authority"],
                tail["revision"],
                tail["evidence_seq"],
                tail["evidence_hash"],
                tail["checkpoint_seq"],
                tail["checkpoint_hash"],
            ),
            state,
        )

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                "UPDATE checkpoints SET record_json = ? WHERE sequence = 1",
                (b"{}",),
            )

        with self.assertRaises(IntegrityError):
            DevelopmentalRuntime.load(self.home)


if __name__ == "__main__":
    unittest.main()
