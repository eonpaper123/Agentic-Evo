from __future__ import annotations

import json
import os
import sys
import unittest

from agentic_evo.native_backends import (
    DARWIN_BACKEND,
    LINUX_BACKEND,
    NATIVE_BACKEND_FIELDS,
)


class NativeBackendTests(unittest.TestCase):
    def test_native_backend_field_list_matches_plan_contract_shape(self) -> None:
        self.assertEqual(
            NATIVE_BACKEND_FIELDS,
            (
                "service_scope",
                "supervisor",
                "witness_principal",
                "body_principal",
                "trusted_state",
                "public_surface",
                "private_lineage",
                "worker_fencing",
                "native_test_status",
            ),
        )

    def test_darwin_backend_matches_required_contract(self) -> None:
        self.assertEqual(
            DARWIN_BACKEND,
            {
                "service_scope": "machine",
                "supervisor": "launchd_launchdaemon",
                "witness_principal": "dedicated_non_login_uid",
                "body_principal": (
                    "dedicated_non_login_body_uid_with_signed_launcher"
                ),
                "trusted_state": "daemon_owned_mode_0700",
                "public_surface": "xpc_audit_token_bound_host_uid",
                "private_lineage": (
                    "private_xpc_audit_token_body_uid_and_code_requirement"
                ),
                "worker_fencing": "launchd_managed_body_job_required",
                "native_test_status": "not_run",
            },
        )
        self.assertNotEqual(
            DARWIN_BACKEND["witness_principal"],
            DARWIN_BACKEND["body_principal"],
        )

    def test_linux_backend_matches_required_contract(self) -> None:
        self.assertEqual(
            LINUX_BACKEND,
            {
                "service_scope": "machine",
                "supervisor": "systemd_system_service",
                "witness_principal": "dedicated_witness_system_uid",
                "body_principal": "dedicated_body_system_uid",
                "trusted_state": "systemd_state_directory",
                "public_surface": (
                    "pathname_af_unix_so_peercred_bound_host_uid"
                ),
                "private_lineage": (
                    "private_pathname_af_unix_so_peercred_body_uid"
                ),
                "worker_fencing": "systemd_managed_body_cgroup_required",
                "native_test_status": "not_run",
            },
        )
        self.assertNotEqual(
            LINUX_BACKEND["witness_principal"],
            LINUX_BACKEND["body_principal"],
        )
        self.assertIn("pathname", LINUX_BACKEND["public_surface"])
        self.assertIn("so_peercred", LINUX_BACKEND["public_surface"])
        self.assertIn("bound_host_uid", LINUX_BACKEND["public_surface"])
        self.assertNotIn("abstract", LINUX_BACKEND["public_surface"])

    def test_native_backend_constants_are_plan_only_and_secret_free(self) -> None:
        canonical = json.dumps(
            {"darwin": DARWIN_BACKEND, "linux": LINUX_BACKEND},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

        for value in (
            os.getcwd(),
            sys.executable,
            os.environ.get("AGENTIC_EVO_HOME", ""),
            os.environ.get("HOME", ""),
            os.environ.get("USERPROFILE", ""),
        ):
            if value:
                self.assertNotIn(value, canonical)

    def test_install_plan_uses_native_backend_single_source_for_darwin_and_linux(
        self,
    ) -> None:
        from agentic_evo.install_plan import build_install_plan

        platforms = build_install_plan()["platforms"]
        self.assertEqual(platforms["darwin"], DARWIN_BACKEND)
        self.assertEqual(platforms["linux"], LINUX_BACKEND)


if __name__ == "__main__":
    unittest.main()
