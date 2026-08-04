from __future__ import annotations

import base64
import hashlib
import io
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
EVIDENCE_CORPUS_PATH = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "windows_gate_b"
    / "corpus.manifest.json"
)
REFERENCE_LAB_GATE_B_NAMESPACE = (
    REPOSITORY_ROOT
    / "artifacts"
    / "labs"
    / "3060-computer"
    / "windows-gate-b"
)
TEST_LAB_ID = "windows-gate-b-test-fixture"
TEST_LAB_EVIDENCE_NAMESPACE = (
    REPOSITORY_ROOT
    / "artifacts"
    / "test-fixtures"
    / TEST_LAB_ID
)
TEST_LAB_GATE_B_NAMESPACE = TEST_LAB_EVIDENCE_NAMESPACE / "windows-gate-b"
HISTORICAL_V1_FROZEN_HASHES = {
    "bundle_manifest_sha256": (
        "a4c3e2f87830044312279e657d366640c8ce64628bd4436af142bfd40e7ceb04"
    ),
    "bundle_artifact_sha256": (
        "2fa4058e74a37ef4d3f378ad7607774dc7ac4de0bcc9f3a3cd3617dbb4e6623b"
    ),
    "evidence_plan_sha256": (
        "3ac6057c02df0e3674c3909fd3cbaf7445a7412b3a81abb101673b1bc5e18c8a"
    ),
    "evidence_result_sha256": (
        "13629892dc470e2dbb7ee73aa2219c28da25b38673b6a4e801b32e0229ee0e8a"
    ),
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


def _namespace_members(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {entry.name for entry in path.iterdir()}


def _cleanup_test_evidence_namespace(evidence: Path) -> None:
    """Remove one synthetic run leaf and only its empty owned parents."""

    # The plan owns one run-specific leaf below this exact namespace.  Refuse
    # cleanup for any other path, then stop as soon as an owned parent is not
    # empty (or cannot be removed); this cannot reach the reference namespace.
    if evidence.parent != TEST_LAB_GATE_B_NAMESPACE:
        return
    shutil.rmtree(evidence, ignore_errors=True)
    for parent in (TEST_LAB_GATE_B_NAMESPACE, TEST_LAB_EVIDENCE_NAMESPACE):
        try:
            parent.rmdir()
        except FileNotFoundError:
            continue
        except OSError:
            break


def _run_captured(
    command: list[str],
    *,
    timeout: int,
    **kwargs: object,
) -> subprocess.CompletedProcess[str]:
    """Run a Windows host command and capture text without losing output.

    Native Windows tools (sc.exe, PowerShell, cmd.exe) write console text in
    the active OEM/ANSI code page (GBK on this lab host), which is not valid
    UTF-8.  With bare ``text=True`` the subprocess reader thread dies on the
    first undecodable byte, so ``subprocess.run`` returns ``stdout=None`` /
    ``stderr=None`` and every downstream assertion fails with a TypeError.
    Decode as UTF-8 and replace undecodable host bytes: ASCII JSON and SIDs
    stay parseable, and only nonsemantic localized text is lossy.  This
    mirrors the project convention in ``windows_gate_a`` of decoding host
    output with ``errors="replace"``.
    """

    return subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **kwargs,
    )


# The CLI child runs the verifier/attacker in-process.  Its own text-mode
# subprocess calls (windows_gate_b_evidence/windows_gate_a) decode with the
# locale UTF-8 default, so GBK console text from sc.exe/cmd.exe kills the
# reader thread and dumps an "Exception in thread" trace onto the child's
# stderr, corrupting the JSON-on-stderr contract these tests assert.  Launch
# the child through this bootstrap so it applies the same utf-8 + replace
# policy as _run_captured; only text-mode calls are patched, so bytes-mode
# calls (e.g. windows_gate_a compiler probes) keep their byte semantics.
_CLI_SUBPROCESS_BOOTSTRAP = (
    "import subprocess as _sp\n"
    "_sp_run = _sp.run\n"
    "def _run(*args, **kwargs):\n"
    "    if (kwargs.get(\"text\") or kwargs.get(\"universal_newlines\")\n"
    "            or \"encoding\" in kwargs or \"errors\" in kwargs):\n"
    "        kwargs.setdefault(\"encoding\", \"utf-8\")\n"
    "        kwargs.setdefault(\"errors\", \"replace\")\n"
    "    return _sp_run(*args, **kwargs)\n"
    "_sp.run = _run\n"
    "import sys as _sys\n"
    "from agentic_evo.cli import main as _main\n"
    "_sys.exit(_main())\n"
)


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
    def test_synthetic_fixture_never_writes_reference_lab_namespace(self) -> None:
        reference_before = _namespace_members(REFERENCE_LAB_GATE_B_NAMESPACE)
        test_before = _namespace_members(TEST_LAB_GATE_B_NAMESPACE)
        self.addCleanup(
            lambda: self.assertEqual(
                _namespace_members(REFERENCE_LAB_GATE_B_NAMESPACE),
                reference_before,
            )
        )
        self.addCleanup(
            lambda: self.assertEqual(
                _namespace_members(TEST_LAB_GATE_B_NAMESPACE),
                test_before,
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._create_fixture(Path(directory))
            plan = json.loads(
                (Path(fixture["evidence"]) / "plan.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                _namespace_members(REFERENCE_LAB_GATE_B_NAMESPACE),
                reference_before,
            )
            self.assertEqual(plan["lab_id"], TEST_LAB_ID)
            self.assertEqual(
                plan["evidence_namespace"],
                "artifacts/test-fixtures/windows-gate-b-test-fixture",
            )
            self.assertEqual(
                Path(fixture["evidence"]).parent,
                TEST_LAB_GATE_B_NAMESPACE,
            )

    def _create_fixture(self, root: Path) -> dict[str, object]:
        from agentic_evo.windows_gate_a import prepare_gate_a_bundle

        bundle = root / "bundle"
        manifest = prepare_gate_a_bundle(bundle)
        manifest_path = bundle / MANIFEST_NAME
        artifact = bundle / ARTIFACT_NAME
        script_sha256 = _sha256(GATE_B_SCRIPT)
        lab_id = TEST_LAB_ID
        run_id = uuid4().hex
        challenge = uuid4().hex
        evidence = root / "evidence"
        plan_process = _run_captured(
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
                "-LabId",
                lab_id,
                "-ArtifactPath",
                str(artifact),
                "-ExpectedArtifactSha256",
                manifest["artifact"]["sha256"],
                "-EvidenceRoot",
                str(evidence),
            ],
            timeout=20,
        )
        self.assertEqual(plan_process.returncode, 0, plan_process.stderr)
        plan = json.loads(plan_process.stdout)
        evidence = Path(plan["evidence_root"])
        evidence.mkdir(parents=True)
        self.addCleanup(_cleanup_test_evidence_namespace, evidence)
        plan_bytes = _canonical_json(plan)
        (evidence / "plan.json").write_bytes(plan_bytes)
        plan_sha256 = hashlib.sha256(plan_bytes[:-1]).hexdigest()

        showsid = _run_captured(
            [
                str(Path(plan["trusted_system_directory"]) / "sc.exe"),
                "showsid",
                plan["service_name"],
            ],
            timeout=10,
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
            "schema": "agentic-evo.windows-gate-b-config-probe.v2",
            "lab_id": lab_id,
            "run_id": run_id,
            "challenge": challenge,
            "plan_sha256": plan_sha256,
            "script_sha256": script_sha256,
            "environment_sha256": plan["environment_sha256"],
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
            "schema": "agentic-evo.windows-gate-b-result.v2",
            "lab_id": lab_id,
            "run_id": run_id,
            "challenge": challenge,
            "status": "configuration_probe_completed",
            "gate_b_outcome": "not_established",
            "elevated_exit_code": 0,
            "elevated_pipe_client_pid": 1234,
            "plan_sha256": plan_sha256,
            "environment_sha256": plan["environment_sha256"],
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

    def _rewrite_result(
        self,
        fixture: dict[str, object],
        result: dict[str, object],
    ) -> None:
        result_path = Path(fixture["evidence"]) / "result.json"
        result_path.write_bytes(_canonical_json(result))
        fixture["result_sha256"] = _sha256(result_path)

    def _create_v2_synthetic_fixture(self, root: Path) -> dict[str, object]:
        return self._create_fixture(root)

    def _materialize_historical_v1_synthetic(
        self,
        member: dict[str, object],
        root: Path,
    ) -> dict[str, object]:
        self.assertEqual(member["label"], "historical_v1_synthetic")
        bundle_seed = member["bundle_seed"]
        evidence_seed = member["evidence_seed"]
        self.assertIsInstance(bundle_seed, dict)
        self.assertIsInstance(evidence_seed, dict)
        self.assertNotIn("lab_id", evidence_seed["plan_overrides"])
        self.assertNotIn("lab_id", evidence_seed["result_overrides"])
        self.assertNotIn("challenge", evidence_seed["result_overrides"])

        fixture = self._create_fixture(root)
        bundle = Path(fixture["bundle"])
        artifact = base64.b64decode(bundle_seed["artifact_bytes_b64"])
        artifact_file = str(bundle_seed["artifact_file"])
        self.assertEqual(artifact_file, ARTIFACT_NAME)
        (bundle / artifact_file).write_bytes(artifact)
        (bundle / MANIFEST_NAME).write_bytes(
            _canonical_json(bundle_seed["gate_a_manifest"])
        )

        plan_path = Path(fixture["evidence"]) / "plan.json"
        result_path = Path(fixture["evidence"]) / "result.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        plan.update(evidence_seed["plan_overrides"])
        for field in (
            "lab_id",
            "lab_declaration_path",
            "lab_declaration_sha256",
            "evidence_namespace",
            "environment",
            "environment_sha256",
        ):
            del plan[field]
        plan["run_id"] = fixture["run_id"]
        plan["artifact_sha256"] = bundle_seed["gate_a_manifest"]["artifact"]["sha256"]
        plan_path.write_bytes(_canonical_json(plan))
        plan_sha256 = hashlib.sha256(_canonical_json(plan)[:-1]).hexdigest()

        result.update(
            {
                field: value
                for field, value in evidence_seed["result_overrides"].items()
                if field != "report"
            }
        )
        result["run_id"] = fixture["run_id"]
        result.pop("lab_id", None)
        result.pop("challenge", None)
        result.pop("plan_sha256", None)
        result.pop("environment_sha256", None)
        report = result["report"]
        report.update(evidence_seed["result_overrides"]["report"])
        report["run_id"] = fixture["run_id"]
        report["challenge"] = fixture["challenge"]
        report.pop("environment_sha256", None)
        report["plan_sha256"] = plan_sha256
        report["script_sha256"] = _sha256(GATE_B_SCRIPT)
        report["observation"]["artifact_sha256"] = plan["artifact_sha256"]
        self._rewrite_result(fixture, result)
        fixture["manifest_sha256"] = _sha256(bundle / MANIFEST_NAME)
        return fixture

    def _load_evidence_corpus(self) -> dict[str, object]:
        return json.loads(EVIDENCE_CORPUS_PATH.read_text(encoding="utf-8"))

    def _materialize_evidence_corpus_member(
        self,
        member: dict[str, object],
        root: Path,
    ) -> dict[str, object]:
        if member["label"] == "historical_v1_synthetic":
            return self._materialize_historical_v1_synthetic(member, root)
        if member["label"] == "v2_synthetic":
            return self._create_v2_synthetic_fixture(root)
        self.fail(f"corpus member is not materializable: {member['label']!r}")

    def test_frozen_evidence_corpus_has_exact_membership_and_safe_metadata(
        self,
    ) -> None:
        corpus = self._load_evidence_corpus()

        self.assertEqual(set(corpus), {"schema", "members"})
        self.assertEqual(
            corpus["schema"],
            "agentic-evo.windows-gate-b-evidence-corpus.v1",
        )
        members = corpus["members"]
        self.assertIsInstance(members, list)
        self.assertEqual(
            [member["label"] for member in members],
            [
                "historical_v1_hash_provenance",
                "historical_v1_synthetic",
                "v2_synthetic",
            ],
        )
        provenance, historical, v2 = members
        self.assertEqual(
            set(provenance),
            {
                "label",
                "kind",
                "materializable",
                "raw_tracked",
                "compatibility_only",
                "lab_binding",
                "frozen_hashes",
            },
        )
        self.assertEqual(
            provenance,
            {
                "label": "historical_v1_hash_provenance",
                "kind": "hash_provenance",
                "materializable": False,
                "raw_tracked": False,
                "compatibility_only": True,
                "lab_binding": "inconclusive",
                "frozen_hashes": HISTORICAL_V1_FROZEN_HASHES,
            },
        )
        self.assertEqual(
            set(historical),
            {
                "label",
                "kind",
                "materializable",
                "raw_tracked",
                "compatibility_only",
                "lab_binding",
                "bundle_seed",
                "evidence_seed",
            },
        )
        self.assertEqual(
            {
                field: historical[field]
                for field in (
                    "label",
                    "kind",
                    "materializable",
                    "raw_tracked",
                    "compatibility_only",
                    "lab_binding",
                )
            },
            {
                "label": "historical_v1_synthetic",
                "kind": "synthetic_executable",
                "materializable": True,
                "raw_tracked": False,
                "compatibility_only": True,
                "lab_binding": "inconclusive",
            },
        )
        self.assertEqual(
            set(historical["bundle_seed"]),
            {"artifact_file", "artifact_bytes_b64", "gate_a_manifest"},
        )
        self.assertEqual(
            historical["bundle_seed"]["artifact_file"],
            ARTIFACT_NAME,
        )
        self.assertTrue(historical["bundle_seed"]["artifact_bytes_b64"])
        base64.b64decode(
            historical["bundle_seed"]["artifact_bytes_b64"],
            validate=True,
        )
        self.assertIsInstance(historical["bundle_seed"]["gate_a_manifest"], dict)
        self.assertEqual(
            set(historical["evidence_seed"]),
            {"plan_overrides", "result_overrides"},
        )
        self.assertIsInstance(historical["evidence_seed"]["plan_overrides"], dict)
        self.assertIsInstance(historical["evidence_seed"]["result_overrides"], dict)
        self.assertNotIn("lab_id", historical["evidence_seed"]["plan_overrides"])
        self.assertNotIn("lab_id", historical["evidence_seed"]["result_overrides"])
        self.assertNotIn("challenge", historical["evidence_seed"]["result_overrides"])
        self.assertNotIn("claims", historical["evidence_seed"]["result_overrides"])
        self.assertEqual(
            set(v2),
            {
                "label",
                "kind",
                "materializable",
                "raw_tracked",
                "compatibility_only",
                "lab_binding",
                "lab_id",
            },
        )
        self.assertEqual(
            v2,
            {
                "label": "v2_synthetic",
                "kind": "synthetic_executable",
                "materializable": True,
                "raw_tracked": False,
                "compatibility_only": False,
                "lab_binding": "bound",
                "lab_id": "3060-computer",
            },
        )

        serialized = json.dumps(corpus, ensure_ascii=False)
        self.assertNotIn(str(REPOSITORY_ROOT), serialized)
        self.assertNotIn(str(REPOSITORY_ROOT).replace("\\", "/"), serialized)
        self.assertNotIn("rawle", serialized.casefold())
        self.assertNotRegex(serialized, r"(?i)(?:[a-z]:[\\/]|(?:^|[\"'])/)")
        self.assertNotRegex(serialized, r"(?i)\b(?:username|hostname|serial|gpu)\b")
        self.assertNotRegex(serialized, r"(?i)\bS-\d+(?:-\d+)+\b")
        self.assertNotRegex(
            serialized,
            r"(?i)\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b",
        )
        self.assertEqual(
            {path.name for path in EVIDENCE_CORPUS_PATH.parent.iterdir()},
            {EVIDENCE_CORPUS_PATH.name},
        )

    def test_materializable_evidence_corpus_members_close_temp_bundles(
        self,
    ) -> None:
        corpus = self._load_evidence_corpus()
        materializable = [
            member for member in corpus["members"] if member["materializable"]
        ]
        self.assertEqual(
            [member["label"] for member in materializable],
            ["historical_v1_synthetic", "v2_synthetic"],
        )

        for member in materializable:
            with self.subTest(label=member["label"]), tempfile.TemporaryDirectory() as temporary:
                fixture = self._materialize_evidence_corpus_member(
                    member,
                    Path(temporary),
                )
                bundle = Path(fixture["bundle"])
                evidence = Path(fixture["evidence"])

                self.assertEqual(
                    {path.name for path in bundle.iterdir()},
                    {ARTIFACT_NAME, MANIFEST_NAME},
                )
                self.assertEqual(
                    {path.name for path in evidence.iterdir()},
                    {"plan.json", "result.json"},
                )
                self.assertTrue(all(path.is_file() for path in bundle.iterdir()))
                self.assertTrue(all(path.is_file() for path in evidence.iterdir()))

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
        return _run_captured(
            [
                sys.executable,
                "-P",
                "-c",
                _CLI_SUBPROCESS_BOOTSTRAP,
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
            timeout=15,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )

    def test_cli_verifies_historical_v1_synthetic_corpus_in_process(self) -> None:
        from agentic_evo import cli

        with tempfile.TemporaryDirectory() as temporary:
            historical = next(
                member
                for member in self._load_evidence_corpus()["members"]
                if member["label"] == "historical_v1_synthetic"
            )
            fixture = self._materialize_historical_v1_synthetic(
                historical,
                Path(temporary),
            )
            artifact = Path(fixture["bundle"]) / ARTIFACT_NAME
            original_run = subprocess.run

            def synthetic_artifact_runner(command: object, **kwargs: object) -> object:
                if isinstance(command, list) and command[:2] == [str(artifact), "console-probe"]:
                    return subprocess.CompletedProcess(command, 1063, "", "")
                return original_run(command, **kwargs)

            output = io.StringIO()
            with (
                mock.patch(
                    "agentic_evo.windows_gate_b_evidence.subprocess.run",
                    side_effect=synthetic_artifact_runner,
                ),
                mock.patch("sys.stdout", output),
            ):
                exit_code = cli.main(
                    [
                        "verify-windows-gate-b-evidence",
                        "--bundle-dir", str(fixture["bundle"]),
                        "--evidence-dir", str(fixture["evidence"]),
                        "--gate-b-script", str(GATE_B_SCRIPT),
                        "--expected-manifest-sha256", str(fixture["manifest_sha256"]),
                        "--expected-script-sha256", str(fixture["script_sha256"]),
                        "--expected-result-sha256", str(fixture["result_sha256"]),
                        "--lab-id", str(fixture["lab_id"]),
                        "--expected-run-id", str(fixture["run_id"]),
                        "--expected-challenge", str(fixture["challenge"]),
                    ]
                )

            payload = json.loads(output.getvalue())
            result = payload["result"]
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["ok"])
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["scope"], "bounded_evidence_and_current_zero_residue")
            self.assertEqual(result["historical_configuration"]["status"], "inconclusive")
            self.assertEqual(result["claims"]["gate_b_outcome"], "not_established")
            for case_id, case in result["case_matrix"].items():
                self.assertEqual(
                    case["status"],
                    "inconclusive" if case_id == "U01" else "not_run",
                )

    def test_cli_exercises_v2_synthetic_corpus_verify_and_attacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            v2 = next(
                member
                for member in self._load_evidence_corpus()["members"]
                if member["label"] == "v2_synthetic"
            )
            fixture = self._materialize_evidence_corpus_member(v2, Path(temporary))

            verification = self._run_gate_b_cli(
                "verify-windows-gate-b-evidence",
                fixture,
            )
            attack = self._run_gate_b_cli(
                "attack-windows-gate-b-evidence",
                fixture,
            )

            self.assertEqual(verification.returncode, 0, verification.stderr)
            verified_payload = json.loads(verification.stdout)
            self.assertTrue(verified_payload["ok"])
            verified = verified_payload["result"]
            self.assertEqual(verified["status"], "passed")
            self.assertEqual(verified["historical_configuration"]["status"], "inconclusive")
            self.assertEqual(verified["claims"]["gate_b_outcome"], "not_established")

            self.assertEqual(attack.returncode, 0, attack.stderr)
            attack_payload = json.loads(attack.stdout)
            self.assertTrue(attack_payload["ok"])
            attacked = attack_payload["result"]
            self.assertEqual(attacked["status"], "passed")
            self.assertEqual(set(attacked["cases"]), set(EXPECTED_ATTACK_FAILURE_CODES))
            for case_id, expected_code in EXPECTED_ATTACK_FAILURE_CODES.items():
                case = attacked["cases"][case_id]
                self.assertEqual(case["status"], "passed")
                self.assertEqual(case["expected_failure_code"], expected_code)
                self.assertIn(expected_code, case["observed_failure_codes"])
            self.assertEqual(
                attacked["cases"]["A07_cleanup_root_swap"]["role"],
                "cleanup_defender",
            )
            self.assertEqual(
                attacked["cases"]["A07_cleanup_root_swap"]["trust_boundary"],
                "same_principal_harness",
            )

    def test_cli_rejects_v2_corpus_bundle_with_undeclared_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            v2 = next(
                member
                for member in self._load_evidence_corpus()["members"]
                if member["label"] == "v2_synthetic"
            )
            fixture = self._materialize_evidence_corpus_member(v2, Path(temporary))
            (Path(fixture["bundle"]) / "extra.txt").write_text("extra", encoding="utf-8")

            completed = self._run_gate_b_cli(
                "verify-windows-gate-b-evidence",
                fixture,
            )

            self.assertEqual(completed.returncode, 7, completed.stderr)
            payload = json.loads(completed.stderr)
            self.assertFalse(payload["ok"])
            result = payload["result"]
            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "undeclared_bundle_entry",
                {failure["code"] for failure in result["failures"]},
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

    def test_verifier_accepts_historical_v1_synthetic_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            historical = next(
                member
                for member in self._load_evidence_corpus()["members"]
                if member["label"] == "historical_v1_synthetic"
            )
            fixture = self._materialize_historical_v1_synthetic(
                historical,
                Path(temporary),
            )
            artifact = Path(fixture["bundle"]) / ARTIFACT_NAME
            original_run = subprocess.run

            def synthetic_artifact_runner(command: object, **kwargs: object) -> object:
                if isinstance(command, list) and command[:2] == [str(artifact), "console-probe"]:
                    return subprocess.CompletedProcess(command, 1063, "", "")
                return original_run(command, **kwargs)

            with mock.patch(
                "agentic_evo.windows_gate_b_evidence.subprocess.run",
                side_effect=synthetic_artifact_runner,
            ):
                verification = self._verify(fixture)

            self.assertEqual(verification["status"], "passed")
            self.assertEqual(verification["lab_id"], fixture["lab_id"])
            self.assertEqual(verification["challenge"], fixture["challenge"])
            self.assertEqual(
                verification["historical_configuration"]["status"],
                "inconclusive",
            )

    def test_verifier_accepts_complete_v2_synthetic_receipt_without_claim_gain(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._create_v2_synthetic_fixture(Path(temporary))
            plan = json.loads(
                (Path(fixture["evidence"]) / "plan.json").read_text(encoding="utf-8")
            )

            verification = self._verify(fixture)

            self.assertEqual(verification["status"], "passed")
            self.assertEqual(plan["schema"], "agentic-evo.windows-gate-b-plan.v2")
            self.assertEqual(
                plan["authorized_effects"],
                {
                    "temporary_service": True,
                    "permanent_service": False,
                    "hook": False,
                    "genesis": False,
                    "system_restart": False,
                },
            )
            self.assertEqual(plan["claim_ceiling"]["gate_b"], "not_established")
            self.assertFalse(verification["claims"]["gate_a_complete"])
            self.assertEqual(verification["claims"]["gate_b_outcome"], "not_established")

    def test_verifier_rejects_mixed_v1_and_v2_receipt_schemas(self) -> None:
        for mix in (
            "plan_v2_result_v1",
            "plan_v1_result_v2",
            "result_v2_report_v1",
        ):
            with self.subTest(mix=mix), tempfile.TemporaryDirectory() as temporary:
                fixture = self._create_v2_synthetic_fixture(Path(temporary))
                plan_path = Path(fixture["evidence"]) / "plan.json"
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                result_path = Path(fixture["evidence"]) / "result.json"
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if mix == "plan_v2_result_v1":
                    result["schema"] = "agentic-evo.windows-gate-b-result.v1"
                    result["report"]["schema"] = (
                        "agentic-evo.windows-gate-b-config-probe.v1"
                    )
                elif mix == "plan_v1_result_v2":
                    plan["schema"] = "agentic-evo.windows-gate-b-plan.v1"
                    for field in (
                        "lab_id",
                        "lab_declaration_path",
                        "lab_declaration_sha256",
                        "evidence_namespace",
                        "environment",
                        "environment_sha256",
                    ):
                        del plan[field]
                    plan_path.write_bytes(_canonical_json(plan))
                else:
                    result["report"]["schema"] = (
                        "agentic-evo.windows-gate-b-config-probe.v1"
                    )
                self._rewrite_result(fixture, result)

                verification = self._verify(fixture)

                self.assertEqual(verification["status"], "failed")

    def test_v2_receipt_binds_lab_run_challenge_plan_and_environment(self) -> None:
        for binding in (
            "plan_lab_id",
            "result_run_id",
            "report_challenge",
            "report_plan_sha256",
            "result_environment_sha256",
        ):
            with self.subTest(binding=binding), tempfile.TemporaryDirectory() as temporary:
                fixture = self._create_v2_synthetic_fixture(Path(temporary))
                plan_path = Path(fixture["evidence"]) / "plan.json"
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                result_path = Path(fixture["evidence"]) / "result.json"
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if binding == "plan_lab_id":
                    plan["lab_id"] = "other-lab"
                    plan_path.write_bytes(_canonical_json(plan))
                    plan_sha256 = hashlib.sha256(_canonical_json(plan)[:-1]).hexdigest()
                    result["report"]["plan_sha256"] = plan_sha256
                    result["plan_sha256"] = plan_sha256
                elif binding == "result_run_id":
                    result["run_id"] = "other-run"
                elif binding == "report_challenge":
                    result["report"]["challenge"] = "other-challenge"
                elif binding == "report_plan_sha256":
                    result["report"]["plan_sha256"] = "0" * 64
                else:
                    result["environment_sha256"] = "0" * 64
                self._rewrite_result(fixture, result)

                verification = self._verify(fixture)

                self.assertEqual(verification["status"], "failed")

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
        for field, expected_code in (
            ("schema", "result_contract_invalid"),
            ("report.schema", "result_contract_invalid"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                fixture = self._create_fixture(Path(temporary))
                result_path = Path(fixture["evidence"]) / "result.json"
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
                linked = _run_captured(
                    [
                        "cmd.exe",
                        "/d",
                        "/c",
                        "mklink",
                        "/J",
                        str(path),
                        str(external),
                    ],
                    timeout=10,
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
            fixture = self._create_v2_synthetic_fixture(Path(temporary))
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
