from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from uuid import uuid4


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GATE_B_SCRIPT = REPOSITORY_ROOT / "tools" / "windows-gate-b-experiment.ps1"
ARTIFACT_NAME = "AgenticEvo.ScmProbe.exe"
MANIFEST_NAME = "gate-a-manifest.json"
GATE_B_CASE_IDS = {
    "C01",
    "C02",
    "I01",
    "S01",
    "S02",
    "S03",
    "P01",
    "P02",
    "L01",
    "R01",
    "R02",
    "U01",
}
EXPECTED_ATTACK_FAILURE_CODES = {
    "A01_artifact_tamper": "artifact_commitment_mismatch",
    "A03_coordinated_bundle_substitution": "manifest_commitment_mismatch",
    "A04_forged_passed_receipt": "receipt_claim_ceiling_exceeded",
    "A05_undeclared_bundle_entry": "undeclared_bundle_entry",
    "A07_cleanup_root_swap": "cleanup_root_swap_blocked",
}


def _canonical_json(value: dict[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FourStateReductionTests(unittest.TestCase):
    def test_required_case_reduction_preserves_counterexamples_and_unknowns(
        self,
    ) -> None:
        from agentic_evo.windows_gate_b_evidence import reduce_case_statuses

        self.assertEqual(reduce_case_statuses(["not_run", "not_run"]), "not_run")
        self.assertEqual(reduce_case_statuses(["passed", "passed"]), "passed")
        self.assertEqual(
            reduce_case_statuses(["passed", "inconclusive"]),
            "inconclusive",
        )
        self.assertEqual(
            reduce_case_statuses(["passed", "not_run"]),
            "inconclusive",
        )
        self.assertEqual(
            reduce_case_statuses(["failed", "inconclusive", "not_run"]),
            "failed",
        )

        with self.assertRaises(ValueError):
            reduce_case_statuses([])
        with self.assertRaises(ValueError):
            reduce_case_statuses(["partial"])


@unittest.skipUnless(sys.platform == "win32", "Windows evidence contract")
class WindowsGateBEvidenceTests(unittest.TestCase):
    def _create_fixture(self, root: Path) -> dict[str, object]:
        from agentic_evo.windows_gate_a import prepare_gate_a_bundle

        bundle = root / "bundle"
        manifest = prepare_gate_a_bundle(bundle)
        manifest_path = bundle / MANIFEST_NAME
        artifact = bundle / ARTIFACT_NAME
        script_sha256 = _sha256(GATE_B_SCRIPT)
        lab_id = "3060-computer"
        run_id = uuid4().hex
        challenge = uuid4().hex
        evidence = root / "evidence"
        plan_process = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(GATE_B_SCRIPT),
                "-Mode",
                "Plan",
                "-RunId",
                run_id,
                "-ArtifactPath",
                str(artifact),
                "-ExpectedArtifactSha256",
                manifest["artifact"]["sha256"],
                "-EvidenceRoot",
                str(evidence),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(plan_process.returncode, 0, plan_process.stderr)
        plan = json.loads(plan_process.stdout)
        evidence.mkdir()
        plan_bytes = _canonical_json(plan)
        (evidence / "plan.json").write_bytes(plan_bytes)
        plan_sha256 = hashlib.sha256(plan_bytes[:-1]).hexdigest()

        showsid = subprocess.run(
            [
                str(Path(plan["trusted_system_directory"]) / "sc.exe"),
                "showsid",
                plan["service_name"],
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(showsid.returncode, 0, showsid.stderr)
        service_sid_match = re.search(
            r"S-1-5-80(?:-\d+){5}",
            showsid.stdout,
        )
        self.assertIsNotNone(service_sid_match, showsid.stdout)
        service_sid = service_sid_match.group(0)
        image_path = (
            f'"{plan["artifact_path"]}" service '
            f'--service-name {plan["service_name"]} '
            f'--probe-path "{plan["probe_path"]}"'
        )
        full_control = 2_032_127
        read_execute = 1_179_817
        observation = {
            "create": {
                "arguments": ["create", plan["service_name"]],
                "exit_code": 0,
                "timed_out": False,
                "output": "fixture receipt only",
            },
            "sidtype": {
                "arguments": ["sidtype", plan["service_name"], "restricted"],
                "exit_code": 0,
                "timed_out": False,
                "output": "fixture receipt only",
            },
            "qsidtype": {
                "arguments": ["qsidtype", plan["service_name"]],
                "exit_code": 0,
                "timed_out": False,
                "output": "SERVICE_SID_TYPE: RESTRICTED",
            },
            "service": {
                "name": plan["service_name"],
                "state": "Stopped",
                "process_id": 0,
                "service_type": "Own Process",
                "start_name": "NT AUTHORITY\\LocalService",
                "path_name": image_path,
            },
            "artifact_sha256": manifest["artifact"]["sha256"],
            "artifact_acl": {
                "owner": "BUILTIN\\Administrators",
                "protected": True,
                "rules": [
                    {
                        "sid": "S-1-5-18",
                        "rights": full_control,
                        "type": "Allow",
                        "inherited": False,
                    },
                    {
                        "sid": "S-1-5-32-544",
                        "rights": full_control,
                        "type": "Allow",
                        "inherited": False,
                    },
                    {
                        "sid": service_sid,
                        "rights": read_execute,
                        "type": "Allow",
                        "inherited": False,
                    },
                ],
            },
            "state_acl": {
                "owner": "BUILTIN\\Administrators",
                "protected": True,
                "rules": [
                    {
                        "sid": "S-1-5-18",
                        "rights": full_control,
                        "type": "Allow",
                        "inherited": False,
                    },
                    {
                        "sid": "S-1-5-32-544",
                        "rights": full_control,
                        "type": "Allow",
                        "inherited": False,
                    },
                    {
                        "sid": service_sid,
                        "rights": full_control,
                        "type": "Allow",
                        "inherited": False,
                    },
                ],
            },
            "service_started": False,
            "probe_created": False,
            "restricted_service_sid_configuration": (
                "configuration_write_accepted_before_cleanup"
            ),
            "running_token_restricted_sid": "not_observed",
        }
        cleanup = {
            "complete": True,
            "service_created_or_reconciled": True,
            "delete": {
                "arguments": ["delete", plan["service_name"]],
                "exit_code": 0,
                "timed_out": False,
                "output": "fixture receipt only",
            },
            "service_absent": True,
            "artifact_tree_absent": True,
            "state_tree_absent": True,
            "errors": [],
        }
        report = {
            "schema": "agentic-evo.windows-gate-b-config-probe.v1",
            "lab_id": lab_id,
            "run_id": run_id,
            "challenge": challenge,
            "plan_sha256": plan_sha256,
            "script_sha256": script_sha256,
            "status": "configuration_probe_completed",
            "error": "",
            "elevated_administrator": True,
            "observation": observation,
            "cleanup": cleanup,
            "claims": {
                "gate_a_complete": False,
                "gate_b_outcome": "not_established",
                "native_security_verified": False,
                "ready_to_install": False,
                "temporary_service_created": True,
                "restricted_sid_configured": True,
                "restricted_service_sid_configuration_write": (
                    "accepted_before_cleanup"
                ),
                "reboot_validation": "not_performed_service_removed",
                "genesis_requested": False,
                "genesis_count": "not_measured",
                "all_attack_cases": "not_run",
                "U01": "partial_cleanup_pass_genesis_not_measured",
            },
        }
        independent_cleanup = {
            "complete": True,
            "service_absent": True,
            "artifact_tree_absent": True,
            "state_tree_absent": True,
        }
        result = {
            "schema": "agentic-evo.windows-gate-b-result.v1",
            "lab_id": lab_id,
            "run_id": run_id,
            "challenge": challenge,
            "status": "configuration_probe_completed",
            "gate_b_outcome": "not_established",
            "elevated_exit_code": 0,
            "elevated_pipe_client_pid": 1234,
            "report": report,
            "independent_cleanup": independent_cleanup,
        }
        result_path = evidence / "result.json"
        result_path.write_bytes(_canonical_json(result))
        return {
            "bundle": bundle,
            "evidence": evidence,
            "manifest": manifest,
            "manifest_sha256": _sha256(manifest_path),
            "script_sha256": script_sha256,
            "result_sha256": _sha256(result_path),
            "lab_id": lab_id,
            "run_id": run_id,
            "challenge": challenge,
        }

    def _verify(
        self,
        fixture: dict[str, object],
        **expected_overrides: object,
    ) -> dict[str, object]:
        from agentic_evo.windows_gate_b_evidence import verify_gate_b_evidence

        expected = {
            "expected_manifest_sha256": str(fixture["manifest_sha256"]),
            "expected_script_sha256": str(fixture["script_sha256"]),
            "expected_result_sha256": str(fixture["result_sha256"]),
            "expected_lab_id": str(fixture["lab_id"]),
            "expected_run_id": str(fixture["run_id"]),
            "expected_challenge": str(fixture["challenge"]),
        }
        expected.update(expected_overrides)
        return verify_gate_b_evidence(
            Path(fixture["bundle"]),
            Path(fixture["evidence"]),
            GATE_B_SCRIPT,
            **expected,
        )

    def _run_gate_b_cli(
        self,
        command: str,
        fixture: dict[str, object],
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        prior = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            str(REPOSITORY_ROOT / "src")
            if not prior
            else os.pathsep.join((str(REPOSITORY_ROOT / "src"), prior))
        )
        return subprocess.run(
            [
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.cli",
                command,
                "--bundle-dir",
                str(fixture["bundle"]),
                "--evidence-dir",
                str(fixture["evidence"]),
                "--gate-b-script",
                str(GATE_B_SCRIPT),
                "--expected-manifest-sha256",
                str(fixture["manifest_sha256"]),
                "--expected-script-sha256",
                str(fixture["script_sha256"]),
                "--expected-result-sha256",
                str(fixture["result_sha256"]),
                "--lab-id",
                str(fixture["lab_id"]),
                "--expected-run-id",
                str(fixture["run_id"]),
                "--expected-challenge",
                str(fixture["challenge"]),
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def test_verifier_recomputes_bounded_evidence_without_writes_or_overclaim(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_fixture(Path(temporary))
            tracked = [
                Path(fixture["bundle"]) / ARTIFACT_NAME,
                Path(fixture["bundle"]) / MANIFEST_NAME,
                Path(fixture["evidence"]) / "plan.json",
                Path(fixture["evidence"]) / "result.json",
                GATE_B_SCRIPT,
            ]
            before = {str(path): _sha256(path) for path in tracked}

            verification = self._verify(fixture)

            self.assertEqual(
                verification["schema"],
                "agentic-evo.windows-gate-b-evidence-verification.v1",
            )
            self.assertEqual(verification["status"], "passed")
            self.assertEqual(
                verification["scope"],
                "bounded_evidence_and_current_zero_residue",
            )
            self.assertFalse(verification["verifier_context"]["elevated"])
            self.assertFalse(any(verification["verifier_context"]["effects"].values()))
            self.assertTrue(all(item["match"] for item in verification["anchors"].values()))
            self.assertEqual(verification["lab_id"], fixture["lab_id"])
            self.assertEqual(verification["run_id"], fixture["run_id"])
            self.assertEqual(verification["challenge"], fixture["challenge"])
            self.assertEqual(
                set(verification["case_matrix"]),
                GATE_B_CASE_IDS,
            )
            for case_id, outcome in verification["case_matrix"].items():
                expected = "inconclusive" if case_id == "U01" else "not_run"
                self.assertEqual(outcome["status"], expected)
            self.assertEqual(
                verification["historical_configuration"]["status"],
                "inconclusive",
            )
            self.assertEqual(
                verification["claims"],
                {
                    "bounded_receipt_consistency_verified": True,
                    "gate_a_complete": False,
                    "gate_b_outcome": "not_established",
                    "native_security_verified": False,
                    "ready_to_install": False,
                    "scm_probe_bundle_ready": True,
                    "zero_residue_observed_now": True,
                },
            )
            self.assertEqual(
                {str(path): _sha256(path) for path in tracked},
                before,
            )

    def test_verifier_fails_closed_for_missing_external_anchor_or_identity_mismatch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_fixture(Path(temporary))
            missing_anchors = (
                "expected_manifest_sha256",
                "expected_script_sha256",
                "expected_result_sha256",
            )
            for field in missing_anchors:
                with self.subTest(missing_anchor=field):
                    verification = self._verify(fixture, **{field: None})
                    self.assertEqual(verification["status"], "failed")
                    self.assertIn(
                        "external_anchor_missing",
                        {failure["code"] for failure in verification["failures"]},
                    )

            identity_mismatches = {
                "expected_lab_id": "other-lab",
                "expected_run_id": "other-run",
                "expected_challenge": "other-challenge",
            }
            for field, wrong_value in identity_mismatches.items():
                with self.subTest(identity=field):
                    verification = self._verify(fixture, **{field: wrong_value})
                    self.assertEqual(verification["status"], "failed")
                    self.assertIn(
                        field.removeprefix("expected_") + "_mismatch",
                        {failure["code"] for failure in verification["failures"]},
                    )

    def test_external_anchor_rejects_coordinated_artifact_manifest_substitution(
        self,
    ) -> None:
        from agentic_evo.windows_gate_a import verify_gate_a_bundle

        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_fixture(Path(temporary))
            bundle = Path(fixture["bundle"])
            artifact = bundle / ARTIFACT_NAME
            manifest_path = bundle / MANIFEST_NAME
            artifact.write_bytes(artifact.read_bytes() + b"substitute-tail")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifact"]["bytes"] = artifact.stat().st_size
            manifest["artifact"]["sha256"] = _sha256(artifact)
            manifest_path.write_bytes(
                (
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")
            )

            self.assertEqual(verify_gate_a_bundle(bundle), manifest)
            verification = self._verify(fixture)

            self.assertEqual(verification["status"], "failed")
            self.assertIn(
                "manifest_commitment_mismatch",
                {failure["code"] for failure in verification["failures"]},
            )
            self.assertFalse(
                verification["claims"]["bounded_receipt_consistency_verified"]
            )

    def test_forged_passed_receipt_fails_anchor_and_semantic_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_fixture(Path(temporary))
            result_path = Path(fixture["evidence"]) / "result.json"
            forged = json.loads(result_path.read_text(encoding="utf-8"))
            forged["gate_b_outcome"] = "passed"
            forged["report"]["claims"]["gate_a_complete"] = True
            forged["report"]["claims"]["native_security_verified"] = True
            forged["report"]["claims"]["ready_to_install"] = True
            result_path.write_bytes(_canonical_json(forged))

            anchored = self._verify(fixture)
            self.assertEqual(anchored["status"], "failed")
            self.assertIn(
                "result_commitment_mismatch",
                {failure["code"] for failure in anchored["failures"]},
            )

            fixture["result_sha256"] = _sha256(result_path)
            repinned = self._verify(fixture)
            self.assertEqual(repinned["status"], "failed")
            self.assertIn(
                "receipt_claim_ceiling_exceeded",
                {failure["code"] for failure in repinned["failures"]},
            )
            self.assertFalse(repinned["claims"]["gate_a_complete"])
            self.assertEqual(
                repinned["claims"]["gate_b_outcome"],
                "not_established",
            )

    def test_result_and_report_schema_drift_fail_closed_even_when_repinned(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_fixture(Path(temporary))
            result_path = Path(fixture["evidence"]) / "result.json"
            for field, expected_code in (
                ("schema", "result_contract_invalid"),
                ("report.schema", "report_contract_invalid"),
            ):
                with self.subTest(field=field):
                    drifted = json.loads(result_path.read_text(encoding="utf-8"))
                    if field == "schema":
                        drifted["schema"] = "evil.result.v999"
                    else:
                        drifted["report"]["schema"] = "evil.report.v999"
                    result_path.write_bytes(_canonical_json(drifted))
                    fixture["result_sha256"] = _sha256(result_path)

                    verification = self._verify(fixture)

                    self.assertEqual(verification["status"], "failed")
                    self.assertIn(
                        expected_code,
                        {failure["code"] for failure in verification["failures"]},
                    )

    def test_plan_namespace_retarget_fails_closed_even_when_repinned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_fixture(Path(temporary))
            plan_path = Path(fixture["evidence"]) / "plan.json"
            result_path = Path(fixture["evidence"]) / "result.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["artifact_product_base"] = r"C:\totally\unrelated-artifact-base"
            plan["artifact_root"] = r"C:\totally\unrelated-artifact-root"
            plan["artifact_path"] = (
                r"C:\totally\unrelated-artifact-root\AgenticEvo.ScmProbe.exe"
            )
            plan["state_product_base"] = r"C:\totally\unrelated-state-base"
            plan["state_root"] = r"C:\totally\unrelated-state-root"
            plan["probe_path"] = r"C:\totally\unrelated-state-root\scm-write.probe"
            plan_path.write_bytes(_canonical_json(plan))

            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["report"]["plan_sha256"] = hashlib.sha256(
                _canonical_json(plan)[:-1]
            ).hexdigest()
            result_path.write_bytes(_canonical_json(result))
            fixture["result_sha256"] = _sha256(result_path)

            verification = self._verify(fixture)

            self.assertEqual(verification["status"], "failed")
            self.assertIn(
                "plan_target_derivation_invalid",
                {failure["code"] for failure in verification["failures"]},
            )

    def test_plan_binding_rejects_source_artifact_artifact_sha256_and_evidence_root_retarget(
        self,
    ) -> None:
        for field, replacement in (
            ("source_artifact", r"C:\totally\other-source\AgenticEvo.ScmProbe.exe"),
            ("artifact_sha256", "0" * 64),
            ("evidence_root", r"C:\totally\other-evidence-root"),
        ):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = self._create_fixture(Path(temporary))
                    plan_path = Path(fixture["evidence"]) / "plan.json"
                    result_path = Path(fixture["evidence"]) / "result.json"
                    plan = json.loads(plan_path.read_text(encoding="utf-8"))
                    plan[field] = replacement
                    plan_path.write_bytes(_canonical_json(plan))
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    result["report"]["plan_sha256"] = hashlib.sha256(
                        _canonical_json(plan)[:-1]
                    ).hexdigest()
                    result_path.write_bytes(_canonical_json(result))
                    fixture["result_sha256"] = _sha256(result_path)

                    verification = self._verify(fixture)

                    self.assertEqual(verification["status"], "failed")
                    self.assertIn(
                        "plan_target_derivation_invalid",
                        {failure["code"] for failure in verification["failures"]},
                    )

    def test_attack_harness_fails_closed_when_source_plan_is_semantically_invalid_even_if_repinned(
        self,
    ) -> None:
        from agentic_evo.windows_gate_b_evidence import (
            exercise_gate_b_evidence_attacks,
        )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_fixture(Path(temporary))
            plan_path = Path(fixture["evidence"]) / "plan.json"
            result_path = Path(fixture["evidence"]) / "result.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["artifact_root"] = r"C:\totally\unrelated-artifact-root"
            plan["artifact_path"] = (
                r"C:\totally\unrelated-artifact-root\AgenticEvo.ScmProbe.exe"
            )
            plan_path.write_bytes(_canonical_json(plan))

            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["report"]["plan_sha256"] = hashlib.sha256(
                _canonical_json(plan)[:-1]
            ).hexdigest()
            result_path.write_bytes(_canonical_json(result))
            fixture["result_sha256"] = _sha256(result_path)

            verification = self._verify(fixture)
            self.assertEqual(verification["status"], "failed")
            self.assertIn(
                "plan_target_derivation_invalid",
                {failure["code"] for failure in verification["failures"]},
            )

            attack = exercise_gate_b_evidence_attacks(
                Path(fixture["bundle"]),
                Path(fixture["evidence"]),
                GATE_B_SCRIPT,
                expected_manifest_sha256=str(fixture["manifest_sha256"]),
                expected_script_sha256=str(fixture["script_sha256"]),
                expected_result_sha256=str(fixture["result_sha256"]),
                expected_lab_id=str(fixture["lab_id"]),
                expected_run_id=str(fixture["run_id"]),
                expected_challenge=str(fixture["challenge"]),
            )

            self.assertEqual(attack["status"], "failed")
            self.assertEqual(attack["cases"], {})
            self.assertIn(
                "plan_target_derivation_invalid",
                {failure["code"] for failure in attack["preflight_failures"]},
            )

    def test_attack_harness_preflight_uses_same_external_bindings_as_verifier(
        self,
    ) -> None:
        from agentic_evo.windows_gate_b_evidence import (
            exercise_gate_b_evidence_attacks,
        )

        wrong_bindings = (
            ("expected_manifest_sha256", "0" * 64, "manifest_commitment_mismatch"),
            ("expected_script_sha256", "0" * 64, "script_commitment_mismatch"),
            ("expected_result_sha256", "0" * 64, "result_commitment_mismatch"),
            ("expected_lab_id", "other-lab", "lab_id_mismatch"),
            ("expected_run_id", "other-run", "run_id_mismatch"),
            ("expected_challenge", "other-challenge", "challenge_mismatch"),
        )
        for field, wrong_value, expected_code in wrong_bindings:
            with self.subTest(binding=field):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = self._create_fixture(Path(temporary))
                    verification = self._verify(
                        fixture,
                        **{field: wrong_value},
                    )
                    self.assertEqual(verification["status"], "failed")
                    self.assertIn(
                        expected_code,
                        {failure["code"] for failure in verification["failures"]},
                    )

                    expected = {
                        "expected_manifest_sha256": str(
                            fixture["manifest_sha256"]
                        ),
                        "expected_script_sha256": str(fixture["script_sha256"]),
                        "expected_result_sha256": str(fixture["result_sha256"]),
                        "expected_lab_id": str(fixture["lab_id"]),
                        "expected_run_id": str(fixture["run_id"]),
                        "expected_challenge": str(fixture["challenge"]),
                    }
                    expected[field] = wrong_value
                    attack = exercise_gate_b_evidence_attacks(
                        Path(fixture["bundle"]),
                        Path(fixture["evidence"]),
                        GATE_B_SCRIPT,
                        **expected,
                    )

                    self.assertEqual(attack["status"], "failed")
                    self.assertEqual(attack["cases"], {})
                    self.assertIn(
                        expected_code,
                        {
                            failure["code"]
                            for failure in attack["preflight_failures"]
                        },
                    )

    def test_plan_authorized_effects_and_claim_ceiling_must_match_contract(
        self,
    ) -> None:
        for mutate in ("authorized_effects", "claim_ceiling"):
            with self.subTest(field=mutate):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = self._create_fixture(Path(temporary))
                    plan_path = Path(fixture["evidence"]) / "plan.json"
                    result_path = Path(fixture["evidence"]) / "result.json"
                    plan = json.loads(plan_path.read_text(encoding="utf-8"))
                    if mutate == "authorized_effects":
                        plan["authorized_effects"]["genesis"] = True
                    else:
                        plan["claim_ceiling"]["gate_b"] = "passed"
                    plan_path.write_bytes(_canonical_json(plan))
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    result["report"]["plan_sha256"] = hashlib.sha256(
                        _canonical_json(plan)[:-1]
                    ).hexdigest()
                    result_path.write_bytes(_canonical_json(result))
                    fixture["result_sha256"] = _sha256(result_path)

                    verification = self._verify(fixture)

                    self.assertEqual(verification["status"], "failed")
                    self.assertIn(
                        "plan_contract_invalid",
                        {failure["code"] for failure in verification["failures"]},
                    )

    def test_nested_claim_overclaim_fails_closed_even_when_repinned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_fixture(Path(temporary))
            result_path = Path(fixture["evidence"]) / "result.json"
            forged = json.loads(result_path.read_text(encoding="utf-8"))
            forged["report"]["claims"]["gate_b_outcome"] = "passed"
            result_path.write_bytes(_canonical_json(forged))
            fixture["result_sha256"] = _sha256(result_path)

            verification = self._verify(fixture)

            self.assertEqual(verification["status"], "failed")
            self.assertIn(
                "receipt_claim_ceiling_exceeded",
                {failure["code"] for failure in verification["failures"]},
            )

    def test_report_claims_shape_drift_fails_closed_even_when_repinned(self) -> None:
        for mutation in ("extra", "missing"):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = self._create_fixture(Path(temporary))
                    result_path = Path(fixture["evidence"]) / "result.json"
                    forged = json.loads(result_path.read_text(encoding="utf-8"))
                    claims = forged["report"]["claims"]
                    if mutation == "extra":
                        claims["future_unverified_gain"] = "passed"
                    else:
                        del claims["all_attack_cases"]
                    result_path.write_bytes(_canonical_json(forged))
                    fixture["result_sha256"] = _sha256(result_path)

                    verification = self._verify(fixture)

                    self.assertEqual(verification["status"], "failed")
                    self.assertIn(
                        "receipt_claim_ceiling_exceeded",
                        {failure["code"] for failure in verification["failures"]},
                    )

    def test_trusted_system_directory_must_not_be_plan_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_fixture(Path(temporary))
            root = Path(temporary)
            plan_path = Path(fixture["evidence"]) / "plan.json"
            result_path = Path(fixture["evidence"]) / "result.json"
            copied_system_dir = root / "copied-system32"
            copied_system_dir.mkdir()
            shutil.copy2(
                Path(json.loads(plan_path.read_text(encoding="utf-8"))[
                    "trusted_system_directory"
                ]) / "sc.exe",
                copied_system_dir / "sc.exe",
            )
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["trusted_system_directory"] = str(copied_system_dir)
            plan_path.write_bytes(_canonical_json(plan))

            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["report"]["plan_sha256"] = hashlib.sha256(
                _canonical_json(plan)[:-1]
            ).hexdigest()
            result_path.write_bytes(_canonical_json(result))
            fixture["result_sha256"] = _sha256(result_path)

            verification = self._verify(fixture)

            self.assertEqual(verification["status"], "failed")
            self.assertIn(
                "trusted_scm_query_unavailable",
                {failure["code"] for failure in verification["failures"]},
            )

    def test_cleanup_holds_bundle_identity_across_verify_delete_boundary(
        self,
    ) -> None:
        import agentic_evo.windows_gate_a as gate_a

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            backup = root / "bundle-before-swap"
            external = root / "external-target"
            gate_a.prepare_gate_a_bundle(bundle)
            external.mkdir()
            shutil.copy2(bundle / ARTIFACT_NAME, external / ARTIFACT_NAME)
            shutil.copy2(bundle / MANIFEST_NAME, external / MANIFEST_NAME)
            sentinel = external / "sentinel.txt"
            sentinel.write_text("must survive", encoding="utf-8")
            external_before = {
                path.name: _sha256(path)
                for path in external.iterdir()
                if path.is_file()
            }
            original_verify = gate_a._verify_bundle_contents
            attack = {"blocked": False, "swapped": False}

            def swap_root_after_verify(
                path: Path,
                *,
                expected: dict[str, object] | None = None,
            ) -> dict[str, object]:
                verified = original_verify(path, expected=expected)
                try:
                    path.rename(backup)
                except OSError:
                    attack["blocked"] = True
                    return verified
                linked = subprocess.run(
                    [
                        "cmd.exe",
                        "/d",
                        "/c",
                        "mklink",
                        "/J",
                        str(path),
                        str(external),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(linked.returncode, 0, linked.stderr)
                attack["swapped"] = True
                return verified

            with mock.patch.object(
                gate_a,
                "_verify_bundle_contents",
                side_effect=swap_root_after_verify,
            ):
                result = gate_a.cleanup_gate_a_bundle(bundle)

            self.assertEqual(result, {"status": "local_artifacts_removed"})
            self.assertTrue(attack["blocked"])
            self.assertFalse(attack["swapped"])
            self.assertFalse(backup.exists())
            self.assertEqual(
                {
                    path.name: _sha256(path)
                    for path in external.iterdir()
                    if path.is_file()
                },
                external_before,
            )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "must survive")

    def test_real_attacker_uses_isolated_copies_and_fresh_verifier_processes(
        self,
    ) -> None:
        from agentic_evo.windows_gate_b_evidence import (
            exercise_gate_b_evidence_attacks,
        )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_fixture(Path(temporary))
            source_paths = [
                Path(fixture["bundle"]) / ARTIFACT_NAME,
                Path(fixture["bundle"]) / MANIFEST_NAME,
                Path(fixture["evidence"]) / "plan.json",
                Path(fixture["evidence"]) / "result.json",
                GATE_B_SCRIPT,
            ]
            before = {str(path): _sha256(path) for path in source_paths}

            report = exercise_gate_b_evidence_attacks(
                Path(fixture["bundle"]),
                Path(fixture["evidence"]),
                GATE_B_SCRIPT,
                expected_manifest_sha256=str(fixture["manifest_sha256"]),
                expected_script_sha256=str(fixture["script_sha256"]),
                expected_result_sha256=str(fixture["result_sha256"]),
                expected_lab_id=str(fixture["lab_id"]),
                expected_run_id=str(fixture["run_id"]),
                expected_challenge=str(fixture["challenge"]),
            )

            self.assertEqual(
                report["schema"],
                "agentic-evo.windows-gate-b-evidence-attack.v1",
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(
                set(report["cases"]),
                {
                    "A01_artifact_tamper",
                    "A03_coordinated_bundle_substitution",
                    "A04_forged_passed_receipt",
                    "A05_undeclared_bundle_entry",
                    "A07_cleanup_root_swap",
                },
            )
            self.assertTrue(all(case["status"] == "passed" for case in report["cases"].values()))
            for case_id, case in report["cases"].items():
                self.assertNotEqual(
                    case["attacker"]["pid"],
                    case["verifier"]["pid"],
                )
                self.assertEqual(case["attacker"]["exit_code"], 0)
                self.assertEqual(case["verifier"]["exit_code"], 0)
                self.assertTrue(case["attacker"]["isolated_copy_mutation"])
                self.assertTrue(case["attacker"]["action_observed"])
                self.assertEqual(
                    case["expected_failure_code"],
                    EXPECTED_ATTACK_FAILURE_CODES[case_id],
                )
                self.assertIn(
                    case["expected_failure_code"],
                    case["observed_failure_codes"],
                )
            cleanup_case = report["cases"]["A07_cleanup_root_swap"]
            self.assertEqual(cleanup_case["role"], "cleanup_defender")
            self.assertEqual(
                cleanup_case["trust_boundary"],
                "same_principal_harness",
            )
            self.assertNotIn("verifier_report", cleanup_case)
            self.assertFalse(any(report["effects"].values()))
            self.assertEqual(
                {str(path): _sha256(path) for path in source_paths},
                before,
            )

    def _a07_case(
        self,
        root: Path,
        *,
        cleanup_nonce: str = "a" * 32,
        worker_pid: int = 4242,
        nested_worker_pid: int | None = None,
        blocked: bool = True,
        swapped: bool = False,
    ) -> tuple[dict[str, object], str, int]:
        external = root / "external-target"
        external.mkdir()
        (external / ARTIFACT_NAME).write_bytes(b"fixture artifact")
        (external / MANIFEST_NAME).write_text("fixture manifest", encoding="utf-8")
        (external / "sentinel.txt").write_text("must survive", encoding="utf-8")
        observed_worker_pid = (
            worker_pid if nested_worker_pid is None else nested_worker_pid
        )
        attempt = {
            "schema": "agentic-evo.windows-gate-b-cleanup-attempt.v2",
            "case_id": "A07_cleanup_root_swap",
            "cleanup_nonce": cleanup_nonce,
            "worker_pid": observed_worker_pid,
            "phase": "after_bundle_verification_before_rename",
            "target": str(root / "bundle"),
            "backup": str(root / "bundle-before-swap"),
            "external_target": str(external),
            "external_sha256": {
                name: _sha256(external / name)
                for name in (ARTIFACT_NAME, MANIFEST_NAME, "sentinel.txt")
            },
            "blocked": blocked,
            "swapped": swapped,
        }
        report = {
            "worker_pid": worker_pid,
            "cleanup_nonce": cleanup_nonce,
            "cleanup_attempt": attempt,
            "isolated_copy_mutation": True,
            "action_observed": blocked and not swapped,
        }
        self.assertEqual(
            set(report),
            {
                "worker_pid",
                "cleanup_nonce",
                "cleanup_attempt",
                "isolated_copy_mutation",
                "action_observed",
            },
        )
        self.assertEqual(
            set(attempt),
            {
                "schema",
                "case_id",
                "cleanup_nonce",
                "worker_pid",
                "phase",
                "target",
                "backup",
                "external_target",
                "external_sha256",
                "blocked",
                "swapped",
            },
        )
        self.assertEqual(
            set(attempt["external_sha256"]),
            {ARTIFACT_NAME, MANIFEST_NAME, "sentinel.txt"},
        )
        return report, cleanup_nonce, worker_pid

    def test_cleanup_attack_verifier_rejects_forged_disk_marker_with_matching_poststate(
        self,
    ) -> None:
        from agentic_evo.windows_gate_b_evidence import _cleanup_attack_verifier

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attack, expected_cleanup_nonce, expected_worker_pid = self._a07_case(root)
            (root / "cleanup-attempt.json").write_bytes(
                _canonical_json(attack["cleanup_attempt"])
            )

            verification = _cleanup_attack_verifier(
                root,
                {},
                expected_cleanup_nonce=expected_cleanup_nonce,
                expected_worker_pid=expected_worker_pid,
            )

            self.assertIn("cleanup_attempt_not_observed", verification["failure_codes"])
            self.assertNotIn("cleanup_root_swap_blocked", verification["failure_codes"])

    def test_a07_pass_requires_parent_bound_worker_report_and_safe_poststate(
        self,
    ) -> None:
        from agentic_evo.windows_gate_b_evidence import _cleanup_attack_verifier

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attack, expected_cleanup_nonce, expected_worker_pid = self._a07_case(root)

            verification = _cleanup_attack_verifier(
                root,
                attack,
                expected_cleanup_nonce=expected_cleanup_nonce,
                expected_worker_pid=expected_worker_pid,
            )

            self.assertEqual(
                verification["failure_codes"],
                ["cleanup_root_swap_blocked"],
            )

    def test_a07_fails_when_worker_report_cleanup_nonce_mismatches(self) -> None:
        from agentic_evo.windows_gate_b_evidence import _cleanup_attack_verifier

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attack, _expected_cleanup_nonce, expected_worker_pid = self._a07_case(
                root,
                cleanup_nonce="b" * 32,
            )

            verification = _cleanup_attack_verifier(
                root,
                attack,
                expected_cleanup_nonce="a" * 32,
                expected_worker_pid=expected_worker_pid,
            )

            self.assertIn(
                "cleanup_attempt_binding_invalid",
                verification["failure_codes"],
            )

    def test_a07_fails_when_worker_report_expected_pid_mismatches(self) -> None:
        from agentic_evo.windows_gate_b_evidence import _cleanup_attack_verifier

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attack, expected_cleanup_nonce, expected_worker_pid = self._a07_case(
                root,
                worker_pid=4243,
            )

            verification = _cleanup_attack_verifier(
                root,
                attack,
                expected_cleanup_nonce=expected_cleanup_nonce,
                expected_worker_pid=expected_worker_pid - 1,
            )

            self.assertIn(
                "cleanup_attempt_binding_invalid",
                verification["failure_codes"],
            )

    def test_a07_fails_when_top_level_and_nested_worker_pid_mismatch(self) -> None:
        from agentic_evo.windows_gate_b_evidence import _cleanup_attack_verifier

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attack, expected_cleanup_nonce, expected_worker_pid = self._a07_case(
                root,
                nested_worker_pid=4243,
            )

            verification = _cleanup_attack_verifier(
                root,
                attack,
                expected_cleanup_nonce=expected_cleanup_nonce,
                expected_worker_pid=expected_worker_pid,
            )

            self.assertIn(
                "cleanup_attempt_binding_invalid",
                verification["failure_codes"],
            )

    def test_a07_fails_when_poststate_is_inconsistent(self) -> None:
        from agentic_evo.windows_gate_b_evidence import _cleanup_attack_verifier

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attack, expected_cleanup_nonce, expected_worker_pid = self._a07_case(root)
            (root / "bundle").mkdir()

            verification = _cleanup_attack_verifier(
                root,
                attack,
                expected_cleanup_nonce=expected_cleanup_nonce,
                expected_worker_pid=expected_worker_pid,
            )

            self.assertIn(
                "cleanup_poststate_inconsistent",
                verification["failure_codes"],
            )

    def test_a07_fails_when_worker_does_not_report_blocked_rename(self) -> None:
        from agentic_evo.windows_gate_b_evidence import _cleanup_attack_verifier

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attack, expected_cleanup_nonce, expected_worker_pid = self._a07_case(
                root,
                blocked=False,
            )

            verification = _cleanup_attack_verifier(
                root,
                attack,
                expected_cleanup_nonce=expected_cleanup_nonce,
                expected_worker_pid=expected_worker_pid,
            )

            self.assertIn("cleanup_attempt_not_observed", verification["failure_codes"])

    def test_a07_fails_when_worker_reports_swapped_or_no_report(self) -> None:
        from agentic_evo.windows_gate_b_evidence import _cleanup_attack_verifier

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attack, expected_cleanup_nonce, expected_worker_pid = self._a07_case(
                root,
                swapped=True,
            )

            for report in (attack, {}):
                with self.subTest(report=bool(report)):
                    verification = _cleanup_attack_verifier(
                        root,
                        report,
                        expected_cleanup_nonce=expected_cleanup_nonce,
                        expected_worker_pid=expected_worker_pid,
                    )

                    self.assertIn(
                        "cleanup_attempt_not_observed",
                        verification["failure_codes"],
                    )

    def test_cli_returns_nonzero_when_verifier_reports_failed_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_fixture(Path(temporary))
            result_path = Path(fixture["evidence"]) / "result.json"
            forged = json.loads(result_path.read_text(encoding="utf-8"))
            forged["report"]["claims"]["gate_b_outcome"] = "passed"
            result_path.write_bytes(_canonical_json(forged))
            fixture["result_sha256"] = _sha256(result_path)

            result = self._run_gate_b_cli(
                "verify-windows-gate-b-evidence",
                fixture,
            )

            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stderr)
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["result"]["status"],
                "failed",
            )

    def test_cli_verifier_and_attacker_refuse_failed_completed_receipt(
        self,
    ) -> None:
        """A child receipt's explicit failure cannot be treated as harness success."""

        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_fixture(Path(temporary))
            result_path = Path(fixture["evidence"]) / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["status"] = "failed"
            result_path.write_bytes(_canonical_json(result))
            fixture["result_sha256"] = _sha256(result_path)

            for command in (
                "verify-windows-gate-b-evidence",
                "attack-windows-gate-b-evidence",
            ):
                with self.subTest(command=command):
                    completed = self._run_gate_b_cli(command, fixture)

                    self.assertEqual(completed.returncode, 7, completed.stderr)
                    payload = json.loads(completed.stderr)
                    self.assertFalse(payload["ok"])
                    self.assertEqual(payload["result"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
