from __future__ import annotations

"""LPAC profile and private Body payload staging.

This module owns only the per-Body AppContainer profile and the files granted
to that profile.  It does not launch a process or decide whether executable
Body development is permitted; the caller must use the LPAC launcher and the
Witness/runtime gate separately.
"""

from collections.abc import Iterable
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
import shutil
import sys
import threading
from typing import Final
from uuid import uuid4


BODY_LPAC_WORKER_MODULES: Final[tuple[str, ...]] = (
    "_util.py",
    "body.py",
    "body_process.py",
    "body_worker.py",
    "development_entrypoint.py",
    "development_executor.py",
    "errors.py",
    "organ_broker.py",
    "windows_native.py",
)
BODY_LPAC_CAPABILITY_NAMES: Final[tuple[str, ...]] = ("registryRead",)
_EXCLUDED_PYTHON_LIBRARY_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {"site-packages", "ensurepip", "__pycache__"}
)
_EXCLUDED_PYTHON_LIBRARY_FILES: Final[frozenset[str]] = frozenset(
    {"sitecustomize.py", "usercustomize.py"}
)

_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_ERROR_INSUFFICIENT_BUFFER = 122
_SDDL_REVISION_1 = 1
_S_OK = 0
_PAYLOAD_ACCESS_MASK = 0x001200A9
_SCRATCH_ACCESS_MASK = 0x001301BF


class LpacStagingError(RuntimeError):
    """A visible failure while creating or cleaning an LPAC Body boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [
        ("Sid", ctypes.c_void_p),
        ("Attributes", wintypes.DWORD),
    ]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


if sys.platform == "win32":
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _userenv = ctypes.WinDLL("userenv", use_last_error=True)

    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    _advapi32.FreeSid.argtypes = [ctypes.c_void_p]
    _advapi32.FreeSid.restype = ctypes.c_void_p
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.CreateDirectoryW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(_SecurityAttributes),
    ]
    _kernel32.CreateDirectoryW.restype = wintypes.BOOL
    _userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _userenv.CreateAppContainerProfile.restype = ctypes.c_long
    _userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    _userenv.DeleteAppContainerProfile.restype = ctypes.c_long


def is_lpac_body_runtime_available(
    *,
    python_executable: Path | None = None,
) -> bool:
    """Check LPAC launch prerequisites without creating any OS state."""

    if sys.platform != "win32":
        return False
    try:
        from .windows_appcontainer import spawn_lpac_suspended_process
    except ImportError:
        return False
    if not callable(spawn_lpac_suspended_process):
        return False
    try:
        lpac_body_capability_sids()
    except (ImportError, LpacStagingError, OSError, ValueError):
        return False
    executable = Path(sys.executable if python_executable is None else python_executable)
    try:
        _validate_runtime_source(executable)
    except LpacStagingError:
        return False
    return True


def lpac_body_capability_sids() -> tuple[str, ...]:
    """Return the one capability set allowed for an executable Body token."""

    from .windows_appcontainer import capability_sids_for_names

    capability_sids = capability_sids_for_names(BODY_LPAC_CAPABILITY_NAMES)
    if (
        not isinstance(capability_sids, tuple)
        or not capability_sids
        or len(capability_sids) != len(BODY_LPAC_CAPABILITY_NAMES)
        or any(not isinstance(sid, str) or not sid for sid in capability_sids)
        or len(set(capability_sids)) != len(capability_sids)
    ):
        raise LpacStagingError("lpac_capability_unavailable")
    return capability_sids


class LpacAppContainerProfile:
    """One independently named AppContainer profile for a private Body."""

    def __init__(
        self,
        *,
        name: str,
        sid: int,
        sid_string: str,
    ) -> None:
        self.name = name
        self._sid: int | None = sid
        self.sid_string = sid_string
        self._closed = False
        self._guard = threading.Lock()

    @property
    def sid(self) -> int:
        with self._guard:
            if self._closed or self._sid is None:
                raise LpacStagingError("profile_closed")
            return self._sid

    def close(self) -> None:
        with self._guard:
            if self._closed:
                return
            sid, self._sid = self._sid, None
            self._closed = True
        try:
            result = _userenv.DeleteAppContainerProfile(self.name)
            if result != _S_OK:
                raise LpacStagingError("profile_delete_failed")
        finally:
            if sid is not None:
                _advapi32.FreeSid(ctypes.c_void_p(sid))


@dataclass
class LpacBodyStaging:
    """Private, explicitly ACL'd files usable by exactly one LPAC profile."""

    profile: LpacAppContainerProfile
    root_path: Path
    payload_root: Path
    scratch_path: Path
    runtime_root: Path
    python_executable: Path
    _staging_parent: Path
    _closed: bool = False
    _guard: threading.Lock = field(default_factory=threading.Lock)

    def close(self) -> None:
        with self._guard:
            if self._closed:
                return
            self._closed = True
        root = self.root_path.resolve()
        parent = self._staging_parent.resolve()
        if root.parent != parent:
            raise LpacStagingError("staging_root_invalid")
        cleanup_error: BaseException | None = None
        try:
            shutil.rmtree(root)
        except BaseException as error:
            cleanup_error = error
        try:
            self.profile.close()
        except BaseException:
            if cleanup_error is None:
                raise
        if cleanup_error is not None:
            raise LpacStagingError("staging_cleanup_failed") from cleanup_error


def create_ephemeral_lpac_profile() -> LpacAppContainerProfile:
    """Create one unique AppContainer profile for a single Body process.

    The profile itself has no broad access.  The launcher adds only the
    fixed ``registryRead`` capability to the child token at spawn time.
    """

    _require_windows()
    name = f"agentic-evo-body-{uuid4().hex}"
    sid = ctypes.c_void_p()
    result = _userenv.CreateAppContainerProfile(
        name,
        name,
        "Agentic-Evo private Body process",
        None,
        0,
        ctypes.byref(sid),
    )
    if result != _S_OK or not sid.value:
        raise LpacStagingError("profile_create_failed")
    try:
        sid_string = _sid_to_string(int(sid.value))
        return LpacAppContainerProfile(
            name=name,
            sid=int(sid.value),
            sid_string=sid_string,
        )
    except BaseException:
        _advapi32.FreeSid(sid)
        _userenv.DeleteAppContainerProfile(name)
        raise


def stage_lpac_body_payload(
    profile: LpacAppContainerProfile,
    *,
    source_package: Path | None = None,
    staging_parent: Path,
    python_executable: Path | None = None,
    worker_modules: Iterable[str] = BODY_LPAC_WORKER_MODULES,
) -> LpacBodyStaging:
    """Stage reviewed Body modules plus an isolated, stdlib-only Python runtime.

    ``source_package`` is retained for source-tree tests.  Product callers leave
    it unset so selected module bytes come from package resources, which works
    when Agentic-Evo itself is running from a zipapp.
    """

    _require_windows()
    if not isinstance(profile, LpacAppContainerProfile):
        raise LpacStagingError("invalid_profile")
    modules = _validated_worker_modules(worker_modules)
    worker_sources = _read_worker_sources(
        modules,
        source_package=source_package,
    )
    source_runtime = Path(
        sys.executable if python_executable is None else python_executable
    ).resolve()
    _validate_runtime_source(source_runtime)

    parent = _evo_staging_parent(staging_parent)
    root = parent / f"body-lpac-{uuid4().hex}"
    user_sid = _current_user_sid_string()
    root_sddl = _directory_sddl(
        user_sid=user_sid,
        appcontainer_sid=profile.sid_string,
        appcontainer_access=_PAYLOAD_ACCESS_MASK,
    )
    payload_sddl = root_sddl
    scratch_sddl = _directory_sddl(
        user_sid=user_sid,
        appcontainer_sid=profile.sid_string,
        appcontainer_access=_SCRATCH_ACCESS_MASK,
    )
    _create_secure_directory(root, root_sddl)
    try:
        payload_root = root / "payload"
        _create_secure_directory(payload_root, payload_sddl)
        package = payload_root / "agentic_evo"
        _create_secure_directory(package, payload_sddl)
        scratch = root / "scratch"
        _create_secure_directory(scratch, scratch_sddl)
        for module, raw in worker_sources.items():
            (package / module).write_bytes(raw)
        runtime_root, staged_python = _stage_python_runtime(
            source_executable=source_runtime,
            destination=root / "runtime",
            directory_sddl=payload_sddl,
        )
    except BaseException as error:
        try:
            shutil.rmtree(root)
        except OSError:
            pass
        raise LpacStagingError("payload_stage_failed") from error
    return LpacBodyStaging(
        profile=profile,
        root_path=root,
        payload_root=payload_root,
        scratch_path=scratch,
        runtime_root=runtime_root,
        python_executable=staged_python,
        _staging_parent=parent,
    )


def _evo_staging_parent(staging_parent: Path) -> Path:
    configured = Path(staging_parent)
    if not configured.is_absolute():
        raise LpacStagingError("staging_parent_outside_evo_storage")
    parent = configured.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def _validated_worker_modules(worker_modules: Iterable[str]) -> tuple[str, ...]:
    if not isinstance(worker_modules, Iterable):
        raise LpacStagingError("worker_module_list_invalid")
    modules = tuple(worker_modules)
    if not modules or len(set(modules)) != len(modules):
        raise LpacStagingError("worker_module_list_invalid")
    for module in modules:
        if (
            not isinstance(module, str)
            or not module.endswith(".py")
            or "/" in module
            or "\\" in module
            or module in {"__init__.py", "runtime.py", "witness.py"}
        ):
            raise LpacStagingError("worker_module_list_invalid")
    return modules


def _read_worker_sources(
    modules: tuple[str, ...],
    *,
    source_package: Path | None,
) -> dict[str, bytes]:
    if source_package is not None:
        source = Path(source_package).resolve()
        if not source.is_dir():
            raise LpacStagingError("worker_source_unavailable")
        try:
            return {
                module: (source / module).read_bytes()
                for module in modules
            }
        except OSError as error:
            raise LpacStagingError("worker_source_unavailable") from error
    try:
        package = resources.files("agentic_evo")
        return {module: package.joinpath(module).read_bytes() for module in modules}
    except (FileNotFoundError, ModuleNotFoundError, OSError) as error:
        raise LpacStagingError("worker_source_unavailable") from error


def _validate_runtime_source(executable: Path) -> None:
    runtime_root = executable.resolve().parent
    version_dll = runtime_root / (
        f"python{sys.version_info.major}{sys.version_info.minor}.dll"
    )
    if (
        sys.version_info < (3, 12)
        or not executable.is_file()
        or not version_dll.is_file()
        or not (runtime_root / "Lib").is_dir()
        or not (runtime_root / "DLLs").is_dir()
    ):
        raise LpacStagingError("python_runtime_unavailable")
    if not any(path.is_file() and path.suffix.casefold() == ".dll" for path in runtime_root.iterdir()):
        raise LpacStagingError("python_runtime_unavailable")


def _stage_python_runtime(
    *,
    source_executable: Path,
    destination: Path,
    directory_sddl: str,
) -> tuple[Path, Path]:
    source_root = source_executable.resolve().parent
    _create_secure_directory(destination, directory_sddl)
    try:
        runtime_files = [source_executable.name]
        runtime_files.extend(
            path.name
            for path in source_root.iterdir()
            if path.is_file()
            and path.suffix.casefold() == ".dll"
            and path.name not in runtime_files
        )
        for file_name in runtime_files:
            shutil.copyfile(source_root / file_name, destination / file_name)
        source_lib = source_root / "Lib"
        destination_lib = destination / "Lib"
        shutil.copytree(
            source_lib,
            destination_lib,
            ignore=_ignore_untrusted_python_library_entries,
            copy_function=shutil.copyfile,
        )
        source_dlls = source_root / "DLLs"
        destination_dlls = destination / "DLLs"
        shutil.copytree(
            source_dlls,
            destination_dlls,
            ignore=_ignore_untrusted_python_library_entries,
            copy_function=shutil.copyfile,
        )
    except BaseException as error:
        raise LpacStagingError("python_runtime_stage_failed") from error
    return destination, destination / source_executable.name


def _ignore_untrusted_python_library_entries(
    _directory: str,
    names: list[str],
) -> set[str]:
    return {
        name
        for name in names
        if (
            name in _EXCLUDED_PYTHON_LIBRARY_DIRECTORIES
            or name in _EXCLUDED_PYTHON_LIBRARY_FILES
            or name.endswith(".pyc")
        )
    }


def _require_windows() -> None:
    if sys.platform != "win32":
        raise LpacStagingError("lpac_unavailable")


def _directory_sddl(
    *,
    user_sid: str,
    appcontainer_sid: str,
    appcontainer_access: int,
) -> str:
    return (
        "D:P"
        f"(A;OICI;FA;;;{user_sid})"
        "(A;OICI;FA;;;SY)"
        f"(A;OICI;0x{appcontainer_access:08X};;;{appcontainer_sid})"
    )


def _create_secure_directory(path: Path, sddl: str) -> None:
    descriptor = ctypes.c_void_p()
    descriptor_size = wintypes.DWORD()
    if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        _SDDL_REVISION_1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise LpacStagingError("staging_acl_invalid")
    try:
        attributes = _SecurityAttributes(
            nLength=ctypes.sizeof(_SecurityAttributes),
            lpSecurityDescriptor=descriptor.value,
            bInheritHandle=False,
        )
        if not _kernel32.CreateDirectoryW(str(path), ctypes.byref(attributes)):
            raise LpacStagingError("staging_directory_create_failed")
    finally:
        _kernel32.LocalFree(descriptor)


def _current_user_sid_string() -> str:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(),
        _TOKEN_QUERY,
        ctypes.byref(token),
    ):
        raise LpacStagingError("current_user_sid_unavailable")
    try:
        buffer = _token_information(token, _TOKEN_USER)
        user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        if not user.User.Sid:
            raise LpacStagingError("current_user_sid_unavailable")
        return _sid_to_string(int(user.User.Sid))
    finally:
        _kernel32.CloseHandle(token)


def _sid_to_string(sid: int) -> str:
    text = ctypes.c_void_p()
    if not _advapi32.ConvertSidToStringSidW(
        ctypes.c_void_p(sid),
        ctypes.byref(text),
    ):
        raise LpacStagingError("sid_string_unavailable")
    try:
        value = ctypes.wstring_at(text.value)
        if not value:
            raise LpacStagingError("sid_string_unavailable")
        return value
    finally:
        _kernel32.LocalFree(text)


def _token_information(token: int, information_class: int) -> ctypes.Array:
    required = wintypes.DWORD()
    if _advapi32.GetTokenInformation(
        token,
        information_class,
        None,
        0,
        ctypes.byref(required),
    ):
        raise LpacStagingError("token_information_invalid")
    if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or not required.value:
        raise LpacStagingError("token_information_unavailable")
    buffer = ctypes.create_string_buffer(required.value)
    if not _advapi32.GetTokenInformation(
        token,
        information_class,
        buffer,
        required.value,
        ctypes.byref(required),
    ):
        raise LpacStagingError("token_information_unavailable")
    return buffer
