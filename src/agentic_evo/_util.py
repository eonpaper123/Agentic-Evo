from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, BinaryIO
from uuid import uuid4

from .errors import IntegrityError


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"cannot read valid JSON from {path}") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"expected an object in {path}")
    return value


def atomic_write_bytes(path: Path, value: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        mode,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_bytes(path, canonical_json_bytes(value) + b"\n")


class ExclusiveFileLock(AbstractContextManager["ExclusiveFileLock"]):
    """Small cross-platform OS lock released automatically when a process dies."""

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._handle: BinaryIO | None = None

    def __enter__(self) -> "ExclusiveFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        handle = os.fdopen(descriptor, "r+b", closefd=True)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())

        deadline = time.monotonic() + self.timeout_seconds
        try:
            while True:
                handle.seek(0)
                if self._try_lock(handle):
                    self._handle = handle
                    return self
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out acquiring lock {self.path}")
                time.sleep(0.01)
        except BaseException:
            handle.close()
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._handle is None:
            return
        try:
            self._unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    @staticmethod
    def _try_lock(handle: BinaryIO) -> bool:
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True

        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    @staticmethod
    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
