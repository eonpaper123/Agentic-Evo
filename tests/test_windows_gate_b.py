from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LAB_ID = "3060-computer"
LAB_DECLARATION_PATH = f"experiments/labs/{LAB_ID}.json"
PLAN_V2_KEYS = {
    "schema",
    "mode",
    "lab_id",
    "run_id",
    "service_name",
    "source_artifact",
    "artifact_sha256",
    "artifact_product_base",
    "artifact_root",
    "artifact_path",
    "state_product_base",
    "state_root",
    "probe_path",
    "evidence_root",
    "trusted_system_directory",
    "lab_declaration_path",
    "lab_declaration_sha256",
    "evidence_namespace",
    "environment",
    "environment_sha256",
    "authorized_effects",
    "claim_ceiling",
}
ENVIRONMENT_KEYS = {
    "os_family",
    "os_version",
    "os_architecture",
    "powershell_edition",
    "powershell_version",
    "trusted_system_directory",
}
AUTHORIZED_EFFECTS = {
    "genesis": False,
    "hook": False,
    "permanent_service": False,
    "system_restart": False,
    "temporary_service": True,
}
CLAIM_CEILING = {
    "gate_b": "not_established",
    "restricted_service_sid_configuration": "configuration_probe_only",
    "C01": "not_run",
    "C02": "not_run",
    "I01": "not_run",
    "S01": "not_run",
    "S02": "not_run",
    "S03": "not_run",
    "P01": "not_run",
    "P02": "not_run",
    "L01": "not_run",
    "R01": "not_run",
    "R02": "not_run",
    "U01": "partial_cleanup_if_service_lifecycle_occurs",
    "native_security_verified": False,
    "ready_to_install": False,
}


@unittest.skipUnless(sys.platform == "win32", "Windows Gate B plan contract")
class WindowsGateBPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "windows-gate-b-experiment.ps1"
        )

    def test_plan_is_exact_and_has_no_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "AgenticEvo.ScmProbe.exe"
            artifact.write_bytes(b"MZgate-b-plan-test")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            evidence = root / "evidence"
            run_id = uuid4().hex

            result = self._run_plan(
                run_id,
                artifact,
                digest,
                evidence,
                lab_id=LAB_ID,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            service_name = f"AgenticEvoGateB_{run_id}"
            self.assertEqual(plan["schema"], "agentic-evo.windows-gate-b-plan.v2")
            self.assertEqual(plan["mode"], "plan")
            self.assertEqual(plan["run_id"], run_id)
            self.assertEqual(plan["service_name"], service_name)
            self.assertEqual(plan["artifact_sha256"], digest)
            self.assertEqual(
                Path(plan["artifact_root"]),
                Path(os.environ["ProgramFiles"])
                / "Agentic-Evo"
                / "GateB"
                / run_id,
            )
            self.assertEqual(
                Path(plan["state_root"]),
                Path(os.environ["ProgramData"])
                / "Agentic-Evo"
                / "GateB"
                / run_id,
            )
            self.assertEqual(
                plan["authorized_effects"],
                {
                    "genesis": False,
                    "hook": False,
                    "permanent_service": False,
                    "system_restart": False,
                    "temporary_service": True,
                },
            )
            self.assertEqual(plan["claim_ceiling"]["gate_b"], "not_established")
            self.assertEqual(
                plan["claim_ceiling"]["restricted_service_sid_configuration"],
                "configuration_probe_only",
            )
            self.assertEqual(plan["claim_ceiling"]["C01"], "not_run")
            self.assertEqual(
                plan["claim_ceiling"]["U01"],
                "partial_cleanup_if_service_lifecycle_occurs",
            )
            self.assertFalse(evidence.exists())
            self.assertFalse(Path(plan["artifact_root"]).exists())
            self.assertFalse(Path(plan["state_root"]).exists())

    def test_plan_requires_lab_id_for_v2_producer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "AgenticEvo.ScmProbe.exe"
            artifact.write_bytes(b"MZgate-b-plan-test")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            evidence = root / "caller-evidence"

            result = self._run_plan(uuid4().hex, artifact, digest, evidence)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(evidence.exists())

    def test_lab_bound_plan_is_exact_v2_and_has_no_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "AgenticEvo.ScmProbe.exe"
            artifact.write_bytes(b"MZgate-b-plan-test")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            caller_evidence = root / "caller-evidence"
            run_id = uuid4().hex
            declaration_file = REPOSITORY_ROOT / Path(LAB_DECLARATION_PATH)
            declaration = json.loads(declaration_file.read_text(encoding="utf-8"))

            result = self._run_plan(
                run_id,
                artifact,
                digest,
                caller_evidence,
                lab_id=LAB_ID,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(set(plan), PLAN_V2_KEYS)
            self.assertEqual(plan["schema"], "agentic-evo.windows-gate-b-plan.v2")
            self.assertEqual(plan["mode"], "plan")
            self.assertEqual(plan["lab_id"], declaration["lab_id"])
            self.assertEqual(plan["lab_declaration_path"], str(LAB_DECLARATION_PATH))
            self.assertEqual(
                plan["lab_declaration_sha256"],
                hashlib.sha256(declaration_file.read_bytes()).hexdigest(),
            )
            self.assertEqual(plan["evidence_namespace"], declaration["evidence_namespace"])
            self.assertEqual(
                Path(plan["evidence_root"]),
                REPOSITORY_ROOT.resolve()
                / declaration["evidence_namespace"]
                / "windows-gate-b"
                / run_id,
            )
            self.assertNotEqual(Path(plan["evidence_root"]), caller_evidence)
            self.assertEqual(set(plan["environment"]), ENVIRONMENT_KEYS)
            environment_json = json.dumps(
                plan["environment"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self.assertEqual(
                plan["environment_sha256"],
                hashlib.sha256(environment_json.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                plan["environment"]["trusted_system_directory"],
                plan["trusted_system_directory"],
            )
            self.assertEqual(plan["authorized_effects"], AUTHORIZED_EFFECTS)
            self.assertEqual(plan["claim_ceiling"], CLAIM_CEILING)
            self.assertFalse(caller_evidence.exists())
            self.assertFalse(Path(plan["evidence_root"]).exists())
            self.assertFalse(Path(plan["artifact_root"]).exists())
            self.assertFalse(Path(plan["state_root"]).exists())
            service = subprocess.run(
                [
                    str(Path(plan["trusted_system_directory"]) / "sc.exe"),
                    "query",
                    plan["service_name"],
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(service.returncode, 1060, service.stderr)
            self._assert_environment_excludes_pii(plan["environment"])

    def test_retained_preflight_is_exact_and_has_no_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "AgenticEvo.ScmProbe.exe"
            artifact.write_bytes(b"MZgate-b-retained-preflight-test")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            caller_evidence = root / "caller-evidence"
            run_id = uuid4().hex
            declaration_file = REPOSITORY_ROOT / Path(LAB_DECLARATION_PATH)
            declaration = json.loads(declaration_file.read_text(encoding="utf-8"))
            service_name = f"AgenticEvoGateB_{run_id}"
            artifact_root = (
                Path(os.environ["ProgramFiles"])
                / "Agentic-Evo"
                / "GateB"
                / run_id
            )
            state_root = (
                Path(os.environ["ProgramData"])
                / "Agentic-Evo"
                / "GateB"
                / run_id
            )
            evidence_root = (
                REPOSITORY_ROOT.resolve()
                / declaration["evidence_namespace"]
                / "windows-gate-b"
                / run_id
            )
            sc_exe = Path(os.environ["SystemRoot"]) / "System32" / "sc.exe"

            self.assertFalse(artifact_root.exists())
            self.assertFalse(state_root.exists())
            self.assertFalse(evidence_root.exists())
            service = subprocess.run(
                [str(sc_exe), "query", service_name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(service.returncode, 1060, service.stderr)

            result = self._run_plan(
                run_id,
                artifact,
                digest,
                caller_evidence,
                lab_id=LAB_ID,
                mode="RetainedPreflight",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            repeated = self._run_plan(
                run_id,
                artifact,
                digest,
                caller_evidence,
                lab_id=LAB_ID,
                mode="RetainedPreflight",
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(repeated.stdout, result.stdout)
            preflight = json.loads(result.stdout)
            self.assertEqual(
                list(preflight),
                [
                    "schema",
                    "mode",
                    "lab_id",
                    "run_id",
                    "service_name",
                    "source_artifact",
                    "artifact_sha256",
                    "artifact_product_base",
                    "artifact_root",
                    "artifact_path",
                    "state_product_base",
                    "state_root",
                    "probe_path",
                    "evidence_root",
                    "trusted_system_directory",
                    "lab_declaration_path",
                    "lab_declaration_sha256",
                    "evidence_namespace",
                    "environment",
                    "environment_sha256",
                    "authorized_effects",
                    "retention",
                    "claim_ceiling",
                ],
            )
            self.assertEqual(
                preflight["schema"],
                "agentic-evo.windows-gate-b-retained-preflight.v1",
            )
            self.assertEqual(preflight["mode"], "retained_preflight")
            self.assertEqual(preflight["lab_id"], declaration["lab_id"])
            self.assertEqual(preflight["run_id"], run_id)
            self.assertEqual(preflight["service_name"], service_name)
            self.assertEqual(Path(preflight["source_artifact"]), artifact.resolve())
            self.assertEqual(preflight["artifact_sha256"], digest)
            self.assertEqual(
                Path(preflight["artifact_product_base"]),
                Path(os.environ["ProgramFiles"]) / "Agentic-Evo",
            )
            self.assertEqual(
                Path(preflight["artifact_root"]),
                artifact_root,
            )
            self.assertEqual(
                Path(preflight["artifact_path"]),
                Path(preflight["artifact_root"]) / "AgenticEvo.ScmProbe.exe",
            )
            self.assertEqual(
                Path(preflight["state_product_base"]),
                Path(os.environ["ProgramData"]) / "Agentic-Evo",
            )
            self.assertEqual(
                Path(preflight["state_root"]),
                state_root,
            )
            self.assertEqual(
                Path(preflight["probe_path"]),
                Path(preflight["state_root"]) / "scm-write.probe",
            )
            self.assertEqual(
                preflight["lab_declaration_path"], str(LAB_DECLARATION_PATH)
            )
            self.assertEqual(
                preflight["lab_declaration_sha256"],
                hashlib.sha256(declaration_file.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                preflight["evidence_namespace"], declaration["evidence_namespace"]
            )
            self.assertEqual(
                Path(preflight["evidence_root"]),
                evidence_root,
            )
            self.assertNotEqual(Path(preflight["evidence_root"]), caller_evidence)
            self.assertEqual(
                preflight["trusted_system_directory"],
                preflight["environment"]["trusted_system_directory"],
            )
            self.assertEqual(set(preflight["environment"]), ENVIRONMENT_KEYS)
            self.assertEqual(
                list(preflight["environment"]),
                [
                    "os_family",
                    "os_version",
                    "os_architecture",
                    "powershell_edition",
                    "powershell_version",
                    "trusted_system_directory",
                ],
            )
            environment_json = json.dumps(
                preflight["environment"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self.assertEqual(
                preflight["environment_sha256"],
                hashlib.sha256(environment_json.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                list(preflight["authorized_effects"]),
                [
                    "temporary_service",
                    "permanent_service",
                    "hook",
                    "genesis",
                    "system_restart",
                ],
            )
            self.assertEqual(
                list(preflight["retention"]),
                [
                    "service_lifetime",
                    "service_start",
                    "cleanup_before_restart",
                    "cleanup_after_post_restart_verification",
                    "reversible_uninstall_required",
                ],
            )
            self.assertEqual(
                list(preflight["claim_ceiling"]),
                [
                    "gate_b",
                    "restricted_service_sid_configuration",
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
                    "native_security_verified",
                    "ready_to_install",
                ],
            )
            self.assertEqual(preflight["claim_ceiling"], CLAIM_CEILING)
            self.assertFalse(caller_evidence.exists())
            self.assertFalse(artifact_root.exists())
            self.assertFalse(state_root.exists())
            self.assertFalse(evidence_root.exists())
            service = subprocess.run(
                [str(sc_exe), "query", service_name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(service.returncode, 1060, service.stderr)
            self.assertEqual(
                {path.relative_to(root) for path in root.rglob("*") if path.is_file()},
                {artifact.relative_to(root)},
            )
            self._assert_environment_excludes_pii(preflight["environment"])

    def test_plan_rejects_invalid_run_id_format(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "AgenticEvo.ScmProbe.exe"
            artifact.write_bytes(b"MZgate-b-plan-test")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            evidence = root / "evidence"

            result = self._run_plan(
                "not-random",
                artifact,
                digest,
                evidence,
                lab_id=LAB_ID,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(evidence.exists())

    def test_run_requires_externally_pinned_script_before_any_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "AgenticEvo.ScmProbe.exe"
            artifact.write_bytes(b"MZgate-b-plan-test")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            evidence = root / "evidence"
            run_id = uuid4().hex

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self.script),
                    "-Mode",
                    "Run",
                    "-RunId",
                    run_id,
                    "-LabId",
                    LAB_ID,
                    "-ArtifactPath",
                    str(artifact),
                    "-ExpectedArtifactSha256",
                    digest,
                    "-EvidenceRoot",
                    str(evidence),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("run_requires_externally_pinned_script", result.stderr)
            self.assertFalse(evidence.exists())

    def test_plan_uses_os_system_directory_not_spoofed_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "AgenticEvo.ScmProbe.exe"
            artifact.write_bytes(b"MZgate-b-plan-test")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            evidence = root / "evidence"
            fake_windows = root / "fake-windows"
            run_id = uuid4().hex
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            command = (
                f"$env:SystemRoot={quote(fake_windows)}; "
                f"& {quote(self.script)} -Mode Plan -RunId {quote(run_id)} "
                f"-LabId {quote(LAB_ID)} "
                f"-ArtifactPath {quote(artifact)} "
                f"-ExpectedArtifactSha256 {quote(digest)} "
                f"-EvidenceRoot {quote(evidence)}"
            )

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(
                Path(plan["trusted_system_directory"]),
                Path(os.environ["SystemRoot"]) / "System32",
            )
            self.assertNotEqual(
                Path(plan["trusted_system_directory"]),
                fake_windows / "System32",
            )
            self.assertFalse(evidence.exists())

    def test_plan_ignores_user_supplied_module_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "AgenticEvo.ScmProbe.exe"
            artifact.write_bytes(b"MZgate-b-plan-test")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            evidence = root / "evidence"
            fake_modules = root / "fake-modules"
            fake_modules.mkdir()
            run_id = uuid4().hex
            environment = os.environ.copy()
            environment["PSModulePath"] = str(fake_modules)

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self.script),
                    "-Mode",
                    "Plan",
                    "-RunId",
                    run_id,
                    "-LabId",
                    LAB_ID,
                    "-ArtifactPath",
                    str(artifact),
                    "-ExpectedArtifactSha256",
                    digest,
                    "-EvidenceRoot",
                    str(evidence),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["run_id"], run_id)
            self.assertFalse(evidence.exists())

    def _run_plan(
        self,
        run_id: str,
        artifact: Path,
        digest: str,
        evidence: Path,
        *,
        lab_id: str | None = None,
        mode: str = "Plan",
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script),
            "-Mode",
            mode,
            "-RunId",
            run_id,
            "-ArtifactPath",
            str(artifact),
            "-ExpectedArtifactSha256",
            digest,
            "-EvidenceRoot",
            str(evidence),
        ]
        if lab_id is not None:
            command.extend(("-LabId", lab_id))
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def _assert_environment_excludes_pii(self, environment: object) -> None:
        serialized = json.dumps(environment, ensure_ascii=False).casefold()
        for identifier in (
            "hostname",
            "username",
            "sid",
            "serial",
            "mac",
            "gpu",
            str(REPOSITORY_ROOT).casefold(),
        ):
            self.assertNotIn(identifier, serialized)


if __name__ == "__main__":
    unittest.main()
