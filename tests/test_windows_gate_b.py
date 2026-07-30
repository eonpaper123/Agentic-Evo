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

            result = self._run_plan(run_id, artifact, digest, evidence)

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            service_name = f"AgenticEvoGateB_{run_id}"
            self.assertEqual(plan["schema"], "agentic-evo.windows-gate-b-plan.v1")
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
            self.assertFalse(evidence.exists())
            self.assertFalse(Path(plan["artifact_root"]).exists())
            self.assertFalse(Path(plan["state_root"]).exists())

    def test_plan_rejects_non_random_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "AgenticEvo.ScmProbe.exe"
            artifact.write_bytes(b"MZgate-b-plan-test")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            evidence = root / "evidence"

            result = self._run_plan("not-random", artifact, digest, evidence)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(evidence.exists())

    def _run_plan(
        self,
        run_id: str,
        artifact: Path,
        digest: str,
        evidence: Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
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


if __name__ == "__main__":
    unittest.main()
