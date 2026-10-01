"""Born+adopted home semantics for the experiment evidence loop.

A fixture home is born exactly like the Genesis CLI births the REAL born home
(named non-hex root ``agentic-evo-root-v1``, pinned 40-hex non-Body genesis
head) and then adopted with ``runtime-adopt`` (genesis evidence seq 1 +
head_advanced seq 2).  These tests pin the canonical semantics decided in
Slice I:

- ``root_commitment`` = sha256_hex(root) when root is a non-hex born string
  (the hex root itself otherwise), applied consistently to prereg, evidence
  records and Body manifests;
- the pack/anchor validation accepts the born+adopted lineage: start_anchor =
  Genesis evidence, end_anchor = latest evidence, and the Genesis record's
  head_after != current head is legitimate after runtime-adopt.
"""

from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from agentic_evo._util import sha256_hex
from agentic_evo.cli import GENESIS_INSTRUMENT_VERSION
from agentic_evo.errors import IntegrityError
from agentic_evo.experiment_pack import (
    ALLOWED_CONTROL_REFS,
    ALLOWED_HYPOTHESIS_REFS,
    EXPERIMENT_VERIFY_SCHEMA,
    _validate_prereg,
    export_experiment_pack,
    export_experiment_prereg,
    verify_experiment_artifact,
)
from agentic_evo.runtime import DevelopmentalRuntime
from agentic_evo.runtime_adopt import adopt_genesis_home
from agentic_evo.trusted import TRUSTED_SCHEMA_VERSION, TrustedState


BORN_ROOT = "agentic-evo-root-v1"
BORN_ROOT_COMMITMENT = sha256_hex(BORN_ROOT)
# The same 40-hex pinned birth head the REAL born home uses (not a Body
# commitment; there is no manifest for it).
GENESIS_PINNED_HEAD = "ee79ae49b8941344e1f314e1f9183deacf8c089d"
EXECUTION_SURFACE = "opencode"
PROJECT_ENVIRONMENT = r"D:\sample-user\Coding\Agentic-Evo"


class BornHomeExperimentPackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        TrustedState.genesis(
            self.home,
            host_binding="born-host-binding",
            purpose_anchor="Born identity anchor for the fixture home.",
            root=BORN_ROOT,
            initial_head=GENESIS_PINNED_HEAD,
            instrument_version=GENESIS_INSTRUMENT_VERSION,
            protocol_version=TRUSTED_SCHEMA_VERSION,
            genesis_payload={
                "trusted_schema": TRUSTED_SCHEMA_VERSION,
                "source": "genesis_cli",
            },
        )
        adopt_genesis_home(self.home)
        self.runtime = DevelopmentalRuntime(self.home)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _refs() -> tuple[list[str], list[str]]:
        return list(ALLOWED_HYPOTHESIS_REFS), list(ALLOWED_CONTROL_REFS)

    def _records(self) -> list[dict[str, object]]:
        from dataclasses import asdict

        return [asdict(record) for record in self.runtime.evidence.records()]

    def _genesis_anchored_prereg(self) -> dict[str, object]:
        """The task-pinned prereg shape (as committed in experiments/001/)."""

        hypothesis_refs, control_refs = self._refs()
        baseline = export_experiment_prereg(
            self.runtime,
            hypothesis_refs=hypothesis_refs,
            control_refs=control_refs,
        )
        genesis = self._records()[0]
        prereg = dict(baseline)
        prereg["root_commitment"] = BORN_ROOT_COMMITMENT
        prereg["execution_surface"] = EXECUTION_SURFACE
        prereg["project_environment"] = PROJECT_ENVIRONMENT
        prereg["start_anchor"] = {
            "sequence": genesis["sequence"],
            "integrity_hash": genesis["integrity_hash"],
        }
        return prereg

    def _append_observation(self, name: str = "measurement") -> int:
        return self.runtime.observe(
            event_kind=name,
            payload={"measurement": name},
            execution_surface="test-surface",
            session_id="test-session",
            project_environment="test-project",
        ).sequence

    def test_export_prereg_canonicalizes_the_born_named_root(self) -> None:
        hypothesis_refs, control_refs = self._refs()
        prereg = export_experiment_prereg(
            self.runtime,
            hypothesis_refs=hypothesis_refs,
            control_refs=control_refs,
        )
        self.assertEqual(prereg["root_commitment"], BORN_ROOT_COMMITMENT)
        self.assertEqual(
            BORN_ROOT_COMMITMENT,
            "c78860b7a6dc65c6af61f69a6e54f4b5c86f9b8b5d612019a7159c95daabe497",
        )
        # status validation now accepts the canonicalized commitment.
        _validate_prereg(prereg, status=self.runtime.status())

    def test_genesis_anchored_prereg_validates_and_pack_exports_on_born_home(self) -> None:
        prereg = self._genesis_anchored_prereg()
        _validate_prereg(prereg, status=self.runtime.status())

        records = self._records()
        self.assertEqual([record["event_kind"] for record in records], ["genesis", "head_advanced"])
        pack = export_experiment_pack(self.runtime, prereg, end_sequence=records[-1]["sequence"])

        self.assertEqual(pack["end_anchor"]["sequence"], records[-1]["sequence"])
        self.assertEqual(pack["end_anchor"]["integrity_hash"], records[-1]["integrity_hash"])
        self.assertEqual(pack["head_end"], records[-1]["head_after"])
        self.assertEqual(pack["head_end"], self.runtime.status().head)
        self.assertEqual(pack["authority_end"], "on")
        self.assertEqual(
            [record["sequence"] for record in pack["evidence_window"]],
            [records[-1]["sequence"]],
        )
        # The adoption advanced TO head_start, so the manifest chain is the
        # single adopted Body commitment (no duplicate entries).
        self.assertEqual(
            [entry["head"] for entry in pack["body_manifests"]],
            [self.runtime.status().head],
        )
        self.assertEqual(
            pack["body_manifests"][0]["manifest"]["root"],
            BORN_ROOT,
        )

    def test_verify_experiment_artifact_passes_on_born_home_pack(self) -> None:
        prereg = self._genesis_anchored_prereg()
        records = self._records()
        pack = export_experiment_pack(self.runtime, prereg, end_sequence=records[-1]["sequence"])

        result = verify_experiment_artifact(pack)
        self.assertEqual(result["schema"], EXPERIMENT_VERIFY_SCHEMA)
        self.assertIs(result["valid"], True)

    def test_pack_export_rejects_wrong_head_start_on_born_home(self) -> None:
        prereg = self._genesis_anchored_prereg()
        prereg["head_start"] = "0" * 64
        records = self._records()
        with self.assertRaises(IntegrityError):
            export_experiment_pack(self.runtime, prereg, end_sequence=records[-1]["sequence"])

    def test_pack_export_rejects_unknown_anchor_sequence_on_born_home(self) -> None:
        prereg = self._genesis_anchored_prereg()
        prereg["start_anchor"] = {"sequence": 99, "integrity_hash": "0" * 64}
        records = self._records()
        with self.assertRaises(IntegrityError):
            export_experiment_pack(self.runtime, prereg, end_sequence=records[-1]["sequence"])

    def test_tail_anchored_prereg_still_works_with_post_prereg_window(self) -> None:
        # The code-derived (tail-anchored) prereg also validates on the born
        # home; its window only opens once new evidence is appended.
        hypothesis_refs, control_refs = self._refs()
        prereg = export_experiment_prereg(
            self.runtime,
            hypothesis_refs=hypothesis_refs,
            control_refs=control_refs,
        )
        _validate_prereg(prereg, status=self.runtime.status())
        tail = self._records()[-1]["sequence"]
        with self.assertRaises(IntegrityError):
            export_experiment_pack(self.runtime, prereg, end_sequence=tail)

        self._append_observation("post-prereg-observation")
        pack = export_experiment_pack(self.runtime, prereg, end_sequence=tail + 1)
        self.assertEqual(pack["head_end"], self.runtime.status().head)
        self.assertEqual(
            [entry["head"] for entry in pack["body_manifests"]],
            [self.runtime.status().head],
        )
        self.assertIs(verify_experiment_artifact(pack)["valid"], True)

    def test_verify_rejects_tampered_root_and_evidence_on_born_home_pack(self) -> None:
        prereg = self._genesis_anchored_prereg()
        records = self._records()
        pack = export_experiment_pack(self.runtime, prereg, end_sequence=records[-1]["sequence"])

        root_tampered = copy.deepcopy(pack)
        root_tampered["prereg"]["root_commitment"] = "0" * 64
        record_tampered = copy.deepcopy(pack)
        record_tampered["evidence_window"][0]["root_commitment"] = "agentic-evo-root-v2"
        head_tampered = copy.deepcopy(pack)
        head_tampered["prereg"]["head_start"] = "1" * 64

        for invalid in (root_tampered, record_tampered, head_tampered):
            with self.subTest(case=invalid):
                with self.assertRaises(IntegrityError):
                    verify_experiment_artifact(invalid)

    def test_manifest_root_must_commit_to_the_born_root(self) -> None:
        prereg = self._genesis_anchored_prereg()
        records = self._records()
        pack = export_experiment_pack(self.runtime, prereg, end_sequence=records[-1]["sequence"])

        foreign = copy.deepcopy(pack)
        foreign["body_manifests"][0]["manifest"]["root"] = "foreign-root-v1"
        with self.assertRaises(IntegrityError):
            verify_experiment_artifact(foreign)


if __name__ == "__main__":
    unittest.main()
