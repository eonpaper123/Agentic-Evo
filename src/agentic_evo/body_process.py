from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import secrets
import signal
import subprocess
import sys
import threading
from time import monotonic
from typing import TYPE_CHECKING, Any, BinaryIO, Mapping, Protocol, Sequence
from uuid import uuid4

from ._util import canonical_json_bytes, sha256_hex
from .body import BODY_SCHEMA_VERSION, DEVELOPMENT_DESCRIPTOR_UNSET
from .development_executor import (
    BodyActionResult,
    DevelopmentExecutorError,
    DevelopmentOpportunity,
)
from .errors import AgenticEvoError
from .organ_broker import (
    OrganInvocationRequest,
    OrganInvoker,
    OrganResponse,
    RecallTrace,
)
if TYPE_CHECKING:
    from .runtime import DevelopmentalRuntime
    from .witness import CurrentBodySession, WitnessCore

if sys.platform == "win32":
    import msvcrt

    from .windows_native import (
        KillOnCloseJob,
        spawn_restricted_suspended_process,
    )


BODY_BOOT_PROTOCOL = "agentic-evo-private-boot-v1"
BODY_LINEAGE_PROTOCOL = "agentic-evo-private-lineage-v1"
MAX_BODY_BOOT_FRAME_BYTES = 8 * 1024 * 1024
MAX_PRIVATE_LINEAGE_TEXT_BYTES = 1024
MAX_DEVELOPMENT_RECALL_TRACE_BYTES = 256 * 1024


class DevelopmentRecallProvider(Protocol):
    """Host-only, same-Root experience reader for one active Body offer."""

    def __call__(
        self,
        *,
        opportunity: DevelopmentOpportunity,
        before_sequence: int | None,
        limit: int,
        cancel_event: threading.Event,
    ) -> tuple[list[dict[str, Any]], bool]:
        ...


def _recall_trace_wire_bytes(trace: Sequence[RecallTrace]) -> int:
    return len(
        canonical_json_bytes([item.to_mapping() for item in trace])
    )


def _development_recall_event_ref(value: object) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("development recall event is invalid")
    event_id = value.get("event_id")
    if (
        not isinstance(event_id, str)
        or not event_id
        or len(event_id.encode("utf-8")) > 4 * 1024
    ):
        raise ValueError("development recall event id is invalid")
    return event_id


def _private_lineage_optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")
    if len(value.encode("utf-8")) > MAX_PRIVATE_LINEAGE_TEXT_BYTES:
        raise ValueError(f"{field} exceeds the private lineage text byte bound")
    return value


def _private_development_descriptor(
    development_kind: str | None | object,
    development_artifact: str | None | object,
) -> tuple[str | None | object, str | None | object]:
    if (development_kind is DEVELOPMENT_DESCRIPTOR_UNSET) != (
        development_artifact is DEVELOPMENT_DESCRIPTOR_UNSET
    ):
        raise ValueError("development descriptor fields must be supplied together")
    if development_kind is DEVELOPMENT_DESCRIPTOR_UNSET:
        return development_kind, development_artifact
    if (development_kind is None) != (development_artifact is None):
        raise ValueError("development descriptor fields must both be strings or null")
    if development_kind is not None:
        if (
            not isinstance(development_kind, str)
            or not development_kind
            or len(development_kind.encode("utf-8")) > 128
            or not isinstance(development_artifact, str)
            or not development_artifact
            or len(development_artifact.encode("utf-8"))
            > MAX_PRIVATE_LINEAGE_TEXT_BYTES
        ):
            raise ValueError("development descriptor fields are invalid")
    return development_kind, development_artifact


class BodyBootError(AgenticEvoError):
    """Raised when the exact-Head subprocess rehearsal cannot be established."""


class BodyLineageOutcomeUnknown(BodyBootError):
    """Raised when a timed-out request may already have crossed linearization."""

    def __init__(
        self,
        *,
        operation: str,
        candidate_head: str | None = None,
    ) -> None:
        self.operation = operation
        self.candidate_head = candidate_head
        super().__init__(
            "private Body lineage outcome is unknown; inspect trusted state"
        )


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
    development_kind: str | None = None
    development_artifact: str | None = None


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
    development_kind: str | None = None
    development_artifact: str | None = None


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
        "development_kind": boot.development_kind,
        "development_artifact": boot.development_artifact,
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
        remaining = memoryview(raw + b"\n")
        while remaining:
            written = stream.write(remaining)
            if (
                not isinstance(written, int)
                or isinstance(written, bool)
                or written <= 0
                or written > len(remaining)
            ):
                raise BodyBootError("private Body pipe made no write progress")
            remaining = remaining[written:]
        stream.flush()
    except (BrokenPipeError, OSError, TypeError, ValueError) as exc:
        raise BodyBootError("private Body pipe is unavailable") from exc


def _write_private_frame_with_timeout(
    stream: BinaryIO,
    value: Mapping[str, Any],
    *,
    timeout_seconds: float,
    process: Any,
    max_bytes: int | None = MAX_BODY_BOOT_FRAME_BYTES,
    write_lock: Any | None = None,
) -> None:
    if timeout_seconds <= 0:
        _kill_process_if_alive(process)
        raise BodyBootError("private Body frame write timed out")
    outcome: Queue[BaseException | None] = Queue(maxsize=1)

    def write() -> None:
        try:
            if write_lock is None:
                write_private_frame(stream, value, max_bytes=max_bytes)
            else:
                with write_lock:
                    write_private_frame(stream, value, max_bytes=max_bytes)
        except BaseException as exc:
            outcome.put_nowait(exc)
        else:
            outcome.put_nowait(None)

    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    try:
        failure = outcome.get(timeout=timeout_seconds)
    except Empty as exc:
        _kill_process_if_alive(process)
        writer.join(timeout=0.25)
        raise BodyBootError("private Body frame write timed out") from exc
    if isinstance(failure, BaseException):
        if isinstance(failure, BodyBootError):
            raise failure
        raise BodyBootError("private Body frame could not be written") from failure


def _kill_process_if_alive(process: Any) -> None:
    try:
        alive = process.poll() is None
    except OSError:
        return
    if not alive:
        return
    pid = getattr(process, "pid", None)
    if os.name == "posix" and isinstance(pid, int) and pid > 0:
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
                return
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


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
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BodyBootError("private Body frame is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BodyBootError("private Body frame must contain one JSON object")
    _require_bounded_private_value_depth(value)
    return value


def _require_bounded_private_value_depth(value: object) -> None:
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > 128:
            raise BodyBootError("private Body frame exceeds its nesting bound")
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


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
        "development_kind",
        "development_artifact",
    }
    if set(value) != expected:
        raise BodyBootError("boot envelope has unexpected fields")
    string_fields = expected - {
        "generation",
        "body_package",
        "development_kind",
        "development_artifact",
    }
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
    development_kind = value["development_kind"]
    development_artifact = value["development_artifact"]
    if (development_kind is None) != (development_artifact is None) or (
        development_kind is not None
        and (
            not isinstance(development_kind, str)
            or not development_kind
            or not isinstance(development_artifact, str)
            or not development_artifact
        )
    ):
        raise BodyBootError("boot envelope development descriptor is invalid")
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
        development_kind=development_kind,
        development_artifact=development_artifact,
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
        "development_kind",
        "development_artifact",
    }
    if set(value) != expected:
        raise BodyBootError("ReadyEcho has unexpected fields")
    string_fields = expected - {
        "generation",
        "development_kind",
        "development_artifact",
    }
    if any(
        not isinstance(value.get(field), str) or not value[field]
        for field in string_fields
    ):
        raise BodyBootError("ReadyEcho contains an invalid string field")
    generation = value["generation"]
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        raise BodyBootError("ReadyEcho contains an invalid generation")
    development_kind = value["development_kind"]
    development_artifact = value["development_artifact"]
    if (development_kind is None) != (development_artifact is None) or (
        development_kind is not None
        and (
            not isinstance(development_kind, str)
            or not development_kind
            or not isinstance(development_artifact, str)
            or not development_artifact
        )
    ):
        raise BodyBootError("ReadyEcho development descriptor is invalid")
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
        development_kind=development_kind,
        development_artifact=development_artifact,
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
    if manifest.get("development_kind") != boot.development_kind:
        raise BodyBootError(
            "Body development kind does not match the boot envelope"
        )
    if manifest.get("development_artifact") != boot.development_artifact:
        raise BodyBootError(
            "Body development path does not match the boot envelope"
        )

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
        development_kind=boot.development_kind,
        development_artifact=boot.development_artifact,
    )


class SpawnedBodyProcess:
    provenance = "subprocess_rehearsal"

    def __init__(
        self,
        *,
        process: Any,
        private_reader: BinaryIO,
        private_writer: BinaryIO,
        body_channel: str,
        process_token: str,
        command: tuple[str, ...],
        boot: BootEnvelope,
        ready: ReadyEcho,
        session: CurrentBodySession,
        witness: WitnessCore,
        process_fence: Any | None,
        lpac_staging: Any | None,
        request_timeout_seconds: float,
        organ_broker: OrganInvoker | None,
        development_recall: DevelopmentRecallProvider | None,
        development_deadline: float | None,
    ) -> None:
        self._process = process
        self._private_reader = private_reader
        self._private_writer = private_writer
        self._body_channel = body_channel
        self._process_token = process_token
        self.command = command
        self.boot = boot
        self.ready = ready
        self._session = session
        self._witness = witness
        self._process_fence = process_fence
        self._lpac_staging = lpac_staging
        self._request_timeout_seconds = request_timeout_seconds
        self._organ_broker = organ_broker
        self._development_recall = development_recall
        self._development_deadline = development_deadline
        self._guard = threading.Lock()
        self._write_guard = threading.Lock()
        self._rehearsal_guard = threading.Lock()
        self._rehearsal_outcomes: Queue[
            dict[str, Any] | BaseException
        ] = Queue(maxsize=1)
        self._pending_rehearsal: tuple[str, int] | None = None
        self._pending_lineage_request: tuple[str, int] | None = None
        self._pending_lineage_response: dict[str, Any] | None = None
        self._next_lineage_sequence = 1
        self._last_lineage_sequence = 0
        self._organ_guard = threading.Lock()
        self._active_organ_opportunity: DevelopmentOpportunity | None = None
        self._organ_cancel_event: threading.Event | None = None
        self._organ_response: OrganResponse | None = None
        self._organ_invocation_seen = False
        self._organ_revoked = False
        self._organ_result_invalidated = False
        self._development_direct_recall_trace: list[RecallTrace] = []
        self._active_development_deadline: float | None = None
        self._closed = threading.Event()
        dispatcher = threading.Thread(
            target=self._dispatch_private_channel,
            daemon=True,
        )
        dispatcher.start()
        monitor = threading.Thread(target=self._monitor, daemon=True)
        self._monitor_thread = monitor
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
            "process_token": self._process_token,
            "body_channel": self._body_channel,
            "process_fencing": (
                "windows_job_object_kill_on_close"
                if self._process_fence is not None
                else "subprocess_only"
            ),
        }

    def rehearse_prepare_successor(
        self,
        *,
        files: Mapping[str, str],
        activation_kind: str | None = None,
        activation_artifact: str | None = None,
        development_kind: str | None | object = DEVELOPMENT_DESCRIPTOR_UNSET,
        development_artifact: str | None | object = DEVELOPMENT_DESCRIPTOR_UNSET,
        causation_ref: str | None = None,
    ) -> str:
        normalized_files = dict(files)
        if not normalized_files or any(
            not isinstance(path, str)
            or not path
            or not isinstance(value, str)
            for path, value in normalized_files.items()
        ):
            raise ValueError("rehearsal files must be non-empty UTF-8 text mappings")
        for value in (activation_kind, activation_artifact):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError("activation fields must be non-empty strings")
        causation_ref = _private_lineage_optional_text(
            causation_ref,
            "causation_ref",
        )
        development_kind, development_artifact = _private_development_descriptor(
            development_kind,
            development_artifact,
        )
        command: dict[str, Any] = {
            "protocol": BODY_LINEAGE_PROTOCOL,
            "kind": "rehearsal_command",
            "operation": "prepare_successor",
            "files": normalized_files,
            "activation_kind": activation_kind,
            "activation_artifact": activation_artifact,
            "causation_ref": causation_ref,
        }
        if development_kind is not DEVELOPMENT_DESCRIPTOR_UNSET:
            command["development_kind"] = development_kind
            command["development_artifact"] = development_artifact
        result = self._run_rehearsal(command)
        if set(result) != {
            "protocol",
            "kind",
            "operation",
            "sequence",
            "ok",
            "candidate_head",
        } or (
            result.get("protocol") != BODY_LINEAGE_PROTOCOL
            or result.get("kind") != "rehearsal_result"
            or result.get("operation") != "prepare_successor"
            or result.get("ok") is not True
            or not isinstance(result.get("candidate_head"), str)
        ):
            raise BodyBootError("private prepare rehearsal was rejected")
        return str(result["candidate_head"])

    def rehearse_advance_head(self, *, candidate_head: str) -> dict[str, Any]:
        if not isinstance(candidate_head, str) or not candidate_head:
            raise ValueError("candidate_head must be a non-empty string")
        result = self._run_rehearsal(
            {
                "protocol": BODY_LINEAGE_PROTOCOL,
                "kind": "rehearsal_command",
                "operation": "advance_head",
                "candidate_head": candidate_head,
            }
        )
        if set(result) != {
            "protocol",
            "kind",
            "operation",
            "sequence",
            "ok",
            "head",
            "generation",
            "authority",
        } or (
            result.get("protocol") != BODY_LINEAGE_PROTOCOL
            or result.get("kind") != "rehearsal_result"
            or result.get("operation") != "advance_head"
            or result.get("ok") is not True
            or not isinstance(result.get("head"), str)
            or not isinstance(result.get("generation"), int)
            or isinstance(result.get("generation"), bool)
            or not isinstance(result.get("authority"), str)
        ):
            raise BodyBootError("private advance rehearsal was rejected")
        return {
            "head": result["head"],
            "generation": result["generation"],
            "authority": result["authority"],
        }

    def submit_surface_successor(
        self,
        *,
        files: Mapping[str, str],
        activation_kind: str,
        activation_artifact: str,
        causation_ref: str | None = None,
        execution_surface: str,
        session_id: str,
        expected_head: str,
    ) -> dict[str, Any]:
        if not files or any(
            not isinstance(path, str)
            or not path
            or not isinstance(value, str)
            for path, value in files.items()
        ):
            raise ValueError("successor files must be non-empty UTF-8 text mappings")
        for value in (
            activation_kind,
            activation_artifact,
            execution_surface,
            session_id,
            expected_head,
        ):
            if not isinstance(value, str) or not value:
                raise ValueError("surface submission fields must be non-empty strings")
        causation_ref = _private_lineage_optional_text(
            causation_ref,
            "causation_ref",
        )
        result = self._run_rehearsal(
            {
                "protocol": BODY_LINEAGE_PROTOCOL,
                "kind": "surface_command",
                "operation": "submit_successor",
                "files": dict(files),
                "activation_kind": activation_kind,
                "activation_artifact": activation_artifact,
                "causation_ref": causation_ref,
                "execution_surface": execution_surface,
                "session_id": session_id,
                "expected_head": expected_head,
            }
        )
        if set(result) != {
            "protocol",
            "kind",
            "operation",
            "sequence",
            "ok",
            "head",
            "generation",
            "authority",
        } or (
            result.get("protocol") != BODY_LINEAGE_PROTOCOL
            or result.get("kind") != "rehearsal_result"
            or result.get("operation") != "submit_successor"
            or result.get("ok") is not True
            or not isinstance(result.get("head"), str)
            or not isinstance(result.get("generation"), int)
            or isinstance(result.get("generation"), bool)
            or not isinstance(result.get("authority"), str)
        ):
            raise BodyBootError("private surface submission was rejected")
        return {
            "head": result["head"],
            "generation": result["generation"],
            "authority": result["authority"],
        }

    def offer_development(
        self,
        *,
        opportunity: DevelopmentOpportunity,
    ) -> BodyActionResult:
        """Let this exact Current Body use one bounded model-organ opportunity."""

        if not isinstance(opportunity, DevelopmentOpportunity):
            raise ValueError("opportunity must be a DevelopmentOpportunity")
        if (
            opportunity.root != self.boot.root
            or opportunity.current_body_ref != self.boot.head
            or not opportunity.lineage_facts.matches_boot(
                head=self.boot.head,
                generation=self.boot.generation,
                activation_kind=self.boot.activation_kind,
                activation_artifact=self.boot.activation_artifact,
                development_kind=self.boot.development_kind,
                development_artifact=self.boot.development_artifact,
            )
        ):
            raise BodyBootError("development opportunity does not bind this Body")
        if self._organ_broker is None:
            raise BodyBootError("model organ broker is unavailable")
        with self._organ_guard:
            if self._active_organ_opportunity is not None:
                raise BodyBootError("another organ opportunity is already active")
            self._active_organ_opportunity = opportunity
            self._organ_cancel_event = threading.Event()
            self._organ_response = None
            self._organ_invocation_seen = False
            self._organ_revoked = False
            self._organ_result_invalidated = False
            self._development_direct_recall_trace = []
        self.assert_bound()
        try:
            result = self._run_rehearsal(
                {
                    "protocol": BODY_LINEAGE_PROTOCOL,
                    "kind": "development_offer",
                    "operation": "offer_development",
                    "opportunity": opportunity.to_body_mapping(),
                }
            )
            with self._organ_guard:
                organ_response = self._organ_response
                organ_result_invalidated = self._organ_result_invalidated
                direct_recall_trace = tuple(self._development_direct_recall_trace)
            body_result = _development_result_from_frame(
                result,
                opportunity=opportunity,
                organ_response=organ_response,
                organ_result_invalidated=organ_result_invalidated,
                direct_recall_trace=direct_recall_trace,
            )
            if body_result.action != "candidate_submitted":
                self.assert_bound()
            return body_result
        finally:
            self._clear_organ_opportunity(opportunity)

    def close(self) -> None:
        self._revoke_organ_opportunity()
        if self._closed.is_set():
            return
        if self._pending_rehearsal is not None:
            # A development organ can be blocked in its model call.  Do not
            # wait for its command lock before honouring Off/service shutdown.
            self._abort_in_flight()
            if not self._closed.wait(1.0):
                self._monitor_thread.join(timeout=1.0)
            return
        with self._rehearsal_guard:
            try:
                alive = self._process.poll() is None
            except OSError:
                alive = False
            if alive:
                try:
                    self._write_frame(
                        {
                            "protocol": BODY_BOOT_PROTOCOL,
                            "operation": "stop",
                            "boot_session": self.boot.boot_session,
                        },
                        timeout_seconds=min(
                            self._request_timeout_seconds,
                            0.25,
                        ),
                    )
                except BodyBootError:
                    pass
        if self._closed.wait(1.0):
            return
        try:
            self._process.terminate()
        except OSError:
            pass
        if self._closed.wait(1.0):
            return
        try:
            self._process.kill()
        except OSError:
            pass
        if not self._closed.wait(1.0):
            if self._process_fence is not None:
                self._process_fence.close()
            self._monitor_thread.join(timeout=1.0)

    def _abort_in_flight(self) -> None:
        self._revoke_organ_opportunity()
        if self._process_fence is not None:
            try:
                self._process_fence.close()
            except OSError:
                pass
        _kill_process_if_alive(self._process)

    def _monitor(self) -> None:
        try:
            self._process.wait()
        except OSError:
            pass
        finally:
            self._revoke_organ_opportunity(invalidate_result=False)
            self._publish_rehearsal_outcome(
                BodyBootError("Body subprocess exited during a private request")
            )
            self._retire()

    def _run_rehearsal(self, command: Mapping[str, Any]) -> dict[str, Any]:
        with self._rehearsal_guard:
            if not self.is_alive():
                raise BodyBootError("Body subprocess is not alive")
            if self._pending_rehearsal is not None:
                raise BodyBootError("another private rehearsal is already active")
            operation = command.get("operation")
            if not isinstance(operation, str):
                raise BodyBootError("private rehearsal operation is invalid")
            self._pending_rehearsal = (
                operation,
                self._next_lineage_sequence,
            )
            self._pending_lineage_request = None
            self._pending_lineage_response = None
            deadline = (
                self._development_deadline
                if operation == "offer_development"
                and getattr(self, "_development_deadline", None) is not None
                else monotonic() + self._request_timeout_seconds
            )
            if operation == "offer_development":
                self._active_development_deadline = deadline
            try:
                try:
                    self._write_frame(
                        command,
                        timeout_seconds=max(deadline - monotonic(), 0.0),
                    )
                except BodyBootError as exc:
                    raise BodyLineageOutcomeUnknown(
                        operation=operation,
                        candidate_head=(
                            str(command["candidate_head"])
                            if operation == "advance_head"
                            and isinstance(command.get("candidate_head"), str)
                            else None
                        ),
                    ) from exc
                try:
                    outcome = self._rehearsal_outcomes.get(
                        timeout=max(deadline - monotonic(), 0.0)
                    )
                except Empty as exc:
                    self._revoke_organ_opportunity()
                    _kill_process_if_alive(self._process)
                    raise BodyLineageOutcomeUnknown(
                        operation=operation,
                        candidate_head=(
                            str(command["candidate_head"])
                            if operation == "advance_head"
                            and isinstance(command.get("candidate_head"), str)
                            else None
                        ),
                    ) from exc
                if isinstance(outcome, BaseException):
                    raise BodyLineageOutcomeUnknown(
                        operation=operation,
                        candidate_head=(
                            str(command["candidate_head"])
                            if operation == "advance_head"
                            and isinstance(
                                command.get("candidate_head"),
                                str,
                            )
                            else None
                        ),
                    ) from outcome
                return outcome
            finally:
                if operation == "offer_development":
                    self._active_development_deadline = None
                self._pending_rehearsal = None
                self._pending_lineage_request = None
                self._pending_lineage_response = None

    def _clear_organ_opportunity(self, opportunity: DevelopmentOpportunity) -> None:
        broker: OrganInvoker | None = None
        with self._organ_guard:
            if self._active_organ_opportunity is opportunity:
                self._active_organ_opportunity = None
                self._organ_cancel_event = None
                self._organ_response = None
                self._development_direct_recall_trace = []
                self._organ_result_invalidated = False
                broker = self._organ_broker
        if broker is not None:
            broker.cancel(opportunity.id)

    def _revoke_organ_opportunity(self, *, invalidate_result: bool = True) -> None:
        with self._organ_guard:
            opportunity = self._active_organ_opportunity
            cancel_event = self._organ_cancel_event
            broker = self._organ_broker
            if opportunity is None:
                return
            self._organ_revoked = True
            if invalidate_result:
                self._organ_response = None
                self._organ_result_invalidated = True
            if cancel_event is not None:
                cancel_event.set()
        if broker is not None:
            broker.cancel(opportunity.id)

    def _remaining_development_timeout(self) -> float:
        deadline = getattr(self, "_active_development_deadline", None)
        if deadline is None:
            raise BodyBootError("development opportunity has no active deadline")
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise BodyBootError("development opportunity timed out")
        return remaining

    @staticmethod
    def _valid_development_recall_request(
        *,
        before_sequence: object,
        limit: object,
    ) -> tuple[int | None, int]:
        if before_sequence is not None and (
            not isinstance(before_sequence, int)
            or isinstance(before_sequence, bool)
            or before_sequence < 1
        ):
            raise BodyBootError("private development recall has invalid before_sequence")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 12
        ):
            raise BodyBootError("private development recall has invalid limit")
        return before_sequence, limit

    def _append_development_recall_trace(self, trace: RecallTrace) -> bool:
        with self._organ_guard:
            organ_trace = (
                ()
                if self._organ_response is None
                else self._organ_response.recall_trace
            )
            candidate = (
                *self._development_direct_recall_trace,
                *organ_trace,
                trace,
            )
            if _recall_trace_wire_bytes(candidate) > MAX_DEVELOPMENT_RECALL_TRACE_BYTES:
                return False
            self._development_direct_recall_trace.append(trace)
            return True

    def _handle_development_recall(self, frame: Mapping[str, Any]) -> None:
        with self._organ_guard:
            opportunity = self._active_organ_opportunity
            cancel_event = self._organ_cancel_event
            provider = self._development_recall
            revoked = self._organ_revoked
        pending = self._pending_rehearsal
        expected_fields = {
            "protocol",
            "kind",
            "operation",
            "boot_session",
            "root",
            "opportunity_id",
            "bound_head",
            "sequence",
            "before_sequence",
            "limit",
        }
        if (
            opportunity is None
            or cancel_event is None
            or provider is None
            or revoked
            or pending is None
            or pending[0] != "offer_development"
            or set(frame) != expected_fields
            or frame.get("protocol") != BODY_LINEAGE_PROTOCOL
            or frame.get("kind") != "development_recall"
            or frame.get("operation") != "recall_experiences"
            or frame.get("boot_session") != self.boot.boot_session
            or frame.get("root") != self.boot.root
            or frame.get("opportunity_id") != opportunity.id
            or frame.get("bound_head") != self.boot.head
            or frame.get("sequence") != pending[1]
        ):
            raise BodyBootError("private development recall is invalid")
        before_sequence, limit = self._valid_development_recall_request(
            before_sequence=frame.get("before_sequence"),
            limit=frame.get("limit"),
        )
        self._remaining_development_timeout()
        self.assert_bound()
        common = {
            "protocol": BODY_LINEAGE_PROTOCOL,
            "kind": "development_recall_result",
            "operation": "recall_experiences",
            "boot_session": self.boot.boot_session,
            "root": self.boot.root,
            "opportunity_id": opportunity.id,
            "bound_head": self.boot.head,
            "sequence": pending[1],
            "before_sequence": before_sequence,
            "limit": limit,
        }
        try:
            experiences, has_more = provider(
                opportunity=opportunity,
                before_sequence=before_sequence,
                limit=limit,
                cancel_event=cancel_event,
            )
        except DevelopmentExecutorError as error:
            self._write_frame(
                {**common, "failure": error.code},
                timeout_seconds=self._remaining_development_timeout(),
            )
            return
        except Exception:
            self._write_frame(
                {**common, "failure": "development_recall_unavailable"},
                timeout_seconds=self._remaining_development_timeout(),
            )
            return

        self.assert_bound()
        with self._organ_guard:
            late = (
                self._active_organ_opportunity is not opportunity
                or self._organ_revoked
                or cancel_event.is_set()
            )
        if late:
            raise BodyBootError("late development recall response rejected")
        if not isinstance(experiences, list) or not isinstance(has_more, bool):
            self._write_frame(
                {**common, "failure": "development_recall_unavailable"},
                timeout_seconds=self._remaining_development_timeout(),
            )
            return
        try:
            event_refs = tuple(
                _development_recall_event_ref(experience)
                for experience in experiences
            )
            trace = RecallTrace(
                before_sequence=before_sequence,
                limit=limit,
                returned_event_refs=event_refs,
                has_more=has_more,
            )
        except ValueError:
            self._write_frame(
                {**common, "failure": "development_recall_unavailable"},
                timeout_seconds=self._remaining_development_timeout(),
            )
            return
        if not self._append_development_recall_trace(trace):
            self._write_frame(
                {**common, "failure": "development_recall_trace_oversize"},
                timeout_seconds=self._remaining_development_timeout(),
            )
            return
        self._write_frame(
            {
                **common,
                "experiences": [dict(experience) for experience in experiences],
                "has_more": has_more,
            },
            timeout_seconds=self._remaining_development_timeout(),
        )

    def _handle_organ_invocation(self, frame: Mapping[str, Any]) -> None:
        with self._organ_guard:
            opportunity = self._active_organ_opportunity
            cancel_event = self._organ_cancel_event
            broker = self._organ_broker
            revoked = self._organ_revoked
        pending = self._pending_rehearsal
        expected_fields = {
            "protocol",
            "kind",
            "operation",
            "boot_session",
            "opportunity_id",
            "bound_head",
            "sequence",
            "prompt",
        }
        if (
            opportunity is None
            or cancel_event is None
            or broker is None
            or revoked
            or pending is None
            or pending[0] != "offer_development"
            or set(frame) != expected_fields
            or frame.get("protocol") != BODY_LINEAGE_PROTOCOL
            or frame.get("kind") != "invoke_organ"
            or frame.get("operation") != "invoke_organ"
        ):
            raise BodyBootError("private organ invocation is invalid")
        try:
            request = OrganInvocationRequest.from_mapping(
                {
                    key: frame[key]
                    for key in (
                        "boot_session",
                        "opportunity_id",
                        "bound_head",
                        "sequence",
                        "prompt",
                    )
                }
            )
        except (KeyError, ValueError) as error:
            raise BodyBootError("private organ invocation is invalid") from error
        if (
            request.boot_session != self.boot.boot_session
            or request.opportunity_id != opportunity.id
            or request.bound_head != self.boot.head
            or request.sequence != pending[1]
        ):
            raise BodyBootError("private organ invocation lost its binding")
        self._remaining_development_timeout()
        with self._organ_guard:
            if self._organ_revoked:
                raise BodyBootError("private organ invocation is invalid")
            self._organ_invocation_seen = True
        self.assert_bound()
        try:
            response = broker.invoke(request, cancel_event)
        except Exception:
            response = OrganResponse(failure="organ_invocation_failed")
        if not isinstance(response, OrganResponse):
            raise BodyBootError("private organ broker returned an invalid response")
        try:
            self.assert_bound()
        except BodyBootError:
            broker.cancel(opportunity.id)
            raise
        with self._organ_guard:
            prior_response = self._organ_response
            late = self._organ_revoked or cancel_event.is_set()
            if not late and prior_response is not None and (
                response.organ_call_ref != prior_response.organ_call_ref
                or response.recall_trace[: len(prior_response.recall_trace)]
                != prior_response.recall_trace
            ):
                late = True
            if not late and _recall_trace_wire_bytes(
                (
                    *self._development_direct_recall_trace,
                    *response.recall_trace,
                )
            ) > MAX_DEVELOPMENT_RECALL_TRACE_BYTES:
                response = OrganResponse(
                    failure="organ_recall_trace_oversize",
                    organ_call_ref=(
                        response.organ_call_ref
                        if prior_response is None
                        else prior_response.organ_call_ref
                    ),
                    recall_trace=(
                        ()
                        if prior_response is None
                        else prior_response.recall_trace
                    ),
                )
            if not late:
                self._organ_response = response
        if late:
            broker.cancel(opportunity.id)
            raise BodyBootError("late organ response rejected")
        self._write_frame(
            {
                "protocol": BODY_LINEAGE_PROTOCOL,
                "kind": "organ_response",
                "operation": "invoke_organ",
                "boot_session": request.boot_session,
                "opportunity_id": request.opportunity_id,
                "bound_head": request.bound_head,
                "sequence": request.sequence,
                **response.to_mapping(),
            },
            timeout_seconds=self._remaining_development_timeout(),
        )

    def _dispatch_private_channel(self) -> None:
        try:
            while True:
                frame = read_private_frame(self._private_reader)
                kind = frame.get("kind")
                if kind == "development_recall":
                    self._handle_development_recall(frame)
                    continue
                if kind == "invoke_organ":
                    self._handle_organ_invocation(frame)
                    continue
                if kind == "lineage_request":
                    response = self._handle_lineage_request(frame)
                    if self._pending_rehearsal is not None and (
                        self._pending_lineage_request
                        == self._pending_rehearsal
                        or self._pending_rehearsal[0] == "offer_development"
                    ):
                        self._pending_lineage_response = response
                    self._write_frame(
                        response,
                        timeout_seconds=self._request_timeout_seconds,
                    )
                    continue
                if kind == "rehearsal_result":
                    sequence = frame.get("sequence")
                    operation = frame.get("operation")
                    if (
                        self._pending_rehearsal is None
                        or not isinstance(sequence, int)
                        or isinstance(sequence, bool)
                        or not isinstance(operation, str)
                        or (operation, sequence) != self._pending_rehearsal
                    ):
                        raise BodyBootError(
                            "unsolicited private rehearsal result"
                        )
                    if operation != "submit_successor" and (
                        self._pending_lineage_request != self._pending_rehearsal
                        or self._pending_lineage_response is None
                        or frame != _rehearsal_result_for_response(
                            self._pending_lineage_response
                        )
                    ):
                        raise BodyBootError("private rehearsal result is invalid")
                    if operation == "submit_successor" and not _is_surface_submission_result(frame):
                        raise BodyBootError("private surface submission result is invalid")
                    self._publish_rehearsal_outcome(frame)
                    continue
                if kind == "development_result":
                    with self._organ_guard:
                        organ_response = self._organ_response
                        organ_result_invalidated = self._organ_result_invalidated
                    if not _is_development_result(
                        frame,
                        pending=self._pending_rehearsal,
                        last_lineage_response=self._pending_lineage_response,
                        organ_response=organ_response,
                        organ_result_invalidated=organ_result_invalidated,
                    ):
                        raise BodyBootError("private development result is invalid")
                    self._publish_rehearsal_outcome(frame)
                    continue
                raise BodyBootError("private Body sent an unknown frame")
        except Exception as exc:
            failure = (
                exc
                if isinstance(exc, BodyBootError)
                else BodyBootError("private Body dispatcher failed closed")
            )
            self._revoke_organ_opportunity()
            self._publish_rehearsal_outcome(failure)
            _kill_process_if_alive(self._process)

    def _handle_lineage_request(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        operation = request.get("operation")
        common = {
            "protocol": BODY_LINEAGE_PROTOCOL,
            "kind": "lineage_response",
            "boot_session": self.boot.boot_session,
            "sequence": request.get("sequence"),
            "operation": operation,
        }
        sequence = request.get("sequence")
        if (
            request.get("protocol") != BODY_LINEAGE_PROTOCOL
            or request.get("kind") != "lineage_request"
            or request.get("boot_session") != self.boot.boot_session
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence != self._next_lineage_sequence
        ):
            raise BodyBootError("private lineage request lost its channel binding")
        self._next_lineage_sequence += 1
        self._last_lineage_sequence = int(sequence)

        try:
            if operation == "prepare_successor":
                surface_submission = "execution_surface" in request
                descriptor_supplied = (
                    "development_kind" in request
                    or "development_artifact" in request
                )
                expected = {
                    "protocol",
                    "kind",
                    "boot_session",
                    "sequence",
                    "operation",
                    "files",
                    "activation_kind",
                    "activation_artifact",
                    "causation_ref",
                } | (
                    {"execution_surface", "session_id", "expected_head"}
                    if surface_submission else set()
                ) | (
                    {"development_kind", "development_artifact"}
                    if descriptor_supplied and not surface_submission else set()
                )
                if set(request) != expected:
                    raise BodyBootError(
                        "private prepare request has unexpected fields"
                    )
                files = request.get("files")
                if not isinstance(files, dict) or any(
                    not isinstance(path, str)
                    or not path
                    or not isinstance(value, str)
                    for path, value in files.items()
                ):
                    raise BodyBootError("private prepare request has invalid files")
                activation_kind = request.get("activation_kind")
                activation_artifact = request.get("activation_artifact")
                for value in (activation_kind, activation_artifact):
                    if value is not None and (
                        not isinstance(value, str) or not value
                    ):
                        raise BodyBootError(
                            "private prepare request has invalid activation"
                        )
                try:
                    causation_ref = _private_lineage_optional_text(
                        request.get("causation_ref"),
                        "causation_ref",
                    )
                except ValueError as exc:
                    raise BodyBootError(
                        "private prepare request has invalid causation_ref"
                    ) from exc
                if descriptor_supplied:
                    try:
                        development_kind, development_artifact = (
                            _private_development_descriptor(
                                request.get("development_kind"),
                                request.get("development_artifact"),
                            )
                        )
                    except ValueError as exc:
                        raise BodyBootError(
                            "private prepare request has invalid development descriptor"
                        ) from exc
                else:
                    development_kind = DEVELOPMENT_DESCRIPTOR_UNSET
                    development_artifact = DEVELOPMENT_DESCRIPTOR_UNSET
                if surface_submission:
                    binding = _surface_submission_binding(request)
                    candidate = self._session._prepare_surface_successor(
                        files=files,
                        activation_kind=activation_kind,
                        activation_artifact=activation_artifact,
                        causation_ref=causation_ref,
                        **binding,
                    )
                else:
                    self._bind_request_to_pending_rehearsal(
                        operation="prepare_successor",
                        sequence=sequence,
                    )
                    candidate = self._session.prepare_successor(
                        files=files,
                        activation_kind=activation_kind,
                        activation_artifact=activation_artifact,
                        development_kind=development_kind,
                        development_artifact=development_artifact,
                        causation_ref=causation_ref,
                    )
                return {**common, "ok": True, "candidate_head": candidate}

            if operation == "advance_head":
                surface_submission = "execution_surface" in request
                if set(request) != {
                    "protocol",
                    "kind",
                    "boot_session",
                    "sequence",
                    "operation",
                    "candidate_head",
                } | (
                    {"execution_surface", "session_id", "expected_head"}
                    if surface_submission else set()
                ):
                    raise BodyBootError(
                        "private advance request has unexpected fields"
                    )
                candidate_head = request.get("candidate_head")
                if not isinstance(candidate_head, str) or not candidate_head:
                    raise BodyBootError(
                        "private advance request has an invalid candidate"
                    )
                if surface_submission:
                    binding = _surface_submission_binding(request)
                    status = self._session._advance_surface_head(
                        candidate_head=candidate_head,
                        **binding,
                    )
                else:
                    self._bind_request_to_pending_rehearsal(
                        operation="advance_head",
                        sequence=sequence,
                    )
                    status = self._session.advance_head(
                        candidate_head=candidate_head
                    )
                return {
                    **common,
                    "ok": True,
                    "head": status.head,
                    "generation": status.generation,
                    "authority": status.authority,
                }
            raise BodyBootError("private lineage operation is not allowed")
        except BodyBootError:
            raise
        except AgenticEvoError:
            return {**common, "ok": False, "error": "lineage_request_rejected"}

    def _bind_request_to_pending_rehearsal(
        self,
        *,
        operation: str,
        sequence: int,
    ) -> None:
        pending = self._pending_rehearsal
        if pending is None:
            return
        if pending[0] == "offer_development":
            if operation not in {"prepare_successor", "advance_head"}:
                raise BodyBootError(
                    "development Body attempted an unsupported lineage operation"
                )
            self._pending_lineage_request = (operation, sequence)
            return
        if pending != (operation, sequence):
            raise BodyBootError(
                "private lineage request does not match its rehearsal command"
            )
        self._pending_lineage_request = (operation, sequence)

    def _write_frame(
        self,
        value: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> None:
        _write_private_frame_with_timeout(
            self._private_writer,
            value,
            timeout_seconds=timeout_seconds,
            process=self._process,
            write_lock=self._write_guard,
        )

    def _publish_rehearsal_outcome(
        self,
        outcome: dict[str, Any] | BaseException,
    ) -> None:
        try:
            self._rehearsal_outcomes.put_nowait(outcome)
        except Full:
            pass

    def _retire(self) -> None:
        with self._guard:
            if self._closed.is_set():
                return
            try:
                self._private_writer.close()
                self._private_reader.close()
                if self._process_fence is not None:
                    self._process_fence.close()
                close_process = getattr(self._process, "close", None)
                if close_process is not None:
                    close_process()
                self._session.close()
                if self._lpac_staging is not None:
                    self._lpac_staging.close()
            finally:
                self._closed.set()


def _rehearsal_result_for_response(
    response: Mapping[str, Any],
) -> dict[str, Any]:
    result = {
        "protocol": BODY_LINEAGE_PROTOCOL,
        "kind": "rehearsal_result",
        "operation": response.get("operation"),
        "sequence": response.get("sequence"),
        "ok": response.get("ok"),
    }
    if response.get("ok") is False:
        return {**result, "error": response.get("error")}
    if response.get("operation") == "prepare_successor":
        return {**result, "candidate_head": response.get("candidate_head")}
    return {
        **result,
        "head": response.get("head"),
        "generation": response.get("generation"),
        "authority": response.get("authority"),
    }


def _surface_submission_binding(request: Mapping[str, Any]) -> dict[str, str]:
    binding = {
        field: request.get(field)
        for field in ("execution_surface", "session_id", "expected_head")
    }
    if any(not isinstance(value, str) or not value for value in binding.values()):
        raise BodyBootError("surface submission has an invalid binding")
    return {field: str(value) for field, value in binding.items()}


def _is_surface_submission_result(frame: Mapping[str, Any]) -> bool:
    return set(frame) == {
        "protocol",
        "kind",
        "operation",
        "sequence",
        "ok",
        "head",
        "generation",
        "authority",
    } and (
        frame.get("protocol") == BODY_LINEAGE_PROTOCOL
        and frame.get("kind") == "rehearsal_result"
        and frame.get("operation") == "submit_successor"
        and frame.get("ok") is True
        and isinstance(frame.get("head"), str)
        and isinstance(frame.get("generation"), int)
        and not isinstance(frame.get("generation"), bool)
        and isinstance(frame.get("authority"), str)
    )


def _development_result_from_frame(
    frame: Mapping[str, Any],
    *,
    opportunity: DevelopmentOpportunity,
    organ_response: OrganResponse | None,
    organ_result_invalidated: bool,
    direct_recall_trace: tuple[RecallTrace, ...] = (),
) -> BodyActionResult:
    required = {"protocol", "kind", "operation", "sequence"}
    if not required.issubset(frame):
        raise BodyBootError("private development result is malformed")
    if (
        frame.get("protocol") != BODY_LINEAGE_PROTOCOL
        or frame.get("kind") != "development_result"
        or frame.get("operation") != "offer_development"
        or not isinstance(frame.get("sequence"), int)
        or isinstance(frame.get("sequence"), bool)
    ):
        raise BodyBootError("private development result is malformed")
    try:
        result = BodyActionResult.from_mapping(
            {
                key: value
                for key, value in frame.items()
                if key not in required
            }
        )
    except DevelopmentExecutorError as error:
        raise BodyBootError("private development result is malformed") from error
    if (
        result.opportunity_id != opportunity.id
        or result.current_body_ref != opportunity.current_body_ref
    ):
        raise BodyBootError("private development result lost its opportunity binding")
    if organ_result_invalidated or not _has_host_organ_metadata(
        result,
        organ_response,
    ):
        raise BodyBootError("private development result altered host organ metadata")
    combined_trace = (*direct_recall_trace, *result.recall_trace)
    if _recall_trace_wire_bytes(combined_trace) > MAX_DEVELOPMENT_RECALL_TRACE_BYTES:
        raise BodyBootError("private development result recall trace is oversized")
    return replace(result, recall_trace=combined_trace)


def _is_development_result(
    frame: Mapping[str, Any],
    *,
    pending: tuple[str, int] | None,
    last_lineage_response: Mapping[str, Any] | None,
    organ_response: OrganResponse | None,
    organ_result_invalidated: bool,
) -> bool:
    if (
        pending is None
        or pending[0] != "offer_development"
        or frame.get("sequence") != pending[1]
    ):
        return False
    try:
        result = BodyActionResult.from_mapping(
            {
                key: value
                for key, value in frame.items()
                if key not in {"protocol", "kind", "operation", "sequence"}
            }
        )
    except DevelopmentExecutorError:
        return False
    if (
        frame.get("protocol") != BODY_LINEAGE_PROTOCOL
        or frame.get("kind") != "development_result"
        or frame.get("operation") != "offer_development"
    ):
        return False
    if organ_result_invalidated or not _has_host_organ_metadata(
        result,
        organ_response,
    ):
        return False
    if result.action == "candidate_submitted":
        return (
            last_lineage_response is not None
            and last_lineage_response.get("operation") == "advance_head"
            and last_lineage_response.get("ok") is True
            and last_lineage_response.get("head") == result.candidate_head
            and last_lineage_response.get("generation") == result.generation
        )
    if result.action in {"no_change", "retain", "withdraw", "request_later"}:
        return last_lineage_response is None
    return result.action == "failed"


def _has_host_organ_metadata(
    result: BodyActionResult,
    organ_response: OrganResponse | None,
) -> bool:
    if organ_response is None:
        return result.organ_call_ref is None and not result.recall_trace
    return (
        result.organ_call_ref == organ_response.organ_call_ref
        and result.recall_trace == organ_response.recall_trace
    )


class BodyProcessSupervisor:
    """Spawns one exact Current Body on its required native boundary."""

    def __init__(
        self,
        runtime: DevelopmentalRuntime,
        witness: WitnessCore,
        *,
        ready_timeout_seconds: float = 5.0,
        request_timeout_seconds: float | None = None,
        organ_broker: OrganInvoker | None = None,
        development_recall: DevelopmentRecallProvider | None = None,
        development_deadline: float | None = None,
    ) -> None:
        if ready_timeout_seconds <= 0:
            raise ValueError("ready_timeout_seconds must be positive")
        if request_timeout_seconds is not None and request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if development_deadline is not None and development_deadline <= 0:
            raise ValueError("development_deadline must be positive")
        self.runtime = runtime
        self.witness = witness
        self.ready_timeout_seconds = ready_timeout_seconds
        self.request_timeout_seconds = max(
            ready_timeout_seconds,
            5.0,
        ) if request_timeout_seconds is None else request_timeout_seconds
        self.organ_broker = organ_broker
        self.development_recall = development_recall
        self.development_deadline = development_deadline

    def spawn_current(self) -> SpawnedBodyProcess:
        status = self.runtime.status()
        session: CurrentBodySession | None = None
        process: Any | None = None
        private_reader: BinaryIO | None = None
        private_writer: BinaryIO | None = None
        process_fence: Any | None = None
        lpac_profile: Any | None = None
        lpac_staging: Any | None = None
        body_read_fd: int | None = None
        body_write_fd: int | None = None
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
            if manifest.development_kind is not None and sys.platform != "win32":
                raise BodyBootError(
                    "executable Body development requires the Windows LPAC launch path"
                )
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
                development_kind=manifest.development_kind,
                development_artifact=manifest.development_artifact,
            )
            if sys.platform == "win32":
                process_fence = KillOnCloseJob()
                body_read_fd, witness_write_fd = os.pipe()
                witness_read_fd, body_write_fd = os.pipe()
                private_writer = os.fdopen(witness_write_fd, "wb", buffering=0)
                private_reader = os.fdopen(witness_read_fd, "rb", buffering=0)
                body_read_handle = msvcrt.get_osfhandle(body_read_fd)
                body_write_handle = msvcrt.get_osfhandle(body_write_fd)
                os.set_handle_inheritable(body_read_handle, True)
                os.set_handle_inheritable(body_write_handle, True)
                if manifest.development_kind is None:
                    command: tuple[str, ...] = (
                        sys.executable,
                        "-P",
                        "-m",
                        "agentic_evo.body_worker",
                        str(body_read_handle),
                        str(body_write_handle),
                    )
                    process = spawn_restricted_suspended_process(
                        command,
                        inherited_handles=(body_read_handle, body_write_handle),
                        cwd=Path(sys.executable).resolve().parent,
                        environment=_body_worker_environment(),
                    )
                    body_channel = "windows_explicit_handle_list_pipe_pair"
                    process_token = "windows_restricted_low_integrity"
                else:
                    from .body_lpac import is_lpac_body_runtime_available

                    if not is_lpac_body_runtime_available():
                        raise BodyBootError(
                            "executable Body development requires the Windows LPAC launch path"
                        )
                    from .body_lpac import (
                        BODY_LPAC_CAPABILITY_NAMES,
                        create_ephemeral_lpac_profile,
                        lpac_body_capability_sids,
                        stage_lpac_body_payload,
                    )
                    from .windows_appcontainer import spawn_lpac_suspended_process

                    lpac_profile = create_ephemeral_lpac_profile()
                    lpac_staging = stage_lpac_body_payload(
                        lpac_profile,
                        staging_parent=self.runtime.home / ".lpac-body-staging",
                    )
                    expected_capability_sids = lpac_body_capability_sids()
                    command = (
                        str(lpac_staging.python_executable),
                        "-P",
                        "-S",
                        "-m",
                        "agentic_evo.body_worker",
                        str(body_read_handle),
                        str(body_write_handle),
                    )
                    process = spawn_lpac_suspended_process(
                        command,
                        inherited_handles=(body_read_handle, body_write_handle),
                        cwd=lpac_staging.payload_root,
                        environment=_lpac_body_worker_environment(
                            payload_root=lpac_staging.payload_root,
                            scratch_path=lpac_staging.scratch_path,
                        ),
                        appcontainer_sid=lpac_profile.sid,
                        capability_names=BODY_LPAC_CAPABILITY_NAMES,
                    )
                    _require_lpac_token_contract(
                        process,
                        expected_appcontainer_sid=lpac_profile.sid_string,
                        expected_capability_sids=expected_capability_sids,
                    )
                    body_channel = "windows_lpac_explicit_handle_list_pipe_pair"
                    process_token = "windows_lpac_registry_read"
                process_fence.assign_handle(process.process_handle)
                os.close(body_read_fd)
                body_read_fd = None
                os.close(body_write_fd)
                body_write_fd = None
                process.resume()
            else:
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
                    start_new_session=True,
                )
                if process.stdin is None or process.stdout is None:
                    raise BodyBootError("Body subprocess pipes were not created")
                private_writer = process.stdin
                private_reader = process.stdout
                body_channel = "subprocess_stdio_pipe_pair"
                process_token = "ordinary_subprocess_rehearsal"
            if private_writer is None or private_reader is None:
                raise BodyBootError("Body subprocess pipes were not created")
            ready_deadline = monotonic() + self.ready_timeout_seconds
            _write_private_frame_with_timeout(
                private_writer,
                asdict(boot),
                timeout_seconds=max(ready_deadline - monotonic(), 0.0),
                process=process,
                max_bytes=None,
            )
            response = _read_private_frame_with_timeout(
                private_reader,
                timeout_seconds=max(ready_deadline - monotonic(), 0.0),
            )
            ready = ready_echo_from_mapping(response)
            validate_ready_echo(boot, ready)
            if process.poll() is not None:
                raise BodyBootError("Body subprocess exited before becoming ready")
            self.witness._authorize(session)
            return SpawnedBodyProcess(
                process=process,
                private_reader=private_reader,
                private_writer=private_writer,
                body_channel=body_channel,
                process_token=process_token,
                command=command,
                boot=boot,
                ready=ready,
                session=session,
                witness=self.witness,
                process_fence=process_fence,
                lpac_staging=lpac_staging,
                request_timeout_seconds=self.request_timeout_seconds,
                organ_broker=self.organ_broker,
                development_recall=self.development_recall,
                development_deadline=self.development_deadline,
            )
        except Exception as exc:
            if process is not None:
                _terminate_process(
                    process,
                    private_reader=private_reader,
                    private_writer=private_writer,
                )
                private_reader = None
                private_writer = None
            else:
                if private_writer is not None:
                    private_writer.close()
                if private_reader is not None:
                    private_reader.close()
            if body_read_fd is not None:
                os.close(body_read_fd)
            if body_write_fd is not None:
                os.close(body_write_fd)
            if session is not None:
                session.close()
            if process_fence is not None:
                process_fence.close()
            if lpac_staging is not None:
                try:
                    lpac_staging.close()
                except Exception:
                    pass
            elif lpac_profile is not None:
                try:
                    lpac_profile.close()
                except Exception:
                    pass
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


def _terminate_process(
    process: Any,
    *,
    private_reader: BinaryIO | None = None,
    private_writer: BinaryIO | None = None,
) -> None:
    try:
        alive = process.poll() is None
    except OSError:
        alive = False
    if alive:
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
    if private_writer is not None:
        try:
            private_writer.close()
        except (OSError, ValueError):
            pass
    if private_reader is not None:
        try:
            private_reader.close()
        except (OSError, ValueError):
            pass
    close_process = getattr(process, "close", None)
    if close_process is not None:
        try:
            close_process()
        except OSError:
            pass


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


def _lpac_body_worker_environment(
    *,
    payload_root: Path,
    scratch_path: Path,
) -> dict[str, str]:
    """Build the only environment inherited by a staged LPAC Body worker."""

    payload_text = str(payload_root)
    scratch_text = str(scratch_path)
    environment = {
        "PYTHONPATH": payload_text,
        "PYTHONUTF8": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LOCALAPPDATA": scratch_text,
        "TEMP": scratch_text,
        "TMP": scratch_text,
    }
    for name in ("SystemRoot", "WINDIR"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _require_lpac_token_contract(
    process: Any,
    *,
    expected_appcontainer_sid: str,
    expected_capability_sids: tuple[str, ...],
) -> None:
    """Fail closed unless the suspended child has exactly the expected token."""

    token_profile = getattr(process, "token_profile", None)
    actual_capability_sids = getattr(token_profile, "capability_sids", None)
    if (
        not isinstance(expected_appcontainer_sid, str)
        or not expected_appcontainer_sid
        or not isinstance(expected_capability_sids, tuple)
        or not expected_capability_sids
        or len(expected_capability_sids) != 1
        or any(
            not isinstance(sid, str) or not sid
            for sid in expected_capability_sids
        )
        or len(set(expected_capability_sids)) != len(expected_capability_sids)
        or token_profile is None
        or token_profile.is_app_container is not True
        or token_profile.is_less_privileged_app_container is not True
        or token_profile.appcontainer_sid != expected_appcontainer_sid
        or token_profile.capability_count != len(expected_capability_sids)
        or not isinstance(actual_capability_sids, tuple)
        or actual_capability_sids != expected_capability_sids
    ):
        raise BodyBootError(
            "LPAC child token does not match the fixed registryRead contract"
        )
