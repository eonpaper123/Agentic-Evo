from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from agentic_evo._util import canonical_json_bytes, sha256_hex
from agentic_evo.errors import IntegrityError
from agentic_evo.experiment_pack import (
    ALLOWED_CONTROL_REFS,
    ALLOWED_HYPOTHESIS_REFS,
    EXPERIMENT_CLAIM_CEILING,
    EXPERIMENT_PACK_SCHEMA,
    EXPERIMENT_PREREG_SCHEMA,
    EXPERIMENT_PROTOCOL_REF,
    EXPERIMENT_VERIFY_SCHEMA,
    export_experiment_pack,
    export_experiment_prereg,
    verify_experiment_artifact,
)
from agentic_evo.runtime import DevelopmentalRuntime


EXPECTED_HYPOTHESIS_REFS = [
    "H001-A",
    "H001-B",
    "H001-C",
    "H001-D",
    "H001-E",
    "H001-F",
]
EXPECTED_CONTROL_REFS = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
EXPECTED_CLAIM_CEILING = {
    "artifact_integrity": "established",
    "committed_head_lineage": "established",
    "declared_metadata_truth": "not_established",
    "causal_attribution": "not_established",
    "future_capability_gain": "not_established",
    "memory_formation": "not_established",
    "autonomous_learning": "not_established",
    "self_evolution": "not_established",
    "second_real_coding_agent_natural_usage": "not_established",
    "zero_human_learning_intervention": "not_established",
}
MANIFEST_FIELDS = {
    "schema_version",
    "root",
    "parent_head",
    "generation",
    "author_kind",
    "created_at",
    "activation_kind",
    "activation_artifact",
    "files",
}

class ExperimentPackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        self.runtime = DevelopmentalRuntime.genesis(
            self.home,
            host_binding="test-host-binding",
            purpose_anchor="Improve the future of the one bound host.",
            initial_body={"entrypoint.md": "Body zero"},
            instrument_version="instrument-test-v1",
            protocol_version="protocol-test-v1",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _refs() -> tuple[list[str], list[str]]:
        return list(ALLOWED_HYPOTHESIS_REFS), list(ALLOWED_CONTROL_REFS)

    def _prereg(self) -> dict[str, object]:
        hypothesis_refs, control_refs = self._refs()
        return export_experiment_prereg(
            self.runtime,
            hypothesis_refs=hypothesis_refs,
            control_refs=control_refs,
        )

    def _append_observation(self, name: str = "measurement") -> int:
        return self.runtime.observe(
            event_kind=name,
            payload={"measurement": name},
            execution_surface="test-surface",
            session_id="test-session",
            project_environment="test-project",
        ).sequence

    def _pack_with_head_change(self) -> dict[str, object]:
        prereg = self._prereg()
        status = self.runtime.status()
        candidate = self.runtime._prepare_successor(
            expected_parent=status.head,
            files={"entrypoint.md": "Body one"},
            author_kind="research_instrument",
            ingress_path="test_instrument",
            expected_authority_epoch=self.runtime.trusted.authority_epoch(),
        )
        self.runtime._advance_head(
            expected_head=status.head,
            candidate_head=candidate,
            author_kind="research_instrument",
            ingress_path="test_instrument",
            expected_authority_epoch=self.runtime.trusted.authority_epoch(),
        )
        advanced = self.runtime.status()
        self.runtime._prepare_successor(
            expected_parent=advanced.head,
            files={"entrypoint.md": "Unadvanced candidate"},
            author_kind="research_instrument",
            ingress_path="test_instrument",
            expected_authority_epoch=self.runtime.trusted.authority_epoch(),
        )
        self._append_observation("post-head-measurement")
        return export_experiment_pack(self.runtime, prereg)

    def test_export_experiment_prereg_emits_exact_field_set_and_seq_hash_anchor(self) -> None:
        self._append_observation()
        tail = self.runtime.evidence.records()[-1]
        status = self.runtime.status()

        prereg = self._prereg()

        self.assertEqual(
            set(prereg),
            {
                "schema",
                "protocol_ref",
                "instrument_version",
                "protocol_version",
                "root_commitment",
                "head_start",
                "execution_surface",
                "project_environment",
                "hypothesis_refs",
                "control_refs",
                "start_anchor",
                "claim_ceiling",
            },
        )
        self.assertEqual(prereg["schema"], EXPERIMENT_PREREG_SCHEMA)
        self.assertEqual(prereg["protocol_ref"], EXPERIMENT_PROTOCOL_REF)
        self.assertEqual(prereg["instrument_version"], status.instrument_version)
        self.assertEqual(prereg["protocol_version"], status.protocol_version)
        self.assertEqual(prereg["root_commitment"], status.root)
        self.assertEqual(prereg["head_start"], status.head)
        self.assertEqual(list(ALLOWED_HYPOTHESIS_REFS), EXPECTED_HYPOTHESIS_REFS)
        self.assertEqual(list(ALLOWED_CONTROL_REFS), EXPECTED_CONTROL_REFS)
        self.assertEqual(prereg["hypothesis_refs"], EXPECTED_HYPOTHESIS_REFS)
        self.assertEqual(prereg["control_refs"], EXPECTED_CONTROL_REFS)
        self.assertEqual(
            prereg["start_anchor"],
            {"sequence": tail.sequence, "integrity_hash": tail.integrity_hash},
        )
        self.assertEqual(
            list(EXPERIMENT_CLAIM_CEILING.items()),
            list(EXPECTED_CLAIM_CEILING.items()),
        )
        self.assertEqual(
            list(prereg["claim_ceiling"].items()),
            list(EXPECTED_CLAIM_CEILING.items()),
        )

    def test_export_experiment_prereg_copies_tail_execution_surface_and_project_environment_or_null(self) -> None:
        self.assertEqual(self._prereg()["execution_surface"], None)
        self.assertEqual(self._prereg()["project_environment"], None)

        self._append_observation()
        prereg = self._prereg()

        self.assertEqual(prereg["execution_surface"], "test-surface")
        self.assertEqual(prereg["project_environment"], "test-project")

    def test_export_experiment_prereg_rejects_unknown_duplicate_or_misordered_refs(self) -> None:
        hypotheses, controls = self._refs()
        cases = (
            (hypotheses + ["unknown-hypothesis"], controls),
            (hypotheses + [hypotheses[0]], controls),
            (list(reversed(hypotheses)), controls),
            (hypotheses, controls + ["unknown-control"]),
            (hypotheses, controls + [controls[0]]),
            (hypotheses, list(reversed(controls))),
        )

        for hypothesis_refs, control_refs in cases:
            with self.subTest(
                hypothesis_refs=hypothesis_refs,
                control_refs=control_refs,
            ):
                with self.assertRaises(IntegrityError):
                    export_experiment_prereg(
                        self.runtime,
                        hypothesis_refs=hypothesis_refs,
                        control_refs=control_refs,
                    )

    def test_export_experiment_pack_uses_exclusive_start_inclusive_end_window(self) -> None:
        prereg = self._prereg()
        first = self._append_observation("first")
        second = self._append_observation("second")

        pack = export_experiment_pack(self.runtime, prereg, end_sequence=second)

        self.assertEqual(
            [record["sequence"] for record in pack["evidence_window"]],
            [first, second],
        )
        self.assertEqual(pack["end_anchor"]["sequence"], second)

    def test_export_experiment_pack_rejects_empty_or_nonforward_window(self) -> None:
        prereg = self._prereg()
        start = prereg["start_anchor"]["sequence"]

        for end_sequence in (start, start - 1):
            with self.subTest(end_sequence=end_sequence):
                with self.assertRaises(IntegrityError):
                    export_experiment_pack(
                        self.runtime,
                        prereg,
                        end_sequence=end_sequence,
                    )

    def test_export_experiment_pack_rejects_end_sequence_beyond_current_tail_without_clamp(self) -> None:
        prereg = self._prereg()
        tail = self._append_observation()

        with self.assertRaises(IntegrityError):
            export_experiment_pack(self.runtime, prereg, end_sequence=tail + 1)

    def test_export_experiment_pack_exports_ordered_committed_manifest_chain_without_blobs(self) -> None:
        pack = self._pack_with_head_change()
        expected_heads = [
            pack["prereg"]["head_start"],
            pack["head_end"],
        ]
        expected_manifests = [
            json.loads(
                (self.runtime.body_store.manifest_path / f"{head}.json").read_text(
                    encoding="utf-8"
                )
            )
            for head in expected_heads
        ]

        self.assertEqual(
            set(pack),
            {
                "schema",
                "prereg",
                "end_anchor",
                "head_end",
                "authority_end",
                "evidence_window",
                "body_manifests",
                "claim_ceiling",
            },
        )
        self.assertEqual(pack["schema"], EXPERIMENT_PACK_SCHEMA)
        self.assertEqual(len(pack["body_manifests"]), 2)
        self.assertEqual(
            pack["body_manifests"],
            [
                {"head": head, "manifest": manifest}
                for head, manifest in zip(expected_heads, expected_manifests)
            ],
        )
        for entry in pack["body_manifests"]:
            self.assertEqual(set(entry), {"head", "manifest"})
            self.assertRegex(entry["head"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                entry["head"], sha256_hex(canonical_json_bytes(entry["manifest"]))
            )
            self.assertEqual(set(entry["manifest"]), MANIFEST_FIELDS)
            self.assertNotIn("manifest_base64", entry["manifest"])
            self.assertNotIn("blobs", entry["manifest"])
        self.assertEqual(pack["body_manifests"][0]["head"], pack["prereg"]["head_start"])
        self.assertEqual(pack["body_manifests"][-1]["head"], pack["head_end"])
        self.assertEqual(
            pack["body_manifests"][0]["manifest"]["root"],
            pack["prereg"]["root_commitment"],
        )
        self.assertEqual(
            pack["body_manifests"][1]["manifest"]["root"],
            pack["prereg"]["root_commitment"],
        )
        self.assertIsNone(pack["body_manifests"][0]["manifest"]["parent_head"])
        self.assertEqual(
            pack["body_manifests"][1]["manifest"]["parent_head"],
            pack["body_manifests"][0]["head"],
        )

    def test_verify_experiment_artifact_fails_closed_on_legacy_or_unknown_schema(self) -> None:
        pack = self._pack_with_head_change()

        for schema in ("agentic-evo.experiment-pack.v0", "unknown-schema"):
            with self.subTest(schema=schema):
                invalid = copy.deepcopy(pack)
                invalid["schema"] = schema
                with self.assertRaises(IntegrityError):
                    verify_experiment_artifact(invalid)

    def test_verify_pack_rejects_claim_ceiling_tampering(self) -> None:
        established = self._pack_with_head_change()
        established["claim_ceiling"]["future_capability_gain"] = "established"
        extra = self._pack_with_head_change()
        extra["claim_ceiling"]["capability"] = "evaluated"

        for invalid in (established, extra):
            with self.assertRaises(IntegrityError):
                verify_experiment_artifact(invalid)

    def test_verify_pack_rejects_sequence_gap_anchor_mismatch_and_record_hash_tamper(self) -> None:
        pack = self._pack_with_head_change()

        gap = copy.deepcopy(pack)
        gap["evidence_window"][-1]["sequence"] += 1
        anchor = copy.deepcopy(pack)
        anchor["end_anchor"]["integrity_hash"] = "0" * 64
        tampered = copy.deepcopy(pack)
        tampered["evidence_window"][0]["integrity_hash"] = "0" * 64
        empty = copy.deepcopy(pack)
        empty["evidence_window"] = []

        for invalid in (gap, anchor, tampered, empty):
            with self.assertRaises(IntegrityError):
                verify_experiment_artifact(invalid)

    def test_verify_pack_rejects_missing_unexpected_reversed_or_unlinked_manifests(self) -> None:
        pack = self._pack_with_head_change()
        missing = copy.deepcopy(pack)
        missing["body_manifests"].pop()
        unexpected = copy.deepcopy(pack)
        unexpected["body_manifests"].append(copy.deepcopy(pack["body_manifests"][0]))
        reversed_chain = copy.deepcopy(pack)
        reversed_chain["body_manifests"].reverse()
        parent_mismatch = copy.deepcopy(pack)
        parent_mismatch["body_manifests"][1]["manifest"]["parent_head"] = "0" * 64

        for invalid in (missing, unexpected, reversed_chain, parent_mismatch):
            with self.assertRaises(IntegrityError):
                verify_experiment_artifact(invalid)

    def test_verify_pack_passes_without_checkpoint_mac_witness_blob_or_surface_identity_inputs(self) -> None:
        pack = self._pack_with_head_change()

        result = verify_experiment_artifact(pack)

        self.assertEqual(result["schema"], EXPERIMENT_VERIFY_SCHEMA)
        self.assertIs(result["valid"], True)


if __name__ == "__main__":
    unittest.main()
