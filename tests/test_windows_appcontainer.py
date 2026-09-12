from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch


class LpacCapabilityContractTests(unittest.TestCase):
    def test_capability_names_are_exactly_registry_read(self) -> None:
        import agentic_evo.windows_appcontainer as launcher

        for names in (
            (),
            ("registryRead", "registryRead"),
            ("internetClient",),
            ("registryRead", "internetClient"),
            ["registryRead"],
        ):
            with self.subTest(names=names):
                with self.assertRaisesRegex(ValueError, "exactly"):
                    launcher.capability_sids_for_names(names)  # type: ignore[arg-type]

    def test_capability_sid_helper_returns_canonical_derived_set(self) -> None:
        import agentic_evo.windows_appcontainer as launcher

        derived = MagicMock()
        derived.sid_string = "S-1-15-3-42"
        with patch.object(
            launcher,
            "_derive_capability_sids",
            return_value=(derived,),
        ):
            self.assertEqual(
                launcher.capability_sids_for_names(("registryRead",)),
                ("S-1-15-3-42",),
            )
        derived.close.assert_called_once_with()

    def test_token_capability_enumeration_is_canonical(self) -> None:
        import agentic_evo.windows_appcontainer as launcher

        entry_size = ctypes.sizeof(launcher._SidAndAttributes)
        buffer = ctypes.create_string_buffer(
            launcher._TokenGroups.Groups.offset + 2 * entry_size
        )
        groups = ctypes.cast(buffer, ctypes.POINTER(launcher._TokenGroups)).contents
        groups.GroupCount = 2
        entries = ctypes.cast(
            ctypes.c_void_p(
                ctypes.addressof(buffer) + launcher._TokenGroups.Groups.offset
            ),
            ctypes.POINTER(launcher._SidAndAttributes),
        )
        entries[0].Sid = 202
        entries[0].Attributes = launcher._SE_GROUP_ENABLED
        entries[1].Sid = 101
        entries[1].Attributes = launcher._SE_GROUP_ENABLED
        with (
            patch.object(launcher, "_token_information", return_value=buffer),
            patch.object(
                launcher,
                "_sid_to_string",
                side_effect=lambda sid: f"S-1-15-3-{sid}",
            ),
        ):
            self.assertEqual(
                launcher._token_capability_sids(ctypes.c_void_p()),
                ("S-1-15-3-101", "S-1-15-3-202"),
            )

    def test_token_capability_enumeration_rejects_disabled_capability(self) -> None:
        import agentic_evo.windows_appcontainer as launcher

        buffer = ctypes.create_string_buffer(
            launcher._TokenGroups.Groups.offset
            + ctypes.sizeof(launcher._SidAndAttributes)
        )
        groups = ctypes.cast(buffer, ctypes.POINTER(launcher._TokenGroups)).contents
        groups.GroupCount = 1
        entries = ctypes.cast(
            ctypes.c_void_p(
                ctypes.addressof(buffer) + launcher._TokenGroups.Groups.offset
            ),
            ctypes.POINTER(launcher._SidAndAttributes),
        )
        entries[0].Sid = 101
        entries[0].Attributes = 0
        with patch.object(launcher, "_token_information", return_value=buffer):
            with self.assertRaisesRegex(PermissionError, "not enabled"):
                launcher._token_capability_sids(ctypes.c_void_p())

    def test_token_contract_rejects_missing_duplicate_or_extra_capabilities(self) -> None:
        import agentic_evo.windows_appcontainer as launcher

        expected_appcontainer_sid = "S-1-15-2-123"
        expected_capability_sids = ("S-1-15-3-42",)
        launcher._verify_expected_lpac_token_profile(
            launcher.LpacTokenProfile(
                is_app_container=True,
                is_less_privileged_app_container=False,
                capability_count=1,
                capability_sids=expected_capability_sids,
                appcontainer_sid=expected_appcontainer_sid,
            ),
            expected_appcontainer_sid=expected_appcontainer_sid,
            expected_capability_sids=expected_capability_sids,
        )
        for capability_count, capability_sids in (
            (0, ()),
            (2, ("S-1-15-3-42", "S-1-15-3-42")),
            (2, ("S-1-15-3-42", "S-1-15-3-99")),
        ):
            profile = launcher.LpacTokenProfile(
                is_app_container=True,
                is_less_privileged_app_container=False,
                capability_count=capability_count,
                capability_sids=capability_sids,
                appcontainer_sid=expected_appcontainer_sid,
            )
            with self.subTest(capability_sids=capability_sids):
                with self.assertRaisesRegex(PermissionError, "exactly match"):
                    launcher._verify_expected_lpac_token_profile(
                        profile,
                        expected_appcontainer_sid=expected_appcontainer_sid,
                        expected_capability_sids=expected_capability_sids,
                    )


@unittest.skipUnless(sys.platform == "win32", "LPAC launcher is Windows-only")
class WindowsAppContainerTests(unittest.TestCase):
    def test_lpac_child_is_suspended_and_has_verified_token_contract(self) -> None:
        from agentic_evo.body_lpac import (
            create_ephemeral_lpac_profile,
            stage_lpac_body_payload,
        )
        from agentic_evo.windows_appcontainer import (
            capability_sids_for_names,
            spawn_lpac_suspended_process,
        )

        profile = create_ephemeral_lpac_profile()
        process = None
        stage = None
        read_fd, write_fd = os.pipe()
        try:
            stage = stage_lpac_body_payload(
                profile,
                staging_parent=_launcher_test_staging_parent(),
            )
            os.set_handle_inheritable(write_fd, True)
            process = spawn_lpac_suspended_process(
                (str(stage.python_executable), "-P", "-S", "-c", "pass"),
                inherited_handles=(write_fd,),
                cwd=stage.payload_root,
                environment=_child_environment(stage.scratch_path),
                appcontainer_sid=profile.sid,
                capability_names=("registryRead",),
            )

            self.assertTrue(process.token_profile.is_app_container)
            self.assertTrue(process.token_profile.is_less_privileged_app_container)
            self.assertEqual(process.token_profile.capability_count, 1)
            self.assertEqual(
                process.token_profile.capability_sids,
                capability_sids_for_names(("registryRead",)),
            )
            self.assertEqual(process.token_profile.appcontainer_sid, profile.sid_string)
            self.assertIsNone(process.poll())
            self.assertFalse(os.get_handle_inheritable(write_fd))

            pid = process.pid
            process.close()
            self.assertTrue(_wait_for_process_exit(pid, timeout_ms=5_000))
        finally:
            if process is not None:
                process.close()
            os.close(read_fd)
            os.close(write_fd)
            if stage is not None:
                stage.close()
            else:
                profile.close()

    def test_standard_appcontainer_fails_the_aap_semantic_canary(self) -> None:
        from agentic_evo.body_lpac import (
            create_ephemeral_lpac_profile,
            stage_lpac_body_payload,
        )
        import agentic_evo.windows_appcontainer as launcher

        profile = create_ephemeral_lpac_profile()
        stage = None
        read_fd, write_fd = os.pipe()
        try:
            stage = stage_lpac_body_payload(
                profile,
                staging_parent=_launcher_test_staging_parent(),
            )
            os.set_handle_inheritable(write_fd, True)
            native_update = launcher._update_process_attribute

            def without_aap_opt_out(
                attribute_list: object,
                attribute: int,
                value: object,
                size: int,
            ) -> None:
                if (
                    attribute
                    == launcher._PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY
                ):
                    return
                native_update(attribute_list, attribute, value, size)

            with patch.object(
                launcher,
                "_update_process_attribute",
                side_effect=without_aap_opt_out,
            ):
                with self.assertRaisesRegex(
                    PermissionError,
                    "ALL_APPLICATION_PACKAGES",
                ):
                    launcher.spawn_lpac_suspended_process(
                        (
                            str(stage.python_executable),
                            "-P",
                            "-S",
                            "-c",
                            "pass",
                        ),
                        inherited_handles=(write_fd,),
                        cwd=stage.payload_root,
                        environment=_child_environment(stage.scratch_path),
                        appcontainer_sid=profile.sid,
                        capability_names=("registryRead",),
                    )
            self.assertFalse(os.get_handle_inheritable(write_fd))
        finally:
            os.close(read_fd)
            os.close(write_fd)
            if stage is not None:
                stage.close()
            else:
                profile.close()


def _child_environment(scratch_path: Path) -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in ("SystemRoot", "WINDIR")
        if key in os.environ
    }
    environment["LOCALAPPDATA"] = str(scratch_path)
    return environment


def _launcher_test_staging_parent() -> Path:
    return Path(__file__).resolve().parents[1] / "artifacts" / "lpac-launcher-test-staging"


def _wait_for_process_exit(pid: int, *, timeout_ms: int) -> bool:
    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return True
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return kernel32.WaitForSingleObject(handle, timeout_ms) == 0
    finally:
        kernel32.CloseHandle(handle)
