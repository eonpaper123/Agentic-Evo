from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import secrets
import subprocess
import sys
import threading
from time import monotonic
from typing import Any, BinaryIO, Mapping
from uuid import uuid4

from ._util import canonical_json_bytes, sha256_hex
from .body import BODY_SCHEMA_VERSION
from .errors import AgenticEvoError
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
        if process.poll() is None:
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
        request_timeout_seconds: float,
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
        self._request_timeout_seconds = request_timeout_seconds
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
        result = self._run_rehearsal(
            {
                "protocol": BODY_LINEAGE_PROTOCOL,
                "kind": "rehearsal_command",
                "operation": "prepare_successor",
                "files": normalized_files,
                "activation_kind": activation_kind,
                "activation_artifact": activation_artifact,
            }
        )
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

    def close(self) -> None:
        if self._closed.is_set():
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

    def _monitor(self) -> None:
        try:
            self._process.wait()
        except OSError:
            pass
        finally:
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
            deadline = monotonic() + self._request_timeout_seconds
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
                self._pending_rehearsal = None
                self._pending_lineage_request = None
                self._pending_lineage_response = None

    def _dispatch_private_channel(self) -> None:
        try:
            while True:
                frame = read_private_frame(self._private_reader)
                kind = frame.get("kind")
                if kind == "lineage_request":
                    response = self._handle_lineage_request(frame)
                    if (
                        self._pending_lineage_request
                        == self._pending_rehearsal
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
                        or self._pending_lineage_request
                        != self._pending_rehearsal
                        or not isinstance(sequence, int)
                        or isinstance(sequence, bool)
                        or not isinstance(operation, str)
                        or (operation, sequence)
                        != self._pending_rehearsal
                        or self._pending_lineage_response is None
                        or frame
                        != _rehearsal_result_for_response(
                            self._pending_lineage_response
                        )
                    ):
                        raise BodyBootError(
                            "unsolicited private rehearsal result"
                        )
                    self._publish_rehearsal_outcome(frame)
                    continue
                raise BodyBootError("private Body sent an unknown frame")
        except Exception as exc:
            failure = (
                exc
                if isinstance(exc, BodyBootError)
                else BodyBootError("private Body dispatcher failed closed")
            )
            self._publish_rehearsal_outcome(failure)
            try:
                if self._process.poll() is None:
                    self._process.kill()
            except OSError:
                pass

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
                if set(request) != {
                    "protocol",
                    "kind",
                    "boot_session",
                    "sequence",
                    "operation",
                    "files",
                    "activation_kind",
                    "activation_artifact",
                }:
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
                self._bind_request_to_pending_rehearsal(
                    operation="prepare_successor",
                    sequence=sequence,
                )
                candidate = self._session.prepare_successor(
                    files=files,
                    activation_kind=activation_kind,
                    activation_artifact=activation_artifact,
                )
                return {**common, "ok": True, "candidate_head": candidate}

            if operation == "advance_head":
                if set(request) != {
                    "protocol",
                    "kind",
                    "boot_session",
                    "sequence",
                    "operation",
                    "candidate_head",
                }:
                    raise BodyBootError(
                        "private advance request has unexpected fields"
                    )
                candidate_head = request.get("candidate_head")
                if not isinstance(candidate_head, str) or not candidate_head:
                    raise BodyBootError(
                        "private advance request has an invalid candidate"
                    )
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
        if pending is not None and pending != (operation, sequence):
            raise BodyBootError(
                "private lineage request does not match its rehearsal command"
            )
        if pending is not None:
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
        process: Any | None = None
        private_reader: BinaryIO | None = None
        private_writer: BinaryIO | None = None
        process_fence: Any | None = None
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
            command: tuple[str, ...] = (
                sys.executable,
                "-P",
                "-m",
                "agentic_evo.body_worker",
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
                command += (str(body_read_handle), str(body_write_handle))
                process = spawn_restricted_suspended_process(
                    command,
                    inherited_handles=(body_read_handle, body_write_handle),
                    cwd=Path(sys.executable).resolve().parent,
                    environment=_body_worker_environment(),
                )
                process_fence.assign_handle(process.process_handle)
                os.close(body_read_fd)
                body_read_fd = None
                os.close(body_write_fd)
                body_write_fd = None
                process.resume()
                body_channel = "windows_explicit_handle_list_pipe_pair"
                process_token = "windows_restricted_low_integrity"
            else:
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
                request_timeout_seconds=max(self.ready_timeout_seconds, 5.0),
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
