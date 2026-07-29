from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


class InstallPlanTests(unittest.TestCase):
    def _environment(
        self,
        *,
        secret: str,
        fake_home: Path,
    ) -> dict[str, str]:
        environment = os.environ.copy()
        prior = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            str(SOURCE_ROOT)
            if not prior
            else os.pathsep.join((str(SOURCE_ROOT), prior))
        )
        environment["AGENTIC_EVO_TEST_SECRET"] = secret
        environment["AGENTIC_EVO_HOME"] = str(fake_home)
        return environment

    def _render(
        self,
        *,
        cwd: Path,
        secret: str,
        fake_home: Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.cli",
                "plan-install",
            ],
            cwd=cwd,
            env=self._environment(secret=secret, fake_home=fake_home),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_plan_is_canonical_deterministic_and_has_zero_side_effects(
        self,
    ) -> None:
        secret = "secret-that-must-never-enter-install-plan"
        with tempfile.TemporaryDirectory() as first_raw, tempfile.TemporaryDirectory() as second_raw:
            first = Path(first_raw)
            second = Path(second_raw)
            first_home = first / "must-not-be-created"
            second_home = second / "must-not-be-created"

            first_result = self._render(
                cwd=first,
                secret=secret,
                fake_home=first_home,
            )
            second_result = self._render(
                cwd=second,
                secret=secret,
                fake_home=second_home,
            )

            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            self.assertEqual(first_result.stderr, "")
            self.assertEqual(second_result.stderr, "")
            self.assertEqual(first_result.stdout, second_result.stdout)
            self.assertFalse(first_home.exists())
            self.assertFalse(second_home.exists())
            self.assertEqual(list(first.iterdir()), [])
            self.assertEqual(list(second.iterdir()), [])

        plan = json.loads(first_result.stdout)
        canonical = json.dumps(
            plan,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        self.assertEqual(first_result.stdout, canonical + "\n")
        self.assertNotIn(secret, first_result.stdout)
        self.assertNotIn(str(first), first_result.stdout)
        self.assertNotIn(str(second), first_result.stdout)
        self.assertNotIn(sys.executable, first_result.stdout)

    def test_plan_names_all_three_native_targets_without_claiming_installation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            result = self._render(
                cwd=directory,
                secret="not-in-output",
                fake_home=directory / "unused-home",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)

        self.assertEqual(
            set(plan),
            {
                "schema",
                "mode",
                "effects",
                "claims",
                "platforms",
                "codex_hook",
                "blockers",
            },
        )
        self.assertEqual(plan["schema"], "agentic-evo.install-dry-run.v1")
        self.assertEqual(plan["mode"], "plan_only")
        self.assertTrue(plan["effects"])
        self.assertTrue(all(value is False for value in plan["effects"].values()))
        self.assertEqual(
            plan["claims"],
            {
                "implemented_native_components": [
                    "win32_job_object_process_tree_fencing",
                    "win32_public_named_pipe_dacl_peer_authentication",
                    "win32_restricted_low_integrity_body_process_rehearsal",
                    "win32_explicit_inherited_private_lineage_transport_rehearsal",
                ],
                "native_security_verified": False,
                "portable_protocol_complete": True,
                "ready_to_install": False,
            },
        )

        platforms = plan["platforms"]
        self.assertEqual(set(platforms), {"win32", "darwin", "linux"})
        expected_fields = {
            "service_scope",
            "supervisor",
            "witness_principal",
            "body_principal",
            "trusted_state",
            "public_surface",
            "private_lineage",
            "worker_fencing",
            "native_test_status",
        }
        for target in platforms.values():
            self.assertEqual(set(target), expected_fields)
            self.assertEqual(target["service_scope"], "machine")
            self.assertEqual(target["native_test_status"], "not_run")

        self.assertEqual(platforms["win32"]["supervisor"], "windows_scm")
        self.assertIn(
            "reject_remote_clients",
            platforms["win32"]["public_surface"],
        )
        self.assertIn(
            "bound_host_sid",
            platforms["win32"]["public_surface"],
        )
        self.assertIn(
            "reject_remote_clients",
            platforms["win32"]["private_lineage"],
        )
        self.assertEqual(
            platforms["darwin"]["supervisor"],
            "launchd_launchdaemon",
        )
        self.assertEqual(
            platforms["darwin"]["witness_principal"],
            "dedicated_non_login_uid",
        )
        self.assertEqual(
            platforms["darwin"]["body_principal"],
            "dedicated_non_login_body_uid_with_signed_launcher",
        )
        self.assertEqual(
            platforms["darwin"]["public_surface"],
            "xpc_audit_token_bound_host_uid",
        )
        self.assertEqual(
            platforms["darwin"]["private_lineage"],
            "private_xpc_audit_token_body_uid_and_code_requirement",
        )
        self.assertNotEqual(
            platforms["darwin"]["witness_principal"],
            platforms["darwin"]["body_principal"],
        )
        self.assertEqual(
            platforms["darwin"]["worker_fencing"],
            "launchd_managed_body_job_required",
        )
        self.assertEqual(
            platforms["linux"]["supervisor"],
            "systemd_system_service",
        )
        self.assertEqual(
            platforms["linux"]["worker_fencing"],
            "systemd_managed_body_cgroup_required",
        )
        self.assertIn("pathname", platforms["linux"]["public_surface"])
        self.assertIn("so_peercred", platforms["linux"]["public_surface"])
        self.assertIn("bound_host_uid", platforms["linux"]["public_surface"])
        self.assertNotIn("abstract", platforms["linux"]["public_surface"])
        self.assertTrue(plan["blockers"])
        self.assertIn(
            "native_service_artifacts_and_reversible_uninstall",
            plan["blockers"],
        )
        self.assertIn("authenticated_host_presence", plan["blockers"])

    def test_hook_plan_is_observation_only_public_surface_and_fail_open(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            result = self._render(
                cwd=directory,
                secret="not-in-output",
                fake_home=directory / "unused-home",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        hook = json.loads(result.stdout)["codex_hook"]

        self.assertEqual(hook["scope"], "user")
        self.assertEqual(hook["status"], "not_installed")
        self.assertEqual(hook["runtime_access"], "public_surface_only")
        self.assertEqual(hook["failure_policy"], "fail_open")
        self.assertEqual(hook["provenance"], "surface_unverified")
        self.assertEqual(
            hook["event_mapping_status"],
            "planned_not_installed_and_not_integration_tested",
        )
        self.assertEqual(
            hook["event_mapping"],
            {
                "PermissionRequest": "observe",
                "PostCompact": "observe",
                "PostToolUse": "observe",
                "PreCompact": "observe",
                "PreToolUse": "observe",
                "SessionEnd": "sleep",
                "SessionStart": "wake",
                "Stop": "observe",
                "SubagentStart": "observe",
                "SubagentStop": "observe",
                "UserPromptSubmit": "observe",
            },
        )
        self.assertEqual(
            set(hook["forbidden_operations"]),
            {
                "advance_head",
                "genesis",
                "prepare_successor",
                "turn_off",
                "turn_on",
            },
        )


if __name__ == "__main__":
    unittest.main()
