from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from agentic_evo.evidence_provenance import (
    CLAIM_CEILINGS,
    PROVENANCE_CLASSES,
    EvidenceProvenanceError,
    ensure_ordinary_relative_directory,
    expected_claim_ceiling,
    normalize_relative_path,
    reject_secret_shaped,
    validate_provenance_path,
    validate_record_provenance,
)
from agentic_evo.test_result_receipts import (
    OBSERVED_LOCAL_NAMESPACE,
    TestResultReceiptError,
    verify_test_result_receipt,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "windows_gate_b" / "corpus.manifest.json"


class EvidenceProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "project"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_closed_classes_have_fixed_one_way_roots_and_canonical_observed_ceiling(self) -> None:
        self.assertEqual(
            PROVENANCE_CLASSES,
            (
                "synthetic_fixture",
                "observed_local_test",
                "agent_authored_decision",
                "external_authorized_observation",
            ),
        )
        self.assertEqual(expected_claim_ceiling("observed_local_test"), "local test-process outcome only")
        self.assertEqual(CLAIM_CEILINGS["synthetic_fixture"], "fixture/parser/verifier behavior only")
        local = validate_provenance_path(
            self.root,
            "observed_local_test",
            "artifacts/p0-p1/observed-local/test-results/a123456789abcdef0123456789abcdef",
        )
        self.assertEqual(local, self.root / "artifacts" / "p0-p1" / "observed-local" / "test-results" / "a123456789abcdef0123456789abcdef")
        with self.assertRaises(EvidenceProvenanceError):
            validate_provenance_path(self.root, "observed_local_test", "tests/fixtures/windows_gate_b")
        with self.assertRaises(EvidenceProvenanceError):
            validate_provenance_path(self.root, "external_authorized_observation", "artifacts/anything")

    def test_relative_normalization_reserved_labs_and_links_fail_closed(self) -> None:
        for unsafe in ("../outside", "/rooted", "C:\\rooted", "a//b", "a/./b", "a/../b"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(EvidenceProvenanceError):
                    normalize_relative_path(unsafe)
        with self.assertRaises(EvidenceProvenanceError):
            validate_provenance_path(
                self.root,
                "observed_local_test",
                "artifacts/labs/3060-computer/windows-gate-b",
            )
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "artifacts"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("host cannot create a directory symlink for the fail-closed test")
        with self.assertRaises(EvidenceProvenanceError):
            ensure_ordinary_relative_directory(self.root, OBSERVED_LOCAL_NAMESPACE)

    def test_explicit_record_class_cannot_be_upgraded_by_binding_or_digest(self) -> None:
        provenance = {
            "class": "synthetic_fixture",
            "producer": "fixture-producer",
            "authorization_ref": None,
            "claim_ceiling": ["fixture/parser/verifier behavior only"],
        }
        validate_record_provenance(
            provenance,
            provenance_class="synthetic_fixture",
            producer="fixture-producer",
        )
        promoted = dict(provenance)
        promoted["class"] = "observed_local_test"
        with self.assertRaises(EvidenceProvenanceError):
            validate_record_provenance(
                promoted,
                provenance_class="synthetic_fixture",
                producer="fixture-producer",
            )
        with self.assertRaises(EvidenceProvenanceError):
            validate_record_provenance(
                provenance,
                provenance_class="observed_local_test",
                producer="fixture-producer",
            )

    def test_secret_shaped_values_are_rejected_before_persistence(self) -> None:
        for value in ("password=hush", "Bearer abc", "sk-example", "github_pat_example", "client_secret=x"):
            with self.subTest(value=value):
                with self.assertRaises(EvidenceProvenanceError):
                    reject_secret_shaped(value, field="test")

    def test_frozen_corpus_members_remain_synthetic_without_byte_mutation_or_promotion(self) -> None:
        before = CORPUS_PATH.read_bytes()
        before_hash = hashlib.sha256(before).hexdigest()
        corpus = json.loads(before.decode("utf-8"))
        materializable = [member for member in corpus["members"] if member["materializable"]]
        self.assertTrue(materializable)
        self.assertTrue(all(member["kind"] == "synthetic_executable" for member in materializable))
        self.assertEqual(next(member for member in materializable if member["label"] == "v2_synthetic")["lab_binding"], "bound")
        with self.assertRaises(TestResultReceiptError):
            verify_test_result_receipt(
                REPOSITORY_ROOT,
                "tests/fixtures/windows_gate_b",
            )
        after = CORPUS_PATH.read_bytes()
        self.assertEqual(after, before)
        self.assertEqual(hashlib.sha256(after).hexdigest(), before_hash)


if __name__ == "__main__":
    unittest.main()
