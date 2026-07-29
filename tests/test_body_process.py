from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentic_evo._util import sha256_hex
from agentic_evo.body_process import (
    BODY_BOOT_PROTOCOL,
    BodyBootError,
    BodyProcessSupervisor,
    ReadyEcho,
    validate_ready_echo,
)
from agentic_evo.runtime import DevelopmentalRuntime
from agentic_evo.witness import WitnessCore


class BodyProcessTests(unittest.TestCase):
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
        self.witness = WitnessCore(self.runtime, lease_seconds=10.0)
        self.supervisor = BodyProcessSupervisor(
            self.runtime,
            self.witness,
            ready_timeout_seconds=2.0,
        )
        self.bodies = []

    def tearDown(self) -> None:
        for body in reversed(self.bodies):
            body.close()
        self.witness.close()
        self.tempdir.cleanup()

    def _spawn(self):
        body = self.supervisor.spawn_current()
        self.bodies.append(body)
        return body

    def test_exact_head_snapshot_crosses_only_the_private_boot_pipe(
        self,
    ) -> None:
        status = self.runtime.status()
        body = self._spawn()

        self.assertTrue(body.is_alive())
        self.assertEqual(body.boot.protocol, BODY_BOOT_PROTOCOL)
        self.assertEqual(body.boot.root, status.root)
        self.assertEqual(body.boot.head, status.head)
        self.assertEqual(body.boot.generation, status.generation)
        self.assertEqual(
            body.boot.activation_digest,
            sha256_hex(b"Body zero"),
        )
        self.assertEqual(body.ready.root, body.boot.root)
        self.assertEqual(body.ready.head, body.boot.head)
        self.assertEqual(body.ready.challenge, body.boot.challenge)
        self.assertEqual(body.ready.boot_session, body.boot.boot_session)
        self.assertEqual(
            body.ready.activation_digest,
            body.boot.activation_digest,
        )

        serialized_command = "\0".join(body.command)
        self.assertNotIn(status.root, serialized_command)
        self.assertNotIn(status.head, serialized_command)
        self.assertNotIn(body.boot.challenge, serialized_command)
        self.assertNotIn(body.boot.boot_session, serialized_command)

        body.close()
        replacement = self._spawn()
        self.assertTrue(replacement.is_alive())
        self.assertNotEqual(
            replacement.boot.boot_session,
            body.boot.boot_session,
        )
        self.assertNotEqual(
            replacement.boot.challenge,
            body.boot.challenge,
        )

    def test_ready_echo_rejects_every_changed_binding_field(self) -> None:
        body = self._spawn()
        ready = body.ready
        replacements = {
            "protocol": "wrong-private-protocol",
            "boot_session": "wrong-session",
            "challenge": "wrong-challenge",
            "root": "wrong-root",
            "head": "f" * 64,
            "generation": ready.generation + 1,
            "activation_kind": "wrong-kind",
            "activation_artifact": "wrong-artifact",
            "activation_digest": "e" * 64,
        }

        for field, value in replacements.items():
            with self.subTest(field=field):
                forged = replace(ready, **{field: value})
                with self.assertRaises(BodyBootError):
                    validate_ready_echo(body.boot, forged)

    def test_binding_change_after_ready_aborts_boot_and_releases_lease(
        self,
    ) -> None:
        original_authorize = self.witness._authorize

        def cross_off_boundary(session) -> None:
            self.runtime.turn_off()
            original_authorize(session)

        with patch.object(
            self.witness,
            "_authorize",
            side_effect=cross_off_boundary,
        ):
            with self.assertRaises(BodyBootError):
                self.supervisor.spawn_current()

        self.runtime.turn_on(host_binding=self.host_binding)
        replacement = self._spawn()
        self.assertTrue(replacement.is_alive())

    def test_worker_crash_retires_volatile_lease_and_allows_same_head_restart(
        self,
    ) -> None:
        crashed = self._spawn()
        crashed._process.kill()
        self.assertTrue(crashed.wait_closed(timeout_seconds=5.0))

        replacement = self._spawn()
        self.assertEqual(replacement.boot.head, crashed.boot.head)
        self.assertNotEqual(
            replacement.boot.boot_session,
            crashed.boot.boot_session,
        )

    def test_valid_large_current_head_is_streamed_without_a_boot_size_cliff(
        self,
    ) -> None:
        session = self.witness.open_current_body_session(
            expected_head=self.runtime.status().head
        )
        large_activation = b"x" * (6 * 1024 * 1024)
        candidate = session.prepare_successor(
            files={"entrypoint.md": large_activation}
        )
        session.advance_head(candidate_head=candidate)

        body = self._spawn()
        self.assertTrue(body.is_alive())
        self.assertEqual(
            body.boot.activation_digest,
            sha256_hex(large_activation),
        )

    def test_invalid_ready_echo_is_not_accepted_as_a_live_body(self) -> None:
        body = self._spawn()
        forged = ReadyEcho(
            protocol=body.ready.protocol,
            boot_session=body.ready.boot_session,
            challenge=body.ready.challenge,
            root=body.ready.root,
            head=body.ready.head,
            generation=body.ready.generation,
            activation_kind=body.ready.activation_kind,
            activation_artifact=body.ready.activation_artifact,
            activation_digest="0" * 64,
        )
        with self.assertRaises(BodyBootError):
            validate_ready_echo(body.boot, forged)
        self.assertTrue(body.is_alive())

    def test_boot_does_not_claim_agent_self_authorship(self) -> None:
        before = self.runtime.evidence.records()
        body = self._spawn()

        self.assertEqual(body.provenance, "subprocess_rehearsal")
        self.assertEqual(self.runtime.evidence.records(), before)
        self.assertNotIn(
            "agent_self_authored",
            {record.author_kind for record in self.runtime.evidence.records()},
        )


if __name__ == "__main__":
    unittest.main()
