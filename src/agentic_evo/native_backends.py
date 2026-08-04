from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping


NATIVE_BACKEND_FIELDS: Final[tuple[str, ...]] = (
    "service_scope",
    "supervisor",
    "witness_principal",
    "body_principal",
    "trusted_state",
    "public_surface",
    "private_lineage",
    "worker_fencing",
    "native_test_status",
)

DARWIN_BACKEND: Final[Mapping[str, str]] = MappingProxyType({
    "service_scope": "machine",
    "supervisor": "launchd_launchdaemon",
    "witness_principal": "dedicated_non_login_uid",
    "body_principal": "dedicated_non_login_body_uid_with_signed_launcher",
    "trusted_state": "daemon_owned_mode_0700",
    "public_surface": "xpc_audit_token_bound_host_uid",
    "private_lineage": "private_xpc_audit_token_body_uid_and_code_requirement",
    "worker_fencing": "launchd_managed_body_job_required",
    "native_test_status": "not_run",
})

LINUX_BACKEND: Final[Mapping[str, str]] = MappingProxyType({
    "service_scope": "machine",
    "supervisor": "systemd_system_service",
    "witness_principal": "dedicated_witness_system_uid",
    "body_principal": "dedicated_body_system_uid",
    "trusted_state": "systemd_state_directory",
    "public_surface": "pathname_af_unix_so_peercred_bound_host_uid",
    "private_lineage": "private_pathname_af_unix_so_peercred_body_uid",
    "worker_fencing": "systemd_managed_body_cgroup_required",
    "native_test_status": "not_run",
})
