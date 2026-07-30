from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from agentic_evo.body import BodyStore
from agentic_evo._util import atomic_write_json, canonical_json_bytes, sha256_hex
from agentic_evo.errors import (
    AuthorityError,
    HeadConflictError,
    IntegrityError,
    InvalidBodyError,
    RootBindingError,
    RuntimeOffError,
)
from agentic_evo.runtime import DevelopmentalRuntime
from agentic_evo.trusted import TrustedState


class MachineLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        self.host_binding = "test-host-binding"
        self.runtime = DevelopmentalRuntime.genesis(
            self.home,
            host_binding=self.host_binding,
            purpose_anchor="Improve the future of the one bound host.",
            initial_body={
                "entrypoint.md": "Body zero",
                "state/open-questions.json": "[]",
            },
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

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

    def test_genesis_persists_one_root_and_head_across_projects_and_sessions(self) -> None:
        initial = self.runtime.status()

        first = self.runtime.wake(
            execution_surface="codex",
            session_id="session-a",
            project_environment="project-a",
            model="model-a",
        )
        self.runtime.sleep(
            execution_surface="codex",
            session_id="session-a",
        )

        reloaded = DevelopmentalRuntime.load(self.home)
        second = reloaded.wake(
            execution_surface="other-coding-agent",
            session_id="session-b",
            project_environment="project-b",
            model="model-b",
        )

        self.assertEqual(initial.root, first.root)
        self.assertEqual(first.root, second.root)
        self.assertEqual(initial.head, first.head)
        self.assertEqual(first.head, second.head)
        self.assertEqual(second.generation, 0)
        self.assertEqual(
            second.body_files,
            ("entrypoint.md", "state/open-questions.json"),
        )

    def test_same_raw_session_id_isolated_by_execution_surface(self) -> None:
        self.runtime.wake(
            execution_surface="codex",
            session_id="shared-session",
            project_environment="project-a",
        )
        self.runtime.wake(
            execution_surface="other-coding-agent",
            session_id="shared-session",
            project_environment="project-b",
        )

        active = self.runtime.status().active_sessions
        self.assertEqual(len(active), 2)
        self.assertEqual(
            {
                (identity.execution_surface, identity.session_id)
                for identity in active
            },
            {
                ("codex", "shared-session"),
                ("other-coding-agent", "shared-session"),
            },
        )

        self.runtime.sleep(
            execution_surface="codex",
            session_id="shared-session",
        )
        session_end = self.runtime.evidence.records()[-1]
        self.assertEqual(session_end.event_kind, "session_end")
        self.assertEqual(session_end.execution_surface, "codex")
        self.assertTrue(session_end.payload["session_was_active"])
        remaining = DevelopmentalRuntime.load(self.home).status().active_sessions
        self.assertEqual(
            tuple(
                (identity.execution_surface, identity.session_id)
                for identity in remaining
            ),
            (("other-coding-agent", "shared-session"),),
        )

    def test_wrong_surface_sleep_cannot_end_same_raw_session_id(self) -> None:
        self.runtime.wake(
            execution_surface="other-coding-agent",
            session_id="shared-session",
            project_environment="project-b",
        )
        before_status = self.runtime.status()

        self.runtime.sleep(
            execution_surface="codex",
            session_id="shared-session",
        )

        self.assertEqual(self.runtime.status(), before_status)
        session_end = self.runtime.evidence.records()[-1]
        self.assertEqual(session_end.execution_surface, "codex")
        self.assertFalse(session_end.payload["session_was_active"])

    def test_repeated_composite_wake_is_a_recorded_reattach(self) -> None:
        before_records = self.runtime.evidence.records()

        for _ in range(2):
            self.runtime.wake(
                execution_surface="codex",
                session_id="reattached-session",
                project_environment="project-a",
            )

        active = self.runtime.status().active_sessions
        self.assertEqual(
            tuple(
                (identity.execution_surface, identity.session_id)
                for identity in active
            ),
            (("codex", "reattached-session"),),
        )
        appended = self.runtime.evidence.records()[len(before_records):]
        self.assertEqual(
            [record.event_kind for record in appended],
            ["session_start", "session_start"],
        )

    def test_session_table_uses_the_composite_surface_identity_as_primary_key(
        self,
    ) -> None:
        with closing(
            sqlite3.connect(self.home / "trusted" / "state.sqlite3")
        ) as connection:
            columns = connection.execute("PRAGMA table_info(sessions)").fetchall()

        primary_key = tuple(
            row[1]
            for row in sorted(
                (row for row in columns if row[5] > 0),
                key=lambda row: row[5],
            )
        )
        self.assertEqual(
            primary_key,
            ("execution_surface", "session_id"),
        )

    def test_session_commitment_is_versioned_and_structurally_unambiguous(
        self,
    ) -> None:
        self.runtime.wake(
            execution_surface="codex",
            session_id="shared:session",
            project_environment="project-a",
        )
        with closing(
            sqlite3.connect(self.home / "trusted" / "state.sqlite3")
        ) as connection:
            session_id, raw_value = connection.execute(
                "SELECT session_id, value_json FROM sessions"
            ).fetchone()
            sessions_hash = connection.execute(
                "SELECT sessions_hash FROM state WHERE id = 1"
            ).fetchone()[0]

        expected_commitment = {
            "schema_version": "agentic-evo-sessions-v2",
            "sessions": [
                {
                    "execution_surface": "codex",
                    "session_id": session_id,
                    "value": json.loads(raw_value),
                }
            ],
        }
        self.assertEqual(
            sessions_hash,
            sha256_hex(canonical_json_bytes(expected_commitment)),
        )

    def test_trusted_session_rejects_surface_value_mismatch_atomically(
        self,
    ) -> None:
        before_status = self.runtime.status()
        before_records = self.runtime.evidence.records()
        manifest = self.runtime.body_store.read_manifest(before_status.head)

        with self.assertRaises(IntegrityError):
            self.runtime.trusted.start_session(
                expected_head=before_status.head,
                session_id="mismatched-session",
                value={
                    "execution_surface": "other-coding-agent",
                    "project_environment": "project-a",
                },
                execution_surface="codex",
                project_environment="project-a",
                model=None,
                body_generation=manifest.generation,
                activation_kind=str(manifest.activation_kind),
                activation_artifact=str(manifest.activation_artifact),
                activation_digest=dict(manifest.files)[
                    str(manifest.activation_artifact)
                ],
            )

        self.assertEqual(self.runtime.status(), before_status)
        self.assertEqual(self.runtime.evidence.records(), before_records)

    def test_pre_composite_trusted_schema_fails_closed(self) -> None:
        db_path = self.home / "trusted" / "state.sqlite3"
        with closing(sqlite3.connect(db_path)) as connection, connection:
            connection.execute(
                "UPDATE state SET schema_version = ? WHERE id = 1",
                ("agentic-evo-trusted-v1",),
            )

        with self.assertRaisesRegex(
            IntegrityError,
            "unsupported trusted state schema",
        ):
            DevelopmentalRuntime.load(self.home)

    def test_legacy_empty_session_schema_cannot_partially_commit_genesis(
        self,
    ) -> None:
        legacy_home = self.home / "legacy-empty-schema"
        trusted_path = legacy_home / "trusted"
        trusted_path.mkdir(parents=True)
        db_path = trusted_path / "state.sqlite3"
        with closing(sqlite3.connect(db_path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    schema_version TEXT NOT NULL,
                    who TEXT NOT NULL,
                    why TEXT NOT NULL,
                    authority TEXT NOT NULL,
                    root TEXT NOT NULL,
                    head TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    instrument_version TEXT NOT NULL,
                    protocol_version TEXT NOT NULL,
                    last_event_sequence INTEGER NOT NULL,
                    last_event_hash TEXT,
                    checkpoint_sequence INTEGER NOT NULL,
                    checkpoint_hash TEXT,
                    sessions_hash TEXT NOT NULL
                );
                CREATE TABLE events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    event_kind TEXT NOT NULL,
                    record_json BLOB NOT NULL
                );
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY,
                    value_json BLOB NOT NULL
                );
                CREATE TABLE checkpoints (
                    sequence INTEGER PRIMARY KEY,
                    record_json BLOB NOT NULL,
                    checkpoint_mac TEXT NOT NULL
                );
                """
            )

        with self.assertRaisesRegex(
            IntegrityError,
            "incompatible trusted state schema",
        ):
            DevelopmentalRuntime.genesis(
                legacy_home,
                host_binding=self.host_binding,
                purpose_anchor="Improve the future of the one bound host.",
                initial_body={"entrypoint.md": "Body zero"},
                instrument_version="instrument-test-v1",
                protocol_version="protocol-test-v1",
            )

        with closing(sqlite3.connect(db_path)) as connection:
            committed_rows = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in ("state", "events", "checkpoints")
            }
        self.assertEqual(
            committed_rows,
            {"state": 0, "events": 0, "checkpoints": 0},
        )

    def test_wake_describes_activation_material_from_the_exact_current_head(self) -> None:
        before = self.runtime.status()
        candidate = self._prepare_successor(
            expected_parent=before.head,
            files={
                "boot/activate.md": "Exact body one",
                "entrypoint.md": "This file is not the selected activation artifact",
            },
            author_kind="research_instrument",
            activation_kind="surface-context-utf8-v1",
            activation_artifact="boot/activate.md",
        )
        self._advance_head(
            expected_head=before.head,
            candidate_head=candidate,
        )

        manifest = self.runtime.body_store.read_manifest(candidate)
        wake = self.runtime.wake(
            execution_surface="codex",
            session_id="exact-head-session",
            project_environment="project-a",
        )

        self.assertEqual(wake.head, candidate)
        self.assertEqual(wake.activation_kind, "surface-context-utf8-v1")
        self.assertEqual(wake.activation_artifact, "boot/activate.md")
        self.assertEqual(
            wake.activation_digest,
            dict(manifest.files)["boot/activate.md"],
        )
        self.assertEqual(wake.activation_context, "Exact body one")

    def test_explicit_missing_activation_artifact_is_rejected(self) -> None:
        before = self.runtime.status()

        with self.assertRaises(InvalidBodyError):
            self._prepare_successor(
                expected_parent=before.head,
                files={"entrypoint.md": "Body one"},
                author_kind="research_instrument",
                activation_kind="surface-context-utf8-v1",
                activation_artifact="boot/missing.md",
            )

    def test_unknown_activation_kind_cannot_become_current_head(self) -> None:
        before = self.runtime.status()
        candidate = self._prepare_successor(
            expected_parent=before.head,
            files={"boot/body.bin": b"\x00\x01future-body"},
            author_kind="research_instrument",
            activation_kind="future-body-v9",
            activation_artifact="boot/body.bin",
        )

        with self.assertRaises(InvalidBodyError):
            self._advance_head(
                expected_head=before.head,
                candidate_head=candidate,
            )

        self.assertEqual(self.runtime.status().head, before.head)

    def test_activation_bytes_are_rechecked_after_manifest_verification(self) -> None:
        store = BodyStore(self.home / "race-body-store")
        head = store.commit(
            root="race-root",
            parent_head=None,
            files={"boot/activate.md": "Committed activation bytes"},
            author_kind="research_instrument",
            activation_kind="surface-context-utf8-v1",
            activation_artifact="boot/activate.md",
        )
        manifest = store.read_manifest(head)
        blob_path = store.blob_path / dict(manifest.files)["boot/activate.md"]
        original_read_manifest = store.read_manifest

        def verify_then_replace(commitment: str):
            verified = original_read_manifest(commitment)
            blob_path.write_bytes(b"replaced after manifest verification")
            return verified

        with patch.object(
            store,
            "read_manifest",
            side_effect=verify_then_replace,
        ):
            with self.assertRaises(IntegrityError):
                store.read_file(head, "boot/activate.md")

    def test_successor_requires_current_parent_and_advances_atomically(self) -> None:
        before = self.runtime.status()
        candidate = self._prepare_successor(
            expected_parent=before.head,
            files={
                "entrypoint.md": "Body one",
                "state/open-questions.json": '["observe real outcomes"]',
            },
            author_kind="research_instrument",
        )

        after = self._advance_head(
            expected_head=before.head,
            candidate_head=candidate,
        )

        self.assertEqual(after.head, candidate)
        self.assertEqual(after.generation, 1)
        self.assertEqual(
            self.runtime.body_store.read_manifest(candidate).parent_head,
            before.head,
        )

        before_stale_attempt = self.runtime.evidence.records()
        with self.assertRaises(HeadConflictError):
            self._prepare_successor(
                expected_parent=before.head,
                files={"entrypoint.md": "Stale branch"},
                author_kind="research_instrument",
            )
        self.assertEqual(
            self.runtime.evidence.records(),
            before_stale_attempt,
        )

        self.assertEqual(self.runtime.status().head, candidate)
        self.assertEqual(self.runtime.status().generation, 1)

    def test_internal_transition_cannot_relabel_candidate_authorship(self) -> None:
        before = self.runtime.status()
        candidate = self._prepare_successor(
            expected_parent=before.head,
            files={"entrypoint.md": "Research-authored candidate"},
            author_kind="research_instrument",
        )

        with self.assertRaises(AuthorityError):
            self.runtime._advance_head(
                expected_head=before.head,
                candidate_head=candidate,
                author_kind="human_learning_intervention",
                ingress_path="test_instrument",
                expected_authority_epoch=self.runtime.trusted.authority_epoch(),
            )

        self.assertEqual(self.runtime.status().head, before.head)

    def test_concurrent_successors_cannot_both_advance_the_same_head(self) -> None:
        before = self.runtime.status()
        candidates = [
            self._prepare_successor(
                expected_parent=before.head,
                files={"entrypoint.md": f"Candidate {index}"},
                author_kind="research_instrument",
            )
            for index in range(2)
        ]
        barrier = threading.Barrier(2)
        successes: list[str] = []
        conflicts: list[str] = []

        def advance(candidate: str) -> None:
            barrier.wait()
            try:
                runtime = DevelopmentalRuntime.load(self.home)
                runtime._advance_head(
                    expected_head=before.head,
                    candidate_head=candidate,
                    author_kind=runtime.body_store.read_manifest(
                        candidate
                    ).author_kind,
                    ingress_path="test_instrument",
                    expected_authority_epoch=runtime.trusted.authority_epoch(),
                )
                successes.append(candidate)
            except HeadConflictError:
                conflicts.append(candidate)

        threads = [
            threading.Thread(target=advance, args=(candidate,))
            for candidate in candidates
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(self.runtime.status().head, successes[0])

    def test_concurrent_genesis_has_exactly_one_origin(self) -> None:
        genesis_home = self.home / "concurrent-genesis"
        rendezvous = threading.Barrier(2)
        original_generate_root = TrustedState.generate_root
        successes: list[DevelopmentalRuntime] = []
        failures: list[Exception] = []

        def synchronized_root() -> str:
            try:
                rendezvous.wait(timeout=0.2)
            except threading.BrokenBarrierError:
                pass
            return original_generate_root()

        def create() -> None:
            try:
                successes.append(
                    DevelopmentalRuntime.genesis(
                        genesis_home,
                        host_binding=self.host_binding,
                        purpose_anchor="Improve the future of the one bound host.",
                        initial_body={"entrypoint.md": "Body zero"},
                        instrument_version="instrument-test-v1",
                        protocol_version="protocol-test-v1",
                    )
                )
            except Exception as exc:
                failures.append(exc)

        with patch.object(
            TrustedState,
            "generate_root",
            side_effect=synchronized_root,
        ):
            threads = [threading.Thread(target=create) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        loaded = DevelopmentalRuntime.load(genesis_home)
        genesis_events = [
            record
            for record in loaded.evidence.records()
            if record.event_kind == "genesis"
        ]
        self.assertEqual(len(genesis_events), 1)
        self.assertEqual(genesis_events[0].root_commitment, loaded.status().root)

    def test_unsupported_genesis_activation_is_rejected_before_birth(self) -> None:
        genesis_home = self.home / "unsupported-genesis"

        with self.assertRaises(InvalidBodyError):
            DevelopmentalRuntime.genesis(
                genesis_home,
                host_binding=self.host_binding,
                purpose_anchor="Improve the future of the one bound host.",
                initial_body={"entrypoint.md": "Body zero"},
                instrument_version="instrument-test-v1",
                protocol_version="protocol-test-v1",
                initial_activation_kind="future-body-v9",
            )

        self.assertFalse((genesis_home / "body").exists())
        self.assertFalse(
            (genesis_home / "trusted" / "state.sqlite3").exists()
        )
        retry = DevelopmentalRuntime.genesis(
            genesis_home,
            host_binding=self.host_binding,
            purpose_anchor="Improve the future of the one bound host.",
            initial_body={"entrypoint.md": "Body zero"},
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )
        self.assertEqual(retry.status().generation, 0)

    def test_off_is_ordered_after_a_concurrent_wake(self) -> None:
        entered_binding = threading.Event()
        release_binding = threading.Event()
        original_start_session = TrustedState.start_session

        def paused_start_session(trusted: TrustedState, **kwargs):
            entered_binding.set()
            release_binding.wait(timeout=5)
            return original_start_session(trusted, **kwargs)

        wake_failures: list[Exception] = []
        off_completed = threading.Event()

        def wake() -> None:
            try:
                self.runtime.wake(
                    execution_surface="codex",
                    session_id="concurrent-session",
                    project_environment="project-a",
                )
            except Exception as exc:
                wake_failures.append(exc)

        def turn_off() -> None:
            self.runtime.turn_off()
            off_completed.set()

        with patch.object(TrustedState, "start_session", paused_start_session):
            wake_thread = threading.Thread(target=wake)
            wake_thread.start()
            self.assertTrue(entered_binding.wait(timeout=2))
            off_thread = threading.Thread(target=turn_off)
            off_thread.start()
            off_finished_before_wake = off_completed.wait(timeout=0.3)
            release_binding.set()
            wake_thread.join(timeout=5)
            off_thread.join(timeout=5)

        self.assertFalse(wake_failures)
        self.assertFalse(off_finished_before_wake)
        self.assertFalse(wake_thread.is_alive())
        self.assertFalse(off_thread.is_alive())
        status = self.runtime.status()
        self.assertEqual(status.lifecycle_state, "off")
        self.assertEqual(status.active_sessions, ())
        event_kinds = [record.event_kind for record in self.runtime.evidence.records()]
        self.assertLess(
            event_kinds.index("session_start"),
            event_kinds.index("host_off"),
        )

    def test_body_bound_to_another_root_cannot_become_current_head(self) -> None:
        before = self.runtime.status()
        foreign_store = BodyStore(self.home / "foreign-body-store")
        foreign_head = foreign_store.commit(
            root="foreign-root",
            parent_head=before.head,
            files={"entrypoint.md": "Foreign body"},
            author_kind="research_instrument",
            activation_kind="surface-context-utf8-v1",
            activation_artifact="entrypoint.md",
        )

        self.runtime.body_store.import_manifest(
            foreign_store.export_manifest(foreign_head)
        )

        with self.assertRaises(RootBindingError):
            self._advance_head(
                expected_head=before.head,
                candidate_head=foreign_head,
            )

        self.assertEqual(self.runtime.status().head, before.head)

    def test_off_stops_wake_and_only_bound_host_can_turn_runtime_back_on(self) -> None:
        self.runtime.turn_off()

        with self.assertRaises(RuntimeOffError):
            self.runtime.wake(
                execution_surface="codex",
                session_id="session-off",
                project_environment="project-a",
            )

        with self.assertRaises(AuthorityError):
            self.runtime.turn_on(host_binding="different-host")

        self.runtime.turn_on(host_binding=self.host_binding)
        wake = self.runtime.wake(
            execution_surface="codex",
            session_id="session-on",
            project_environment="project-a",
        )
        self.assertEqual(wake.root, self.runtime.status().root)

    def test_off_rejects_observation_events(self) -> None:
        self.runtime.turn_off()
        records_after_off = self.runtime.evidence.records()

        with self.assertRaises(RuntimeOffError):
            self.runtime.observe(
                event_kind="tool_use_finished",
                payload={},
            )

        self.assertEqual(self.runtime.evidence.records(), records_after_off)

    def test_forged_body_generation_cannot_advance_head(self) -> None:
        before = self.runtime.status()
        valid_candidate = self._prepare_successor(
            expected_parent=before.head,
            files={"entrypoint.md": "Forged generation"},
            author_kind="research_instrument",
        )
        manifest_path = (
            self.home / "body" / "manifests" / f"{valid_candidate}.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["generation"] = 999
        forged_candidate = sha256_hex(canonical_json_bytes(manifest))
        atomic_write_json(
            self.home / "body" / "manifests" / f"{forged_candidate}.json",
            manifest,
        )

        with self.assertRaises(IntegrityError):
            self._advance_head(
                expected_head=before.head,
                candidate_head=forged_candidate,
            )

        self.assertEqual(self.runtime.status().head, before.head)

    def test_trusted_state_tampering_is_detected(self) -> None:
        db_path = self.home / "trusted" / "state.sqlite3"
        with closing(sqlite3.connect(db_path)) as connection, connection:
            connection.execute(
                "UPDATE state SET head = ? WHERE id = 1",
                ("0" * 64,),
            )

        with self.assertRaises(IntegrityError):
            DevelopmentalRuntime.load(self.home)

    def test_replaying_an_old_state_row_is_detected_against_current_history(self) -> None:
        db_path = self.home / "trusted" / "state.sqlite3"
        with closing(sqlite3.connect(db_path)) as connection:
            old_state = connection.execute(
                """
                SELECT authority, head, revision,
                       last_event_sequence, last_event_hash,
                       checkpoint_sequence, checkpoint_hash, sessions_hash
                FROM state
                WHERE id = 1
                """
            ).fetchone()
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
        with closing(sqlite3.connect(db_path)) as connection, connection:
            connection.execute(
                """
                UPDATE state
                SET authority = ?, head = ?, revision = ?,
                    last_event_sequence = ?, last_event_hash = ?,
                    checkpoint_sequence = ?, checkpoint_hash = ?,
                    sessions_hash = ?
                WHERE id = 1
                """,
                old_state,
            )

        with self.assertRaises(IntegrityError):
            DevelopmentalRuntime.load(self.home)

    def test_load_waits_for_an_in_flight_head_transition(self) -> None:
        before = self.runtime.status()
        candidate = self._prepare_successor(
            expected_parent=before.head,
            files={"entrypoint.md": "Body one"},
            author_kind="research_instrument",
        )
        entered_completion = threading.Event()
        release_completion = threading.Event()
        load_completed = threading.Event()
        load_results: list[DevelopmentalRuntime] = []
        load_failures: list[Exception] = []
        original_insert_checkpoint = TrustedState._insert_checkpoint

        def paused_insert_checkpoint(
            trusted: TrustedState,
            connection: sqlite3.Connection,
            state,
            event,
        ):
            if event.event_kind == "head_advanced":
                entered_completion.set()
                release_completion.wait(timeout=5)
            return original_insert_checkpoint(
                trusted,
                connection,
                state,
                event,
            )

        def advance() -> None:
            self._advance_head(
                expected_head=before.head,
                candidate_head=candidate,
            )

        def load() -> None:
            try:
                load_results.append(DevelopmentalRuntime.load(self.home))
            except Exception as exc:
                load_failures.append(exc)
            finally:
                load_completed.set()

        with patch.object(
            TrustedState,
            "_insert_checkpoint",
            paused_insert_checkpoint,
        ):
            advance_thread = threading.Thread(target=advance)
            advance_thread.start()
            self.assertTrue(entered_completion.wait(timeout=2))
            load_thread = threading.Thread(target=load)
            load_thread.start()
            load_finished_during_transition = load_completed.wait(timeout=0.3)
            release_completion.set()
            advance_thread.join(timeout=5)
            load_thread.join(timeout=5)

        self.assertFalse(load_finished_during_transition)
        self.assertFalse(load_failures)
        self.assertEqual(len(load_results), 1)
        self.assertEqual(load_results[0].status().head, candidate)

    def test_session_state_corruption_is_an_integrity_failure(self) -> None:
        self.runtime.wake(
            execution_surface="codex",
            session_id="session-corrupt",
            project_environment="project-a",
        )
        db_path = self.home / "trusted" / "state.sqlite3"
        with closing(sqlite3.connect(db_path)) as connection, connection:
            connection.execute(
                """
                UPDATE sessions
                SET value_json = ?
                WHERE execution_surface = ? AND session_id = ?
                """,
                (b"not-json", "codex", "session-corrupt"),
            )

        with self.assertRaises(IntegrityError):
            DevelopmentalRuntime.load(self.home)


if __name__ == "__main__":
    unittest.main()
