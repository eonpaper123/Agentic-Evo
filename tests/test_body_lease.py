from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agentic_evo.errors import (
    AuthorityError,
    BodyLeaseError,
    HeadConflictError,
    InvalidBodyError,
)
from agentic_evo.runtime import DevelopmentalRuntime
from agentic_evo.witness import WitnessCore


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CurrentBodyLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        self.host_binding = "test-host-binding"
        self.runtime = DevelopmentalRuntime.genesis(
            self.home,
            host_binding=self.host_binding,
            purpose_anchor="Improve the future of the one bound host.",
            initial_body={"entrypoint.md": "Body zero"},
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
        self.clock = FakeClock()
        self.witness = WitnessCore(
            self.runtime,
            lease_seconds=10.0,
            clock=self.clock,
        )

    def tearDown(self) -> None:
        self.witness.close()
        self.tempdir.cleanup()

    def _open(self):
        return self.witness.open_current_body_session(
            expected_head=self.runtime.status().head
        )

    def test_only_exact_current_head_can_receive_the_one_live_lease(self) -> None:
        session = self._open()
        candidate = session.prepare_successor(
            files={"entrypoint.md": "Candidate body"}
        )

        with self.assertRaises(HeadConflictError):
            self.witness.open_current_body_session(expected_head=candidate)

        competing_witness = WitnessCore(
            DevelopmentalRuntime.load(self.home),
            lease_seconds=10.0,
            clock=self.clock,
        )
        self.addCleanup(competing_witness.close)
        with self.assertRaises(BodyLeaseError):
            competing_witness.open_current_body_session(
                expected_head=self.runtime.status().head
            )

        session.close()
        replacement = competing_witness.open_current_body_session(
            expected_head=self.runtime.status().head
        )
        replacement.close()

    def test_explicit_revoke_allows_replacement_but_old_session_stays_dead(
        self,
    ) -> None:
        old = self._open()
        self.witness.revoke_current_body_session(reason="test replacement")
        replacement = self._open()

        with self.assertRaises(BodyLeaseError):
            old.prepare_successor(files={"entrypoint.md": "stale"})

        candidate = replacement.prepare_successor(
            files={"entrypoint.md": "replacement"}
        )
        self.assertEqual(
            self.runtime.body_store.read_manifest(candidate).parent_head,
            self.runtime.status().head,
        )

    def test_expired_lease_cannot_mutate_or_resurrect(self) -> None:
        session = self._open()
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        self.clock.advance(11.0)

        with self.assertRaises(BodyLeaseError):
            session.prepare_successor(files={"entrypoint.md": "too late"})

        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)
        replacement = self._open()
        replacement.close()

    def test_off_revokes_lease_and_on_does_not_restore_it(self) -> None:
        session = self._open()
        self.witness.turn_off()
        records_after_off = self.runtime.evidence.records()

        with self.assertRaises(BodyLeaseError):
            session.prepare_successor(files={"entrypoint.md": "after off"})
        self.assertEqual(self.runtime.evidence.records(), records_after_off)

        self.witness.turn_on(host_binding=self.host_binding)
        with self.assertRaises(BodyLeaseError):
            session.prepare_successor(files={"entrypoint.md": "after on"})

        replacement = self._open()
        replacement.close()

    def test_successful_head_transition_consumes_the_old_lease(self) -> None:
        session = self._open()
        before = self.runtime.status()
        candidate = session.prepare_successor(
            files={"entrypoint.md": "Body one"}
        )
        after = session.advance_head(candidate_head=candidate)

        self.assertEqual(after.head, candidate)
        with self.assertRaises(BodyLeaseError):
            session.advance_head(candidate_head=candidate)
        with self.assertRaises(BodyLeaseError):
            session.prepare_successor(files={"entrypoint.md": "replay"})

        replacement = self.witness.open_current_body_session(
            expected_head=candidate
        )
        self.assertNotEqual(before.head, after.head)
        replacement.close()

    def test_failed_transition_keeps_the_lease_for_correction(self) -> None:
        session = self._open()
        invalid = session.prepare_successor(
            files={"future.bin": b"future body"},
            activation_kind="future-body-v9",
            activation_artifact="future.bin",
        )
        with self.assertRaises(InvalidBodyError):
            session.advance_head(candidate_head=invalid)

        valid = session.prepare_successor(
            files={"entrypoint.md": "corrected body"}
        )
        db_path = self.home / "trusted" / "state.sqlite3"
        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER fail_rehearsal_advance
                BEFORE INSERT ON events
                WHEN NEW.event_kind = 'head_advanced'
                BEGIN
                    SELECT RAISE(ABORT, 'injected transition failure');
                END
                """
            )
        with self.assertRaises(sqlite3.DatabaseError):
            session.advance_head(candidate_head=valid)
        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute("DROP TRIGGER fail_rehearsal_advance")

        after = session.advance_head(candidate_head=valid)
        self.assertEqual(after.head, valid)

    def test_channel_has_only_lineage_rehearsal_operations(self) -> None:
        session = self._open()
        public_methods = {
            name
            for name in dir(session)
            if not name.startswith("_") and callable(getattr(session, name))
        }
        self.assertEqual(
            public_methods,
            {"advance_head", "close", "prepare_successor"},
        )
        self.assertFalse(hasattr(self.runtime, "prepare_successor"))
        self.assertFalse(hasattr(self.runtime, "advance_head"))

    def test_ingress_is_derived_without_claiming_agent_self_authorship(
        self,
    ) -> None:
        session = self._open()
        candidate = session.prepare_successor(
            files={"entrypoint.md": "rehearsal body"}
        )
        manifest = self.runtime.body_store.read_manifest(candidate)
        self.assertEqual(manifest.author_kind, "in_process_rehearsal")
        session.advance_head(candidate_head=candidate)

        lineage_records = self.runtime.evidence.records()[-2:]
        self.assertTrue(
            all(record.author_kind == "in_process_rehearsal" for record in lineage_records)
        )
        self.assertTrue(
            all(
                record.payload["ingress_principal"]
                == "in_process_body_lease"
                for record in lineage_records
            )
        )
        self.assertNotIn(
            "agent_self_authored",
            {record.author_kind for record in self.runtime.evidence.records()},
        )

        self.runtime.observe(
            event_kind="surface_claim",
            execution_surface="codex",
            payload={"claimed_author_kind": "agent_self_authored"},
        )
        surface_record = self.runtime.evidence.records()[-1]
        self.assertEqual(surface_record.source_kind, "execution_surface")
        self.assertEqual(surface_record.author_kind, "surface_unverified")

    def test_foreign_authorship_cannot_be_laundered_through_the_lease(
        self,
    ) -> None:
        session = self._open()
        status = self.runtime.status()
        foreign = self.runtime.body_store.commit(
            root=status.root,
            parent_head=status.head,
            files={"entrypoint.md": "human-authored body"},
            author_kind="human_learning_intervention",
            activation_kind="surface-context-utf8-v1",
            activation_artifact="entrypoint.md",
        )

        with self.assertRaises(AuthorityError):
            session.advance_head(candidate_head=foreign)
        self.assertEqual(self.runtime.status().head, status.head)


if __name__ == "__main__":
    unittest.main()
