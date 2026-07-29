from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from queue import Empty, Queue
import secrets
import subprocess
import sys
import threading
from typing import Any, BinaryIO, Mapping
from uuid import uuid4

from ._util import canonical_json_bytes, sha256_hex
from .body import BODY_SCHEMA_VERSION
from .errors import AgenticEvoError
from .runtime import DevelopmentalRuntime
from .witness import CurrentBodySession, WitnessCore


BODY_BOOT_PROTOCOL = "agentic-evo-private-boot-v1"
MAX_BODY_BOOT_FRAME_BYTES = 8 * 1024 * 1024


class BodyBootError(AgenticEvoError):
    """Raised when the exact-Head subprocess rehearsal cannot be established."""


@dataclass(frozen=True)
class BootEnvelope:
    protocol: str
    boot_session: str
    challenge: str
    root: str
    head: str
    generation: int
    activation_kind: str
    activation_artifact: str
    activation_digest: str
    body_package: dict[str, Any]


@dataclass(frozen=True)
class ReadyEcho:
    protocol: str
    boot_session: str
    challenge: str
    root: str
    head: str
    generation: int
    activation_kind: str
    activation_artifact: str
    activation_digest: str


def validate_ready_echo(boot: BootEnvelope, ready: ReadyEcho) -> None:
    expected = {
        "protocol": boot.protocol,
        "boot_session": boot.boot_session,
        "challenge": boot.challenge,
        "root": boot.root,
        "head": boot.head,
        "generation": boot.generation,
        "activation_kind": boot.activation_kind,
        "activation_artifact": boot.activation_artifact,
        "activation_digest": boot.activation_digest,
    }
    if asdict(ready) != expected:
        raise BodyBootError("Body ReadyEcho does not match its exact boot envelope")


def write_private_frame(
    stream: BinaryIO,
    value: Mapping[str, Any],
    *,
    max_bytes: int | None = MAX_BODY_BOOT_FRAME_BYTES,
) -> None:
    raw = canonical_json_bytes(dict(value))
    if max_bytes is not None and len(raw) > max_bytes:
        raise BodyBootError("private Body frame exceeds its byte bound")
    try:
        stream.write(raw + b"\n")
        stream.flush()
    except (BrokenPipeError, OSError, ValueError) as exc:
        raise BodyBootError("private Body pipe is unavailable") from exc


def read_private_frame(
    stream: BinaryIO,
    *,
    max_bytes: int | None = MAX_BODY_BOOT_FRAME_BYTES,
) -> dict[str, Any]:
    try:
        raw = (
            stream.readline()
            if max_bytes is None
            else stream.readline(max_bytes + 2)
        )
    except (OSError, ValueError) as exc:
        raise BodyBootError("private Body pipe is unavailable") from exc
    if not raw:
        raise BodyBootError("private Body pipe reached EOF")
    if (
        (max_bytes is not None and len(raw) > max_bytes + 1)
        or not raw.endswith(b"\n")
    ):
        raise BodyBootError("private Body frame is incomplete or oversized")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BodyBootError("private Body frame is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BodyBootError("private Body frame must contain one JSON object")
    return value


def boot_envelope_from_mapping(value: Mapping[str, Any]) -> BootEnvelope:
    expected = {
        "protocol",
        "boot_session",
        "challenge",
        "root",
        "head",
        "generation",
        "activation_kind",
        "activation_artifact",
        "activation_digest",
        "body_package",
    }
    if set(value) != expected:
        raise BodyBootError("boot envelope has unexpected fields")
    string_fields = expected - {"generation", "body_package"}
    if any(
        not isinstance(value.get(field), str) or not value[field]
        for field in string_fields
    ):
        raise BodyBootError("boot envelope contains an invalid string field")
    if value["protocol"] != BODY_BOOT_PROTOCOL:
        raise BodyBootError("boot envelope uses the wrong protocol")
    generation = value["generation"]
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        raise BodyBootError("boot envelope contains an invalid generation")
    package = value["body_package"]
    if not isinstance(package, dict):
        raise BodyBootError("boot envelope body_package must be an object")
    return BootEnvelope(
        protocol=str(value["protocol"]),
        boot_session=str(value["boot_session"]),
        challenge=str(value["challenge"]),
        root=str(value["root"]),
        head=str(value["head"]),
        generation=generation,
        activation_kind=str(value["activation_kind"]),
        activation_artifact=str(value["activation_artifact"]),
        activation_digest=str(value["activation_digest"]),
        body_package=dict(package),
    )


def ready_echo_from_mapping(value: Mapping[str, Any]) -> ReadyEcho:
    expected = {
        "protocol",
        "boot_session",
        "challenge",
        "root",
        "head",
        "generation",
        "activation_kind",
        "activation_artifact",
        "activation_digest",
    }
    if set(value) != expected:
        raise BodyBootError("ReadyEcho has unexpected fields")
    string_fields = expected - {"generation"}
    if any(
        not isinstance(value.get(field), str) or not value[field]
        for field in string_fields
    ):
        raise BodyBootError("ReadyEcho contains an invalid string field")
    generation = value["generation"]
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        raise BodyBootError("ReadyEcho contains an invalid generation")
    return ReadyEcho(
        protocol=str(value["protocol"]),
        boot_session=str(value["boot_session"]),
        challenge=str(value["challenge"]),
        root=str(value["root"]),
        head=str(value["head"]),
        generation=generation,
        activation_kind=str(value["activation_kind"]),
        activation_artifact=str(value["activation_artifact"]),
        activation_digest=str(value["activation_digest"]),
    )


def validate_boot_package(boot: BootEnvelope) -> None:
    package = boot.body_package
    if set(package) != {"commitment", "manifest_base64", "blobs"}:
        raise BodyBootError("Body package has unexpected fields")
    if package["commitment"] != boot.head:
        raise BodyBootError("Body package commitment is not the spawn Head")
    try:
        manifest_raw = base64.b64decode(
            str(package["manifest_base64"]),
            validate=True,
        )
        manifest = json.loads(manifest_raw)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BodyBootError("Body package manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise BodyBootError("Body package manifest must be an object")
    if sha256_hex(canonical_json_bytes(manifest)) != boot.head:
        raise BodyBootError("Body package does not reconstruct the spawn Head")
    if manifest.get("schema_version") != BODY_SCHEMA_VERSION:
        raise BodyBootError("Body package schema is unsupported")
    if manifest.get("root") != boot.root:
        raise BodyBootError("Body package Root does not match the boot envelope")
    if manifest.get("generation") != boot.generation:
        raise BodyBootError("Body package generation does not match the boot envelope")
    if manifest.get("activation_kind") != boot.activation_kind:
        raise BodyBootError("Body activation kind does not match the boot envelope")
    if manifest.get("activation_artifact") != boot.activation_artifact:
        raise BodyBootError("Body activation path does not match the boot envelope")

    files = manifest.get("files")
    blobs = package["blobs"]
    if not isinstance(files, dict) or not isinstance(blobs, dict):
        raise BodyBootError("Body package files and blobs must be objects")
    digests = set()
    for path, digest in files.items():
        if (
            not isinstance(path, str)
            or not path
            or not isinstance(digest, str)
            or len(digest) != 64
        ):
            raise BodyBootError("Body package contains an invalid file commitment")
        digests.add(digest)
    if set(blobs) != digests:
        raise BodyBootError("Body package blobs do not match its manifest")

    decoded_blobs: dict[str, bytes] = {}
    for digest, encoded in blobs.items():
        try:
            raw = base64.b64decode(str(encoded), validate=True)
        except (ValueError, TypeError) as exc:
            raise BodyBootError("Body package contains invalid blob bytes") from exc
        if sha256_hex(raw) != digest:
            raise BodyBootError("Body package blob commitment is invalid")
        decoded_blobs[digest] = raw

    activation_digest = files.get(boot.activation_artifact)
    if activation_digest != boot.activation_digest:
        raise BodyBootError("activation digest does not match the Body package")
    if activation_digest not in decoded_blobs:
        raise BodyBootError("activation bytes are absent from the Body package")


def ready_echo_for_boot(boot: BootEnvelope) -> ReadyEcho:
    validate_boot_package(boot)
    return ReadyEcho(
        protocol=boot.protocol,
        boot_session=boot.boot_session,
        challenge=boot.challenge,
        root=boot.root,
        head=boot.head,
        generation=boot.generation,
        activation_kind=boot.activation_kind,
        activation_artifact=boot.activation_artifact,
        activation_digest=boot.activation_digest,
    )


class SpawnedBodyProcess:
    provenance = "subprocess_rehearsal"

    def __init__(
        self,
        *,
        process: subprocess.Popen[bytes],
        command: tuple[str, ...],
        boot: BootEnvelope,
        ready: ReadyEcho,
        session: CurrentBodySession,
        witness: WitnessCore,
    ) -> None:
        self._process = process
        self.command = command
        self.boot = boot
        self.ready = ready
        self._session = session
        self._witness = witness
        self._guard = threading.Lock()
        self._closed = threading.Event()
        monitor = threading.Thread(target=self._monitor, daemon=True)
        monitor.start()

    @property
    def pid(self) -> int:
        return self._process.pid

    def is_alive(self) -> bool:
        return not self._closed.is_set() and self._process.poll() is None

    def wait_closed(self, *, timeout_seconds: float) -> bool:
        return self._closed.wait(timeout_seconds)

    def assert_bound(self) -> None:
        if not self.is_alive():
            raise BodyBootError("Body subprocess is not alive")
        try:
            self._witness._authorize(self._session)
        except AgenticEvoError as exc:
            raise BodyBootError(
                "Body subprocess lost its trusted boot binding"
            ) from exc

    def describe(self) -> dict[str, Any]:
        return {
            "state": "ready" if self.is_alive() else "stopped",
            "pid": self.pid,
            "head": self.boot.head,
            "provenance": self.provenance,
        }

    def close(self) -> None:
        if self._closed.is_set():
            return
        if self._process.poll() is None and self._process.stdin is not None:
            try:
                write_private_frame(
                    self._process.stdin,
                    {
                        "protocol": BODY_BOOT_PROTOCOL,
                        "operation": "stop",
                        "boot_session": self.boot.boot_session,
                    },
                )
            except BodyBootError:
                pass
        try:
            self._process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=1.0)
        self._retire()

    def _monitor(self) -> None:
        self._process.wait()
        self._retire()

    def _retire(self) -> None:
        with self._guard:
            if self._closed.is_set():
                return
            try:
                if self._process.stdin is not None:
                    self._process.stdin.close()
                if self._process.stdout is not None:
                    self._process.stdout.close()
                self._session.close()
            finally:
                self._closed.set()


class BodyProcessSupervisor:
    """Spawns a fixed diagnostic worker from one exact Current Head."""

    def __init__(
        self,
        runtime: DevelopmentalRuntime,
        witness: WitnessCore,
        *,
        ready_timeout_seconds: float = 2.0,
    ) -> None:
        if ready_timeout_seconds <= 0:
            raise ValueError("ready_timeout_seconds must be positive")
        self.runtime = runtime
        self.witness = witness
        self.ready_timeout_seconds = ready_timeout_seconds

    def spawn_current(self) -> SpawnedBodyProcess:
        status = self.runtime.status()
        session: CurrentBodySession | None = None
        process: subprocess.Popen[bytes] | None = None
        try:
            session = self.witness.open_current_body_session(
                expected_head=status.head
            )
            manifest = self.runtime.body_store.read_manifest(status.head)
            if (
                manifest.activation_kind is None
                or manifest.activation_artifact is None
            ):
                raise BodyBootError("Current Head has no activation descriptor")
            activation_digest = dict(manifest.files)[
                manifest.activation_artifact
            ]
            boot = BootEnvelope(
                protocol=BODY_BOOT_PROTOCOL,
                boot_session=uuid4().hex,
                challenge=secrets.token_hex(32),
                root=status.root,
                head=status.head,
                generation=status.generation,
                activation_kind=manifest.activation_kind,
                activation_artifact=manifest.activation_artifact,
                activation_digest=activation_digest,
                body_package=self.runtime.body_store.export_manifest(status.head),
            )
            command = (
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.body_worker",
            )
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                close_fds=True,
                cwd=Path(sys.executable).resolve().parent,
                env=_body_worker_environment(),
            )
            if process.stdin is None or process.stdout is None:
                raise BodyBootError("Body subprocess pipes were not created")
            write_private_frame(
                process.stdin,
                asdict(boot),
                max_bytes=None,
            )
            response = _read_private_frame_with_timeout(
                process.stdout,
                timeout_seconds=self.ready_timeout_seconds,
            )
            ready = ready_echo_from_mapping(response)
            validate_ready_echo(boot, ready)
            if process.poll() is not None:
                raise BodyBootError("Body subprocess exited before becoming ready")
            self.witness._authorize(session)
            return SpawnedBodyProcess(
                process=process,
                command=command,
                boot=boot,
                ready=ready,
                session=session,
                witness=self.witness,
            )
        except Exception as exc:
            if process is not None:
                _terminate_process(process)
            if session is not None:
                session.close()
            if isinstance(exc, BodyBootError):
                raise
            raise BodyBootError(
                "exact-Head subprocess boot rehearsal failed"
            ) from exc


def _read_private_frame_with_timeout(
    stream: BinaryIO,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    outcome: Queue[dict[str, Any] | BaseException] = Queue(maxsize=1)

    def read() -> None:
        try:
            outcome.put(read_private_frame(stream))
        except BaseException as exc:
            outcome.put(exc)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        value = outcome.get(timeout=timeout_seconds)
    except Empty as exc:
        raise BodyBootError("Body ReadyEcho timed out") from exc
    if isinstance(value, BaseException):
        raise BodyBootError("Body ReadyEcho could not be read") from value
    return value


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)
    if process.stdin is not None:
        process.stdin.close()
    if process.stdout is not None:
        process.stdout.close()


def _body_worker_environment() -> dict[str, str]:
    source_root = str(Path(__file__).resolve().parents[1])
    environment = {
        "PYTHONPATH": source_root,
        "PYTHONUTF8": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in ("SystemRoot", "WINDIR"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment
