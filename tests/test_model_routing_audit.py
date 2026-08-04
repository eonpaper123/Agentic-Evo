from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agentic_evo.model_routing_audit import (
    MODEL_ROUTE_NAMESPACE,
    ModelRoutingAuditError,
    record_model_routing_decision,
    validate_model_routing_decision,
    verify_model_routing_decision,
)
from agentic_evo.test_result_receipts import OBSERVED_LOCAL_NAMESPACE, run_test_result_receipt


class ModelRoutingAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "project"
        self.root.mkdir()
        policy = self.root / "docs" / "policy.json"
        policy.parent.mkdir()
        policy.write_text('{"route":"local-decision-only"}\n', encoding="utf-8")
        self.policy_ref = "docs/policy.json"
        self.receipt_id = "a123456789abcdef0123456789abcdef"
        self.receipt = run_test_result_receipt(
            project_root=self.root,
            receipt_namespace=OBSERVED_LOCAL_NAMESPACE,
            run_id=self.receipt_id,
            suite_id="routing-local-unit",
            timeout_seconds=5,
            argv=[sys.executable, "-c", "print('local test only')"],
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _record(self, decision_id: str = "b123456789abcdef0123456789abcdef") -> dict[str, object]:
        return record_model_routing_decision(
            project_root=self.root,
            record_namespace=MODEL_ROUTE_NAMESPACE,
            decision_id=decision_id,
            capability="documentation-review",
            candidate_model_refs=["model-a", "model-b"],
            selected_model_ref="model-a",
            basis="declared_policy",
            policy_ref=self.policy_ref,
            evidence_refs=[f"{OBSERVED_LOCAL_NAMESPACE}/{self.receipt_id}/receipt.json"],
            fallback_model_ref="model-b",
        )

    def test_explicit_decision_binds_candidate_policy_and_local_receipt_without_provider_claim(self) -> None:
        decision_id = "b123456789abcdef0123456789abcdef"
        record = self._record(decision_id)
        directory = self.root / MODEL_ROUTE_NAMESPACE / decision_id
        self.assertEqual({entry.name for entry in directory.iterdir()}, {"record.json"})
        raw = (directory / "record.json").read_bytes()
        self.assertNotIn(b"local test only", raw)
        verified = verify_model_routing_decision(self.root, f"{MODEL_ROUTE_NAMESPACE}/{decision_id}")
        self.assertEqual(verified, record)
        self.assertEqual(verified["provenance"]["class"], "agent_authored_decision")
        self.assertEqual(verified["observed_effect"], {"request_executed": False, "provider_effective_model": "not_observed"})
        self.assertEqual(verified["evidence_refs"][0]["receipt_sha256"], self.receipt["integrity_sha256"])

    def test_candidate_membership_secret_inputs_and_invalid_receipts_fail_closed(self) -> None:
        with self.assertRaises(ModelRoutingAuditError):
            record_model_routing_decision(
                project_root=self.root,
                record_namespace=MODEL_ROUTE_NAMESPACE,
                decision_id="c123456789abcdef0123456789abcdef",
                capability="documentation-review",
                candidate_model_refs=["model-a"],
                selected_model_ref="model-b",
                basis="declared_policy",
                policy_ref=self.policy_ref,
            )
        with self.assertRaises(ModelRoutingAuditError):
            record_model_routing_decision(
                project_root=self.root,
                record_namespace=MODEL_ROUTE_NAMESPACE,
                decision_id="d123456789abcdef0123456789abcdef",
                capability="token=not-allowed",
                candidate_model_refs=["model-a"],
                selected_model_ref="model-a",
                basis="declared_policy",
                policy_ref=self.policy_ref,
            )
        with self.assertRaises(ModelRoutingAuditError):
            record_model_routing_decision(
                project_root=self.root,
                record_namespace=MODEL_ROUTE_NAMESPACE,
                decision_id="e123456789abcdef0123456789abcdef",
                capability="documentation-review",
                candidate_model_refs=["model-a"],
                selected_model_ref="model-a",
                basis="declared_policy",
                policy_ref=self.policy_ref,
                evidence_refs=["tests/fixtures/windows_gate_b/corpus.manifest.json"],
            )

    def test_cli_records_and_verifies_a_no_provider_route_declaration(self) -> None:
        decision_id = "c123456789abcdef0123456789abcdef"
        repository_root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(repository_root / "src")
        record = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentic_evo.cli",
                "record-model-route",
                "--project-root",
                str(self.root),
                "--record-namespace",
                MODEL_ROUTE_NAMESPACE,
                "--decision-id",
                decision_id,
                "--capability",
                "documentation-review",
                "--candidate-model-ref",
                "model-a",
                "--selected-model-ref",
                "model-a",
                "--basis",
                "declared_policy",
                "--policy-ref",
                self.policy_ref,
            ],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(record.returncode, 0, record.stderr)
        payload = json.loads(record.stdout)
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["result"]["provider_effective_model"], "not_observed")
        verify = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentic_evo.cli",
                "verify-model-route",
                "--project-root",
                str(self.root),
                "--record-dir",
                f"{MODEL_ROUTE_NAMESPACE}/{decision_id}",
            ],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertEqual(json.loads(verify.stdout)["result"]["provider_effective_model"], "not_observed")

    def test_policy_drift_tamper_and_effective_model_promotion_are_rejected(self) -> None:
        decision_id = "f123456789abcdef0123456789abcdef"
        self._record(decision_id)
        directory = self.root / MODEL_ROUTE_NAMESPACE / decision_id
        record_path = directory / "record.json"
        saved_policy = (self.root / self.policy_ref).read_bytes()
        (self.root / self.policy_ref).write_text('{"route":"changed"}\n', encoding="utf-8")
        with self.assertRaises(ModelRoutingAuditError):
            verify_model_routing_decision(self.root, f"{MODEL_ROUTE_NAMESPACE}/{decision_id}")
        (self.root / self.policy_ref).write_bytes(saved_policy)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        promoted = copy.deepcopy(record)
        promoted["observed_effect"]["request_executed"] = True
        promoted["observed_effect"]["provider_effective_model"] = "model-a"
        with self.assertRaises(ModelRoutingAuditError):
            validate_model_routing_decision(promoted, project_root=self.root)
        record["selection"]["selected_model_ref"] = "model-b"
        record_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(ModelRoutingAuditError):
            verify_model_routing_decision(self.root, f"{MODEL_ROUTE_NAMESPACE}/{decision_id}")

    def test_reserved_or_wrong_record_namespace_fails_before_publication(self) -> None:
        with self.assertRaises(ModelRoutingAuditError):
            record_model_routing_decision(
                project_root=self.root,
                record_namespace="artifacts/labs/3060-computer/model-routing",
                decision_id="0123456789abcdef0123456789abcdef",
                capability="documentation-review",
                candidate_model_refs=["model-a"],
                selected_model_ref="model-a",
                basis="declared_policy",
                policy_ref=self.policy_ref,
            )
        self.assertFalse((self.root / "artifacts").exists() and (self.root / "artifacts" / "labs").exists())


if __name__ == "__main__":
    unittest.main()
