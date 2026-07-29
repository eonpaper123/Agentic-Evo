from __future__ import annotations

from typing import Any


def build_install_plan() -> dict[str, Any]:
    """Return a deterministic target contract that performs no installation writes."""

    return {
        "schema": "agentic-evo.install-dry-run.v1",
        "mode": "plan_only",
        "effects": {
            "create_state": False,
            "install_hook": False,
            "install_service": False,
            "perform_genesis": False,
            "start_process": False,
        },
        "claims": {
            "implemented_native_components": [
                "win32_job_object_process_tree_fencing"
            ],
            "native_security_verified": False,
            "portable_protocol_complete": True,
            "ready_to_install": False,
        },
        "platforms": {
            "win32": {
                "service_scope": "machine",
                "supervisor": "windows_scm",
                "witness_principal": "restricted_service_sid",
                "body_principal": "restricted_token_distinct_from_witness",
                "trusted_state": "programdata_service_sid_acl",
                "public_surface": (
                    "named_pipe_explicit_dacl_bound_host_sid_"
                    "reject_remote_clients"
                ),
                "private_lineage": (
                    "named_pipe_body_sid_and_capability_reject_remote_clients"
                ),
                "worker_fencing": "job_object",
                "native_test_status": "not_run",
            },
            "darwin": {
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
            "linux": {
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
        },
        "codex_hook": {
            "scope": "user",
            "status": "not_installed",
            "runtime_access": "public_surface_only",
            "failure_policy": "fail_open",
            "provenance": "surface_unverified",
            "event_mapping_status": (
                "planned_not_installed_and_not_integration_tested"
            ),
            "event_mapping": {
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
            "forbidden_operations": [
                "advance_head",
                "genesis",
                "prepare_successor",
                "turn_off",
                "turn_on",
            ],
        },
        "blockers": [
            "authenticated_host_presence",
            "formal_genesis_authorization",
            "native_witness_and_body_principals",
            "service_owned_trusted_state",
            "peer_authenticated_public_and_private_ipc",
            "native_process_tree_fencing",
            "native_service_artifacts_and_reversible_uninstall",
            "target_platform_integration_tests",
        ],
    }
