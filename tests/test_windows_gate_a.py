from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


EXPECTED_CASE_IDS = {
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


@unittest.skipUnless(sys.platform == "win32", "Windows Gate A contract")
class WindowsGateABundleTests(unittest.TestCase):
    def test_prepare_verify_and_cleanup_exact_no_uac_bundle(self) -> None:
        from agentic_evo.windows_gate_a import (
            cleanup_gate_a_bundle,
            prepare_gate_a_bundle,
            verify_gate_a_bundle,
        )

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            bundle = parent / "gate-a"

            report = prepare_gate_a_bundle(bundle)

            self.assertEqual(
                {path.name for path in bundle.iterdir()},
                {"AgenticEvo.ScmProbe.exe", "gate-a-manifest.json"},
            )
            manifest = json.loads(
                (bundle / "gate-a-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema"], "agentic-evo.windows-gate-a.v1")
            self.assertEqual(manifest["gate"], "A")
            self.assertEqual(manifest["status"], "partial")
            self.assertEqual(report, manifest)
            self.assertTrue(manifest["claims"]["scm_probe_bundle_ready"])
            self.assertFalse(manifest["claims"]["gate_a_complete"])
            for claim in (
                "privileged_installation_executed",
                "scm_observed",
                "service_token_observed",
                "state_acl_attacked",
                "native_security_verified",
                "ready_to_install",
            ):
                self.assertFalse(manifest["claims"][claim])
            self.assertEqual(
                manifest["effects"],
                {
                    "create_protected_state": False,
                    "install_hook": False,
                    "install_service": False,
                    "perform_genesis": False,
                    "request_elevation": False,
                    "start_service": False,
                },
            )

            artifact = manifest["artifact"]
            self.assertEqual(artifact["file"], "AgenticEvo.ScmProbe.exe")
            self.assertEqual(len(artifact["sha256"]), 64)
            self.assertEqual(
                artifact["bytes"],
                (bundle / artifact["file"]).stat().st_size,
            )
            self.assertEqual(artifact["console_probe"]["expected_exit_code"], 1063)
            self.assertEqual(
                artifact["runtime_closure"],
                ["protected_probe_artifact", "windows_system_dotnet_framework"],
            )
            self.assertNotIn("python", json.dumps(artifact).casefold())
            self.assertNotIn("checkout", json.dumps(artifact).casefold())

            target = manifest["protected_target_contract"]
            self.assertEqual(target["service_type"], "SERVICE_WIN32_OWN_PROCESS")
            self.assertEqual(target["account"], "NT AUTHORITY\\LocalService")
            self.assertEqual(target["service_sid_type"], "RESTRICTED")
            self.assertTrue(target["artifact_dacl"]["protected"])
            self.assertTrue(target["state_dacl"]["protected"])
            self.assertEqual(
                set(target["state_closure"]),
                {
                    "directory",
                    "database",
                    "key_material",
                    "sqlite_journal",
                    "sqlite_shm",
                    "sqlite_temp",
                    "sqlite_wal",
                },
            )

            harness = manifest["gate_b_case_matrix"]
            self.assertEqual(
                {case["id"] for case in harness},
                EXPECTED_CASE_IDS,
            )
            self.assertEqual(
                manifest["missing_gate_a_components"],
                [
                    "executable_gate_b_cleanup",
                    "independent_verifier",
                    "real_attacker",
                    "trusted_elevated_handoff",
                ],
            )
            self.assertEqual(
                manifest["cleanup"]["ordered_operations"],
                [
                    "stop_service",
                    "delete_service",
                    "wait_until_service_absent",
                    "delete_state_root",
                    "delete_artifact_root",
                    "assert_no_residue",
                ],
            )
            self.assertTrue(manifest["cleanup"]["random_namespace_required"])
            self.assertTrue(manifest["cleanup"]["manifest_targets_only"])
            self.assertTrue(manifest["cleanup"]["genesis_home_forbidden"])

            verified = verify_gate_a_bundle(bundle)
            self.assertEqual(verified, manifest)
            probe = subprocess.run(
                [str(bundle / artifact["file"]), "console-probe"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(probe.returncode, 1063, probe.stderr)
            self.assertEqual(probe.stdout, "")

            cleanup_gate_a_bundle(bundle)
            self.assertFalse(bundle.exists())
            self.assertTrue(parent.exists())
            self.assertEqual(
                cleanup_gate_a_bundle(bundle),
                {"status": "already_absent"},
            )

    def test_prepare_and_cleanup_refuse_ambiguous_targets(self) -> None:
        from agentic_evo.windows_gate_a import (
            GateABundleError,
            cleanup_gate_a_bundle,
            prepare_gate_a_bundle,
        )

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            occupied = parent / "occupied"
            occupied.mkdir()
            marker = occupied / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            with self.assertRaises(GateABundleError):
                prepare_gate_a_bundle(occupied)
            with self.assertRaises(GateABundleError):
                cleanup_gate_a_bundle(occupied)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_tamper_fails_closed_and_cleanup_preserves_external_files(self) -> None:
        from agentic_evo.windows_gate_a import (
            GateABundleError,
            cleanup_gate_a_bundle,
            prepare_gate_a_bundle,
            verify_gate_a_bundle,
        )

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            sentinel = parent / "outside.txt"
            sentinel.write_text("outside", encoding="utf-8")
            bundle = parent / "gate-a"
            prepare_gate_a_bundle(bundle)
            artifact = bundle / "AgenticEvo.ScmProbe.exe"
            original = artifact.read_bytes()
            artifact.write_bytes(original[:-1] + bytes([original[-1] ^ 0x01]))

            with self.assertRaises(GateABundleError):
                verify_gate_a_bundle(bundle)
            with self.assertRaises(GateABundleError):
                cleanup_gate_a_bundle(bundle)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")
            self.assertTrue(bundle.exists())

    def test_cleanup_rejects_a_junction_instead_of_following_its_target(self) -> None:
        from agentic_evo.windows_gate_a import (
            GateABundleError,
            cleanup_gate_a_bundle,
            prepare_gate_a_bundle,
            verify_gate_a_bundle,
        )

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            real_root = parent / "real-root"
            real_root.mkdir()
            bundle = real_root / "gate-a"
            junction = parent / "root-junction"
            prepare_gate_a_bundle(bundle)
            linked = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(junction),
                    str(real_root),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if linked.returncode != 0:
                self.skipTest(f"junction creation unavailable: {linked.stderr}")
            try:
                with self.assertRaises(GateABundleError):
                    cleanup_gate_a_bundle(junction / "gate-a")
                self.assertEqual(
                    verify_gate_a_bundle(bundle)["status"],
                    "partial",
                )
            finally:
                junction.rmdir()


class WindowsGateAPlatformTests(unittest.TestCase):
    def test_non_windows_host_is_rejected_before_output_creation(self) -> None:
        from agentic_evo.windows_gate_a import (
            GateABundleError,
            prepare_gate_a_bundle,
        )

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "gate-a"
            with mock.patch("agentic_evo.windows_gate_a.sys.platform", "linux"):
                with self.assertRaisesRegex(
                    GateABundleError,
                    "unsupported_host_platform",
                ):
                    prepare_gate_a_bundle(output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
