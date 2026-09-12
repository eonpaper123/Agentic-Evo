from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from multiprocessing.connection import Connection, Listener
from pathlib import Path
import socket
import sys
import threading
from typing import Any, Mapping
from uuid import uuid4

from ._util import ExclusiveFileLock, canonical_json_bytes
from .body_process import BodyProcessSupervisor, SpawnedBodyProcess
from .development_executor import (
    BodyActionResult,
    CurrentBodyLineageFacts,
    DevelopmentExecutor,
    DevelopmentExecutorError,
    DevelopmentOpportunity,
)
from .errors import AgenticEvoError, IntegrityError, RuntimeOffError
from .ipc import (
    CONTROL_PROTOCOL,
    PUBLIC_PROTOCOL,
    PUBLIC_IO_TIMEOUT_SECONDS,
    InvalidPublicFrame,
    control_endpoint,
    receive_public_message,
    send_public_message,
    service_endpoint,
)
from .runtime import DevelopmentalRuntime, RuntimeStatus, WakeState
from .witness import WitnessCore
from .version import VERSION


_MAX_PUBLIC_TEXT_BYTES = 1024
_MAX_PUBLIC_BODY_FILES = 16
_MAX_PUBLIC_ACTIVE_SESSIONS = 32
_MAX_PUBLIC_BODY_FILE_LIST_BYTES = 8 * 1024
_MAX_PUBLIC_SESSION_LIST_BYTES = 24 * 1024
_MAX_PUBLIC_CONTEXT_JSON_BYTES = 24 * 1024
_MAX_PUBLIC_METADATA_JSON_BYTES = 1024
_MAX_PUBLIC_EXPERIENCES = 12
_MAX_PUBLIC_EXPERIENCE_LIST_BYTES = 48 * 1024
_PRODUCT_CONFIG_NAME = "product.json"


class PublicRequestError(AgenticEvoError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _ManagedConnection:
    """Give one accepted transport exactly one raw close owner."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self._close_guard = threading.Lock()
        self._closed = False

    def close(self) -> None:
        with self._close_guard:
            if self._closed:
                return
            try:
                self.connection.close()
            except (OSError, ValueError):
                pass
            finally:
                self._closed = True


class WitnessService:
    """Foreground rehearsal of one fixed-home Witness public boundary.

    This process is intentionally not described as an independent OS principal.
    It proves protocol separation and singleton supervision only.
    """

    _PUBLIC_PARAMETERS = {
        "status": (frozenset(), frozenset()),
        "stop_service": (frozenset(), frozenset()),
        "wake": (
            frozenset(
                {
                    "execution_surface",
                    "session_id",
                    "project_environment",
                }
            ),
            frozenset({"model"}),
        ),
        "sleep": (
            frozenset({"execution_surface", "session_id"}),
            frozenset(),
        ),
        "observe": (
            frozenset({"event_kind", "payload"}),
            frozenset(
                {
                    "execution_surface",
                    "session_id",
                    "turn_id",
                    "tool_call_id",
                    "project_environment",
                    "occurred_at",
                    "correlation_ref",
                    "causation_ref",
                    "parent_ref",
                    "coverage_gap",
                }
            ),
        ),
        "recall_experiences": (
            frozenset({"execution_surface", "session_id", "limit"}),
            frozenset({"before_sequence"}),
        ),
        "submit_successor": (
            frozenset(
                {
                    "execution_surface",
                    "session_id",
                    "expected_head",
                    "files",
                    "activation_kind",
                    "activation_artifact",
                }
            ),
            frozenset({"causation_ref"}),
        ),
    }
    _MAX_CONCURRENT_CONNECTIONS = 16

    def __init__(self, home: Path) -> None:
        self.home = Path(home).expanduser().resolve(strict=False)
        self.runtime = DevelopmentalRuntime.load(self.home)
        self.endpoint = service_endpoint(self.home)
        self.control_endpoint = control_endpoint(self.home)
        self.witness = WitnessCore(self.runtime)
        self.body_supervisor = BodyProcessSupervisor(
            self.runtime,
            self.witness,
        )
        self._body: SpawnedBodyProcess | None = None
        self._body_guard = threading.RLock()
        self._connection_slots = threading.BoundedSemaphore(
            self._MAX_CONCURRENT_CONNECTIONS
        )
        self._lifecycle_guard = threading.RLock()
        self._quiescent = threading.Condition(self._lifecycle_guard)
        self._stop_requested = threading.Event()
        self._active_dispatches = 0
        self._active_connections: dict[int, _ManagedConnection] = {}
        self._connection_workers: set[threading.Thread] = set()
        self._public_listener: Any | None = None
        self._control_listener: Listener | None = None
        self._control_thread: threading.Thread | None = None
        self._development_guard = threading.RLock()
        self._development_executor: DevelopmentExecutor | None = None
        self._development_thread: threading.Thread | None = None
        self._pending_development: tuple[str, int] | None = None
        self._development_timer: threading.Timer | None = None
        self._development_blocked = False
        self._last_development: dict[str, Any] | None = None
        self._development_organ_argv = self._load_development_organ_argv()

    def request_stop(self) -> None:
        """Ask the supervisor loop to stop without changing runtime authority."""

        with self._lifecycle_guard:
            self._stop_requested.set()
            public_listener = self._public_listener
            control_listener = self._control_listener
            active_connections = tuple(self._active_connections.values())
        self._cancel_development()
        self._wake_pending_listener(self.endpoint, public_listener)
        self._wake_pending_listener(self.control_endpoint, control_listener)
        if public_listener is not None:
            public_listener.close()
        if control_listener is not None:
            control_listener.close()
        for connection in active_connections:
            connection.close()
        with self._quiescent:
            while self._active_dispatches:
                self._quiescent.wait()

    def serve_forever(self) -> None:
        service_lock = ExclusiveFileLock(
            self.runtime.runtime_path / ".witness-service.lock",
            timeout_seconds=0.0,
        )
        with service_lock:
            self.runtime.detach_persisted_sessions()
            self._remove_stale_unix_endpoint(self.endpoint)
            self._remove_stale_unix_endpoint(self.control_endpoint)
            listener: Any | None = None
            try:
                self._ensure_body()
                self._start_control_listener()
                native_windows = sys.platform == "win32"
                if native_windows:
                    from .windows_pipe import (
                        WindowsPublicPipeListener,
                        current_process_sid,
                    )

                    listener = WindowsPublicPipeListener(
                        self.endpoint.address,
                        expected_sid=current_process_sid(),
                    )
                else:
                    listener = Listener(
                        self.endpoint.address,
                        family=self.endpoint.family,
                        backlog=1,
                        authkey=None,
                    )
                with self._lifecycle_guard:
                    if self._stop_requested.is_set():
                        return
                    self._public_listener = listener
                self._restore_development_opportunity()
                while not self._stop_requested.is_set():
                    try:
                        accepted = listener.accept()
                    except (EOFError, OSError):
                        if self._stop_requested.is_set():
                            break
                        raise
                    connection = (
                        accepted.connection if native_windows else accepted
                    )
                    if not self._register_connection(connection):
                        connection.close()
                        break
                    if native_windows:
                        managed_connection = self._managed_connection(connection)
                        try:
                            self._serve_connection(connection, managed_connection)
                        finally:
                            managed_connection.close()
                            self._unregister_connection(connection)
                        continue
                    if not self._connection_slots.acquire(blocking=False):
                        self._managed_connection(connection).close()
                        self._unregister_connection(connection)
                        continue
                    worker = threading.Thread(
                        target=self._serve_connection_with_deadline,
                        args=(connection,),
                        daemon=True,
                    )
                    with self._lifecycle_guard:
                        self._connection_workers.add(worker)
                    try:
                        worker.start()
                    except BaseException:
                        with self._lifecycle_guard:
                            self._connection_workers.discard(worker)
                        self._managed_connection(connection).close()
                        self._unregister_connection(connection)
                        self._connection_slots.release()
                        raise
            finally:
                self.request_stop()
                with self._lifecycle_guard:
                    if self._public_listener is listener:
                        self._public_listener = None
                    control_listener = self._control_listener
                    self._control_listener = None
                    control_thread = self._control_thread
                    self._control_thread = None
                if listener is not None:
                    listener.close()
                if control_listener is not None:
                    control_listener.close()
                if (
                    control_thread is not None
                    and control_thread is not threading.current_thread()
                ):
                    control_thread.join()
                self._join_connection_workers()
                self._close_body()
                self.witness.close()
                self._remove_stale_unix_endpoint(self.endpoint)
                self._remove_stale_unix_endpoint(self.control_endpoint)

    def dispatch_public(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._begin_dispatch()
        try:
            return self._dispatch_public(request)
        finally:
            self._end_dispatch()

    def _dispatch_public(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._validate_request_envelope(request)
        operation = request["operation"]
        params = request["params"]
        self._validate_parameters(operation, params)
        if operation == "submit_successor":
            self._ensure_body()

        if operation == "status":
            with self._development_guard:
                development_active = self._development_executor is not None
            if not development_active:
                self._ensure_body()
            result = self._project_status(self.runtime.status())
            result["body_rehearsal"] = self._body_description()
            result["development"] = self._development_description()
            return result
        if operation == "stop_service":
            return {"stopping": True, "authority_unchanged": True}
        if operation == "wake":
            return self._project_wake(self.runtime.wake(**params))
        if operation == "sleep":
            result = self._project_status(self.runtime.sleep(**params))
            self._schedule_development(
                reason="task_end",
                after_sequence=self.runtime.trusted.last_event_sequence(),
            )
            return result
        if operation == "observe":
            record = self.runtime.observe(**params)
            return {
                "event_id": record.event_id,
                "sequence": record.sequence,
                "integrity_hash": record.integrity_hash,
            }
        if operation == "recall_experiences":
            experiences, has_more = self.runtime.recall_experiences(**params)
            projected = self._bounded_recent_json_list(
                experiences,
                max_items=params["limit"],
                max_bytes=_MAX_PUBLIC_EXPERIENCE_LIST_BYTES,
            )
            if experiences and not projected:
                raise PublicRequestError(
                    "experience_projection_too_large",
                    "the newest experience exceeds the public response bound",
                )
            return {
                "experiences": projected,
                "has_more": has_more or len(projected) < len(experiences),
                "next_before_sequence": (
                    projected[0]["sequence"]
                    if projected
                    and (has_more or len(projected) < len(experiences))
                    else None
                ),
            }
        if operation == "submit_successor":
            with self._body_guard:
                body = self._body
                if body is None:
                    raise PublicRequestError(
                        "body_unavailable",
                        "Current Body is unavailable",
                    )
                result = body.submit_surface_successor(**params)
            return result
        raise AssertionError("validated public operation has no dispatcher")

    def _serve_connection(
        self,
        connection: Connection,
        managed_connection: _ManagedConnection | None = None,
    ) -> None:
        request_id: str | None = None
        stop_after_response = False
        try:
            request = receive_public_message(
                connection,
                timeout_seconds=PUBLIC_IO_TIMEOUT_SECONDS,
                close_on_timeout=(
                    managed_connection.close
                    if managed_connection is not None
                    else connection.close
                ),
            )
            possible_id = request.get("request_id")
            if isinstance(possible_id, str):
                request_id = possible_id
            result = self.dispatch_public(request)
            stop_after_response = request["operation"] == "stop_service"
            response = {
                "protocol": PUBLIC_PROTOCOL,
                "request_id": request_id,
                "ok": True,
                "result": result,
            }
        except InvalidPublicFrame:
            response = self._error_response(
                request_id,
                "invalid_frame",
                "request is not one bounded UTF-8 JSON object",
            )
        except PublicRequestError as exc:
            response = self._error_response(request_id, exc.code, str(exc))
        except RuntimeOffError:
            response = self._error_response(
                request_id,
                "runtime_off",
                "runtime is off",
            )
        except AgenticEvoError:
            response = self._error_response(
                request_id,
                "operation_failed",
                "trusted runtime rejected the operation",
            )
        except Exception:
            response = self._error_response(
                request_id,
                "internal_error",
                "Witness could not complete the request",
            )

        try:
            send_public_message(connection, response)
        except (InvalidPublicFrame, EOFError, OSError, ValueError):
            pass
        if stop_after_response:
            self.request_stop()

    def _serve_connection_with_deadline(self, connection: Connection) -> None:
        managed_connection = self._managed_connection(connection)
        try:
            self._serve_connection(connection, managed_connection)
        finally:
            managed_connection.close()
            self._unregister_connection(connection)
            with self._lifecycle_guard:
                self._connection_workers.discard(threading.current_thread())
            self._connection_slots.release()

    @classmethod
    def _validate_request_envelope(cls, request: Mapping[str, Any]) -> None:
        if set(request) != {"protocol", "request_id", "operation", "params"}:
            raise PublicRequestError(
                "invalid_request",
                "request envelope has unexpected fields",
            )
        if request.get("protocol") != PUBLIC_PROTOCOL:
            raise PublicRequestError(
                "invalid_protocol",
                "request protocol is unsupported",
            )
        request_id = request.get("request_id")
        if (
            not isinstance(request_id, str)
            or not request_id
            or len(request_id) > 128
        ):
            raise PublicRequestError(
                "invalid_request",
                "request_id must be a bounded non-empty string",
            )
        operation = request.get("operation")
        if not isinstance(operation, str) or operation not in cls._PUBLIC_PARAMETERS:
            raise PublicRequestError(
                "operation_not_public",
                "operation is not available on the public Surface endpoint",
            )
        if not isinstance(request.get("params"), dict):
            raise PublicRequestError(
                "invalid_parameters",
                "params must be one JSON object",
            )

    @classmethod
    def _validate_parameters(
        cls,
        operation: str,
        params: Mapping[str, Any],
    ) -> None:
        required, optional = cls._PUBLIC_PARAMETERS[operation]
        keys = frozenset(params)
        if not required.issubset(keys) or not keys.issubset(required | optional):
            raise PublicRequestError(
                "invalid_parameters",
                "operation parameters do not match the public contract",
            )

        if operation == "submit_successor":
            files = params.get("files")
            if not isinstance(files, dict) or not files or any(
                not isinstance(path, str)
                or not path
                or Path(path).is_absolute()
                or ".." in Path(path).parts
                or not isinstance(value, str)
                for path, value in files.items()
            ):
                raise PublicRequestError(
                    "invalid_parameters",
                    "files must be a non-empty UTF-8 text object",
                )
            string_fields = keys - {"files"}
        else:
            string_fields = keys - {
                "payload",
                "model",
                "limit",
                "before_sequence",
            }
        for field in string_fields:
            value = params[field]
            if value is not None and not isinstance(value, str):
                raise PublicRequestError(
                    "invalid_parameters",
                    f"{field} must be a string or null",
                )
        for field in required - {"payload", "files", "limit"}:
            value = params[field]
            if not isinstance(value, str) or not value:
                raise PublicRequestError(
                    "invalid_parameters",
                    f"{field} must be a non-empty string",
                )
        if "model" in params:
            model = params["model"]
            if model is not None and not isinstance(model, str):
                raise PublicRequestError(
                    "invalid_parameters",
                    "model must be a string or null",
                )
        if "payload" in params and not isinstance(params["payload"], dict):
            raise PublicRequestError(
                "invalid_parameters",
                "payload must be one JSON object",
            )
        if operation == "recall_experiences":
            limit = params.get("limit")
            if (
                not isinstance(limit, int)
                or isinstance(limit, bool)
                or not 1 <= limit <= _MAX_PUBLIC_EXPERIENCES
            ):
                raise PublicRequestError(
                    "invalid_parameters",
                    "limit must be an integer from 1 through 12",
                )
            before_sequence = params.get("before_sequence")
            if before_sequence is not None and (
                not isinstance(before_sequence, int)
                or isinstance(before_sequence, bool)
                or before_sequence < 1
            ):
                raise PublicRequestError(
                    "invalid_parameters",
                    "before_sequence must be a positive integer",
                )
        for field in keys - {"payload"}:
            value = params[field]
            if (
                isinstance(value, str)
                and len(value.encode("utf-8")) > _MAX_PUBLIC_TEXT_BYTES
            ):
                raise PublicRequestError(
                    "invalid_parameters",
                    f"{field} exceeds the public text byte bound",
                )

    @staticmethod
    def _project_status(status: RuntimeStatus) -> dict[str, Any]:
        result = asdict(status)
        result["service_version"] = VERSION
        sessions = [
            asdict(identity)
            for identity in status.active_sessions
        ]
        projected_sessions = WitnessService._bounded_json_list(
            sessions,
            max_items=_MAX_PUBLIC_ACTIVE_SESSIONS,
            max_bytes=_MAX_PUBLIC_SESSION_LIST_BYTES,
        )
        result["active_sessions"] = projected_sessions
        result["active_session_count"] = len(sessions)
        result["active_sessions_truncated"] = (
            len(projected_sessions) < len(sessions)
        )
        for field in ("instrument_version", "protocol_version"):
            value = str(result[field])
            projected, truncated = WitnessService._bounded_json_text(
                value,
                max_bytes=_MAX_PUBLIC_METADATA_JSON_BYTES,
            )
            result[field] = projected
            result[f"{field}_char_count"] = len(value)
            result[f"{field}_truncated"] = truncated
        return result

    @staticmethod
    def _project_wake(wake: WakeState) -> dict[str, Any]:
        result = asdict(wake)
        body_files = list(wake.body_files)
        projected_files = WitnessService._bounded_json_list(
            body_files,
            max_items=_MAX_PUBLIC_BODY_FILES,
            max_bytes=_MAX_PUBLIC_BODY_FILE_LIST_BYTES,
        )
        result["body_files"] = projected_files
        result["body_file_count"] = len(body_files)
        result["body_files_truncated"] = len(projected_files) < len(body_files)
        context = wake.activation_context
        projected_context, context_truncated = WitnessService._bounded_json_text(
            context,
            max_bytes=_MAX_PUBLIC_CONTEXT_JSON_BYTES,
        )
        result["activation_context"] = projected_context
        result["activation_context_char_count"] = len(context)
        result["activation_context_truncated"] = context_truncated
        return result

    @staticmethod
    def _bounded_json_list(
        values: list[Any],
        *,
        max_items: int,
        max_bytes: int,
    ) -> list[Any]:
        projected: list[Any] = []
        for value in values[:max_items]:
            candidate = [*projected, value]
            if len(canonical_json_bytes(candidate)) > max_bytes:
                break
            projected.append(value)
        return projected

    @staticmethod
    def _bounded_recent_json_list(
        values: list[Any],
        *,
        max_items: int,
        max_bytes: int,
    ) -> list[Any]:
        """Keep the newest complete items while returning chronological order."""

        projected: list[Any] = []
        for value in reversed(values[-max_items:]):
            candidate = [value, *projected]
            if len(canonical_json_bytes(candidate)) > max_bytes:
                break
            projected = candidate
        return projected

    @staticmethod
    def _bounded_json_text(value: str, *, max_bytes: int) -> tuple[str, bool]:
        if len(canonical_json_bytes(value)) <= max_bytes:
            return value, False
        lower = 0
        upper = len(value)
        while lower < upper:
            middle = (lower + upper + 1) // 2
            if len(canonical_json_bytes(value[:middle])) <= max_bytes:
                lower = middle
            else:
                upper = middle - 1
        return value[:lower], True

    @staticmethod
    def _error_response(
        request_id: str | None,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "protocol": PUBLIC_PROTOCOL,
            "request_id": request_id,
            "ok": False,
            "error": {"code": code, "message": message},
        }

    def _start_control_listener(self) -> None:
        listener = Listener(
            self.control_endpoint.address,
            family=self.control_endpoint.family,
            backlog=1,
            authkey=None,
        )
        with self._lifecycle_guard:
            if self._stop_requested.is_set():
                listener.close()
                return
            self._control_listener = listener
        thread = threading.Thread(
            target=self._serve_control_loop,
            args=(listener,),
            daemon=True,
        )
        with self._lifecycle_guard:
            self._control_thread = thread
        thread.start()

    def _serve_control_loop(self, listener: Listener) -> None:
        while not self._stop_requested.is_set():
            try:
                connection = listener.accept()
            except (EOFError, OSError):
                return
            if not self._register_connection(connection):
                connection.close()
                return
            try:
                self._serve_control_connection_with_deadline(connection)
            finally:
                self._unregister_connection(connection)

    def _wake_pending_listener(
        self,
        endpoint: Any,
        listener: Any | None,
    ) -> None:
        if listener is None:
            return
        try:
            if sys.platform == "win32":
                from .windows_pipe import connect_windows_public_pipe

                connection = connect_windows_public_pipe(
                    endpoint.address,
                    timeout_seconds=0.25,
                )
            else:
                wake_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                wake_socket.settimeout(0.25)
                try:
                    wake_socket.connect(endpoint.address)
                finally:
                    wake_socket.close()
                return
        except (EOFError, OSError):
            return
        connection.close()

    def _serve_control_connection_with_deadline(
        self,
        connection: Connection,
    ) -> None:
        managed_connection = self._managed_connection(connection)
        try:
            self._serve_control_connection(connection, managed_connection)
        finally:
            managed_connection.close()

    def _serve_control_connection(
        self,
        connection: Connection,
        managed_connection: _ManagedConnection | None = None,
    ) -> None:
        request_id: str | None = None
        try:
            request = receive_public_message(
                connection,
                timeout_seconds=PUBLIC_IO_TIMEOUT_SECONDS,
                close_on_timeout=(
                    managed_connection.close
                    if managed_connection is not None
                    else connection.close
                ),
            )
            possible_id = request.get("request_id")
            if isinstance(possible_id, str):
                request_id = possible_id
            self._validate_control_request(request)
            self._begin_dispatch()
            try:
                if request["operation"] == "on":
                    result = self._turn_on(request["params"]["host_binding"])
                else:
                    result = self._turn_off_rehearsal()
            finally:
                self._end_dispatch()
            response = {
                "protocol": CONTROL_PROTOCOL,
                "request_id": request_id,
                "ok": True,
                "result": result,
            }
        except InvalidPublicFrame:
            response = self._control_error_response(
                request_id,
                "invalid_frame",
                "request is not one bounded UTF-8 JSON object",
            )
        except PublicRequestError as exc:
            response = self._control_error_response(
                request_id,
                exc.code,
                str(exc),
            )
        except AgenticEvoError:
            response = self._control_error_response(
                request_id,
                "operation_failed",
                "trusted runtime rejected the authority transition",
            )
        except Exception:
            response = self._control_error_response(
                request_id,
                "internal_error",
                "Witness could not complete the authority transition",
            )

        try:
            send_public_message(connection, response)
        except (InvalidPublicFrame, EOFError, OSError, ValueError):
            pass

    @staticmethod
    def _validate_control_request(request: Mapping[str, Any]) -> None:
        if set(request) != {"protocol", "request_id", "operation", "params"}:
            raise PublicRequestError(
                "invalid_request",
                "control request envelope has unexpected fields",
            )
        if request.get("protocol") != CONTROL_PROTOCOL:
            raise PublicRequestError(
                "invalid_protocol",
                "control request protocol is unsupported",
            )
        request_id = request.get("request_id")
        if (
            not isinstance(request_id, str)
            or not request_id
            or len(request_id) > 128
        ):
            raise PublicRequestError(
                "invalid_request",
                "request_id must be a bounded non-empty string",
            )
        operation = request.get("operation")
        if operation not in {"off", "on"}:
            raise PublicRequestError(
                "operation_not_control",
                "operation is not available on the authority control endpoint",
            )
        params = request.get("params")
        if operation == "off" and params != {}:
            raise PublicRequestError(
                "invalid_parameters",
                "Off rehearsal accepts no caller-supplied parameters",
            )
        if operation == "on" and (
            not isinstance(params, Mapping)
            or set(params) != {"host_binding"}
            or not isinstance(params.get("host_binding"), str)
            or not params["host_binding"]
        ):
            raise PublicRequestError(
                "invalid_parameters",
                "On requires the host binding",
            )

    def _load_development_organ_argv(self) -> tuple[str, ...] | None:
        path = self.home / "config" / _PRODUCT_CONFIG_NAME
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise IntegrityError("product development configuration is unreadable") from error
        if not isinstance(value, dict):
            raise IntegrityError("product development configuration is invalid")
        argv = value.get("development_organ_argv")
        if (
            value.get("schema") != "agentic-evo.product.v1"
            or not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) or not item for item in argv)
            or not Path(argv[0]).is_absolute()
            or not Path(argv[0]).is_file()
        ):
            raise IntegrityError("product development configuration is invalid")
        return tuple(argv)

    def _restore_development_opportunity(self) -> None:
        if self._development_organ_argv is None:
            return
        status = self.runtime.status()
        if status.authority != "on":
            return
        latest = next(
            (
                record
                for record in reversed(self.runtime.trusted.records())
                if record.event_kind
                in {"body_development_action", "body_development_failed"}
            ),
            None,
        )
        if latest is None:
            self._schedule_development(
                reason="bootstrap",
                after_sequence=self.runtime.trusted.last_event_sequence(),
            )
            return
        self._last_development = {
            "event_kind": latest.event_kind,
            "sequence": latest.sequence,
            "action": latest.payload.get("action"),
            "failure": latest.payload.get("failure"),
        }
        if latest is not None and latest.payload.get("action") == "request_later":
            not_before = latest.payload.get("not_before")
            if isinstance(not_before, str):
                self._schedule_development_at(
                    not_before=not_before,
                    after_sequence=latest.sequence,
                )

    def _schedule_development(self, *, reason: str, after_sequence: int) -> None:
        if self._development_organ_argv is None or self._stop_requested.is_set():
            return
        with self._development_guard:
            if self._development_blocked:
                return
            if self._development_thread is not None and self._development_thread.is_alive():
                self._pending_development = (reason, after_sequence)
                return
            thread = threading.Thread(
                target=self._development_worker,
                args=(reason, after_sequence),
                daemon=True,
                name="agentic-evo-development",
            )
            self._development_thread = thread
            thread.start()

    def _development_worker(self, reason: str, after_sequence: int) -> None:
        current = (reason, after_sequence)
        while not self._stop_requested.is_set():
            self._run_development_opportunity(
                reason=current[0],
                after_sequence=current[1],
            )
            with self._development_guard:
                pending = self._pending_development
                self._pending_development = None
                if pending is None or self._stop_requested.is_set():
                    self._development_thread = None
                    return
                current = pending

    def _run_development_opportunity(self, *, reason: str, after_sequence: int) -> None:
        status = self.runtime.status()
        if status.authority != "on" or self._development_organ_argv is None:
            return
        opportunity_id = f"development-{uuid4().hex}"
        executor: DevelopmentExecutor | None = None
        try:
            opportunity = DevelopmentOpportunity(
                id=opportunity_id,
                reason=reason,
                root=status.root,
                current_body_ref=status.head,
                after_sequence=after_sequence,
                organ_argv=self._development_organ_argv,
                working_dir=str(self.home),
                lineage_facts=self._current_body_lineage_facts(
                    expected_head=status.head,
                ),
            )
            executor = DevelopmentExecutor(self.runtime, self.witness)
            with self._development_guard:
                if self._development_blocked or self._stop_requested.is_set():
                    return
                self._development_executor = executor
            with self._body_guard:
                self._close_body()
            with self._development_guard:
                if self._development_blocked or self._stop_requested.is_set():
                    return
            result = executor.offer_development(opportunity)
            extra: dict[str, Any] = {}
            if result.action in {"retain", "withdraw"}:
                current = self.runtime.status()
                if current.head != result.candidate_head:
                    raise DevelopmentExecutorError("candidate_resolution_head_mismatch")
                if result.action == "withdraw":
                    extra.update(
                        self._withdraw_candidate(
                            result,
                            opportunity_id=opportunity.id,
                        )
                    )
            payload = {
                **result.to_mapping(),
                "opportunity_reason": reason,
                "after_sequence": after_sequence,
                **extra,
            }
            record = self.runtime.record_body_development(
                event_kind="body_development_action",
                opportunity_id=opportunity.id,
                project_environment=str(self.home),
                causation_ref=opportunity.id,
                payload=payload,
            )
            with self._development_guard:
                self._last_development = {
                    "event_kind": record.event_kind,
                    "sequence": record.sequence,
                    "action": result.action,
                    "failure": None,
                }
            if result.action == "request_later" and result.not_before is not None:
                self._schedule_development_at(
                    not_before=result.not_before,
                    after_sequence=record.sequence,
                )
        except Exception as error:
            try:
                if self.runtime.status().authority == "on":
                    failure = (
                        error.code
                        if isinstance(error, DevelopmentExecutorError)
                        else type(error).__name__
                    )
                    record = self.runtime.record_body_development(
                        event_kind="body_development_failed",
                        opportunity_id=opportunity_id,
                        project_environment=str(self.home),
                        causation_ref=opportunity_id,
                        payload={
                            "action": "failed",
                            "opportunity_reason": reason,
                            "after_sequence": after_sequence,
                            "failure": failure,
                        },
                    )
                    with self._development_guard:
                        self._last_development = {
                            "event_kind": record.event_kind,
                            "sequence": record.sequence,
                            "action": "failed",
                            "failure": failure,
                        }
            except AgenticEvoError:
                pass
        finally:
            with self._development_guard:
                if executor is not None and self._development_executor is executor:
                    self._development_executor = None
            if not self._stop_requested.is_set():
                try:
                    self._ensure_body()
                except AgenticEvoError:
                    pass

    def _current_body_lineage_facts(
        self,
        *,
        expected_head: str,
    ) -> CurrentBodyLineageFacts:
        manifest = self.runtime.body_store.read_manifest(expected_head)
        advanced, resolution = self.runtime.trusted.current_body_lineage_records(
            expected_head=expected_head,
        )
        return CurrentBodyLineageFacts(
            manifest_head=manifest.commitment,
            manifest_generation=manifest.generation,
            manifest_parent_head=manifest.parent_head,
            manifest_created_at=manifest.created_at,
            manifest_activation_kind=manifest.activation_kind,
            manifest_activation_artifact=manifest.activation_artifact,
            manifest_development_kind=manifest.development_kind,
            manifest_development_artifact=manifest.development_artifact,
            head_advanced_event_ref=(
                None if advanced is None else advanced.event_id
            ),
            head_advanced_event_status=(
                "no_recorded_head_advanced" if advanced is None else "recorded"
            ),
            latest_recorded_resolution_action=(
                "no_recorded_resolution"
                if resolution is None
                else str(resolution.payload["action"])
            ),
            latest_recorded_resolution_record_ref=(
                None if resolution is None else resolution.event_id
            ),
        )

    def _withdraw_candidate(
        self,
        result: BodyActionResult,
        *,
        opportunity_id: str,
    ) -> dict[str, str]:
        if result.candidate_head is None:
            raise DevelopmentExecutorError("candidate_resolution_incomplete")
        candidate = self.runtime.body_store.read_manifest(result.candidate_head)
        if candidate.parent_head is None:
            raise DevelopmentExecutorError("candidate_has_no_parent")
        parent = self.runtime.body_store.read_manifest(candidate.parent_head)
        files = {
            path: self.runtime.body_store.read_file(parent.commitment, path)
            for path in parent.file_names
        }
        lease = self.witness.open_current_body_session(expected_head=candidate.commitment)
        try:
            corrective = lease.prepare_successor(
                files=files,
                activation_kind=parent.activation_kind,
                activation_artifact=parent.activation_artifact,
                development_kind=parent.development_kind,
                development_artifact=parent.development_artifact,
                causation_ref=opportunity_id,
            )
            lease.advance_head(candidate_head=corrective)
            return {
                "restored_from_head": parent.commitment,
                "corrective_head": corrective,
            }
        finally:
            lease.close()

    def _schedule_development_at(
        self,
        *,
        not_before: str,
        after_sequence: int,
    ) -> None:
        due = datetime.fromisoformat(not_before.replace("Z", "+00:00"))
        if due.tzinfo is None:
            raise DevelopmentExecutorError("invalid_not_before")
        delay = max(0.0, (due - datetime.now(timezone.utc)).total_seconds())
        timer = threading.Timer(
            delay,
            self._fire_deferred_development,
            args=(after_sequence,),
        )
        timer.daemon = True
        with self._development_guard:
            if self._development_timer is not None:
                self._development_timer.cancel()
            self._development_timer = timer
        timer.start()

    def _fire_deferred_development(self, after_sequence: int) -> None:
        with self._development_guard:
            self._development_timer = None
        self._schedule_development(
            reason="body_due",
            after_sequence=after_sequence,
        )

    def _cancel_development(self) -> None:
        with self._development_guard:
            self._development_blocked = True
            timer = self._development_timer
            self._development_timer = None
            executor = self._development_executor
            self._pending_development = None
        if timer is not None:
            timer.cancel()
        if executor is not None:
            executor.cancel()

    def _turn_off_rehearsal(self) -> dict[str, Any]:
        with self._body_guard:
            self.runtime.rehearse_turn_off()
        self._cancel_development()
        with self._body_guard:
            self._close_body()
            status = self.runtime.status()
            result = self._project_status(status)
            result["body_rehearsal"] = self._body_description()
            result["control_provenance"] = "control_unverified"
            return result

    def _turn_on(self, host_binding: str) -> dict[str, Any]:
        with self._body_guard:
            self.runtime.trusted.set_authority(
                authority="on",
                host_binding=host_binding,
            )
            self._ensure_body()
            status = self.runtime.status()
        with self._development_guard:
            self._development_blocked = False
        self._restore_development_opportunity()
        return self._project_status(status)

    @staticmethod
    def _control_error_response(
        request_id: str | None,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "protocol": CONTROL_PROTOCOL,
            "request_id": request_id,
            "ok": False,
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def _remove_stale_unix_endpoint(endpoint: Any) -> None:
        if endpoint.family != "AF_UNIX":
            return
        path = Path(endpoint.address)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _ensure_body(self) -> None:
        with self._body_guard:
            status = self.runtime.status()
            if status.authority != "on":
                self._close_body()
                return
            if (
                self._body is not None
                and self._body.is_alive()
                and self._body.boot.head == status.head
            ):
                try:
                    self._body.assert_bound()
                except AgenticEvoError:
                    pass
                else:
                    return
            self._close_body()
            self._body = self.body_supervisor.spawn_current()

    def _close_body(self) -> None:
        with self._body_guard:
            body = self._body
            self._body = None
            if body is not None:
                body.close()

    def _body_description(self) -> dict[str, Any]:
        with self._body_guard:
            if self._body is None:
                return {
                    "state": "absent",
                    "head": None,
                    "provenance": "subprocess_rehearsal",
                }
            return self._body.describe()

    def _development_description(self) -> dict[str, Any]:
        with self._development_guard:
            return {
                "configured": self._development_organ_argv is not None,
                "active": self._development_executor is not None,
                "pending": self._pending_development is not None,
                "scheduled": self._development_timer is not None,
                "last": (
                    None
                    if self._last_development is None
                    else dict(self._last_development)
                ),
            }

    def _register_connection(self, connection: Connection) -> bool:
        with self._lifecycle_guard:
            if self._stop_requested.is_set():
                return False
            self._active_connections[id(connection)] = _ManagedConnection(connection)
            return True

    def _managed_connection(self, connection: Connection) -> _ManagedConnection:
        with self._lifecycle_guard:
            managed_connection = self._active_connections.get(id(connection))
        if managed_connection is not None:
            return managed_connection
        return _ManagedConnection(connection)

    def _unregister_connection(self, connection: Connection) -> None:
        with self._lifecycle_guard:
            self._active_connections.pop(id(connection), None)

    def _begin_dispatch(self) -> None:
        with self._quiescent:
            if self._stop_requested.is_set():
                raise PublicRequestError(
                    "service_stopping",
                    "Witness service is stopping",
                )
            self._active_dispatches += 1

    def _end_dispatch(self) -> None:
        with self._quiescent:
            self._active_dispatches -= 1
            if not self._active_dispatches:
                self._quiescent.notify_all()

    def _join_connection_workers(self) -> None:
        while True:
            with self._lifecycle_guard:
                workers = tuple(self._connection_workers)
            if not workers:
                return
            for worker in workers:
                if worker is not threading.current_thread():
                    worker.join()


def _write_startup_error(code: str, message: str) -> None:
    print(
        json.dumps(
            {"ok": False, "error": {"code": code, "message": message}},
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=sys.stderr,
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the persistent Agentic-Evo Witness service.",
    )
    parser.add_argument(
        "--dev-home",
        type=Path,
        required=True,
        help="Existing Agentic-Evo runtime home; this command never creates an identity.",
    )
    arguments = parser.parse_args(argv)

    if not (arguments.dev_home / "trusted" / "state.sqlite3").is_file():
        _write_startup_error(
            "not_initialized",
            "the development runtime has no existing trusted state",
        )
        return 3

    try:
        service = WitnessService(arguments.dev_home)
        service.serve_forever()
    except TimeoutError:
        _write_startup_error(
            "service_already_running",
            "another Witness service already owns this development home",
        )
        return 4
    except IntegrityError:
        _write_startup_error(
            "trusted_state_invalid",
            "the existing trusted state failed verification",
        )
        return 5
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
