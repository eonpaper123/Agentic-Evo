from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from multiprocessing.connection import Connection, Listener
from pathlib import Path
import sys
import threading
from typing import Any, Mapping

from ._util import ExclusiveFileLock
from .body_process import BodyProcessSupervisor, SpawnedBodyProcess
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
from .runtime import DevelopmentalRuntime
from .witness import WitnessCore


class PublicRequestError(AgenticEvoError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WitnessService:
    """Foreground rehearsal of one fixed-home Witness public boundary.

    This process is intentionally not described as an independent OS principal.
    It proves protocol separation and singleton supervision only.
    """

    _PUBLIC_PARAMETERS = {
        "status": (frozenset(), frozenset()),
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
        "sleep": (frozenset({"session_id"}), frozenset()),
        "observe": (
            frozenset({"event_kind", "payload"}),
            frozenset(
                {
                    "execution_surface",
                    "session_id",
                    "turn_id",
                    "tool_call_id",
                    "project_environment",
                    "coverage_gap",
                }
            ),
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
        self._control_listener: Listener | None = None

    def serve_forever(self) -> None:
        service_lock = ExclusiveFileLock(
            self.runtime.runtime_path / ".witness-service.lock",
            timeout_seconds=0.0,
        )
        with service_lock:
            self._remove_stale_unix_endpoint(self.endpoint)
            self._remove_stale_unix_endpoint(self.control_endpoint)
            listener: Listener | None = None
            try:
                self._ensure_body()
                self._start_control_listener()
                listener = Listener(
                    self.endpoint.address,
                    family=self.endpoint.family,
                    backlog=1,
                    authkey=None,
                )
                while True:
                    connection = listener.accept()
                    if not self._connection_slots.acquire(blocking=False):
                        connection.close()
                        continue
                    worker = threading.Thread(
                        target=self._serve_connection_with_deadline,
                        args=(connection,),
                        daemon=True,
                    )
                    worker.start()
            finally:
                if listener is not None:
                    listener.close()
                if self._control_listener is not None:
                    self._control_listener.close()
                self._close_body()
                self.witness.close()
                self._remove_stale_unix_endpoint(self.endpoint)
                self._remove_stale_unix_endpoint(self.control_endpoint)

    def dispatch_public(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._validate_request_envelope(request)
        operation = request["operation"]
        params = request["params"]
        self._validate_parameters(operation, params)
        self._ensure_body()

        if operation == "status":
            result = asdict(self.runtime.status())
            result["body_rehearsal"] = self._body_description()
            return result
        if operation == "wake":
            return asdict(self.runtime.wake(**params))
        if operation == "sleep":
            return asdict(self.runtime.sleep(**params))
        if operation == "observe":
            record = self.runtime.observe(**params)
            return {
                "event_id": record.event_id,
                "sequence": record.sequence,
                "integrity_hash": record.integrity_hash,
            }
        raise AssertionError("validated public operation has no dispatcher")

    def _serve_connection(self, connection: Connection) -> None:
        request_id: str | None = None
        try:
            request = receive_public_message(
                connection,
                timeout_seconds=PUBLIC_IO_TIMEOUT_SECONDS,
            )
            possible_id = request.get("request_id")
            if isinstance(possible_id, str):
                request_id = possible_id
            result = self.dispatch_public(request)
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

    def _serve_connection_with_deadline(self, connection: Connection) -> None:
        deadline = threading.Timer(
            PUBLIC_IO_TIMEOUT_SECONDS,
            connection.close,
        )
        deadline.daemon = True
        deadline.start()
        try:
            self._serve_connection(connection)
        finally:
            deadline.cancel()
            connection.close()
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

        string_fields = keys - {"payload", "model"}
        for field in string_fields:
            value = params[field]
            if value is not None and not isinstance(value, str):
                raise PublicRequestError(
                    "invalid_parameters",
                    f"{field} must be a string or null",
                )
        for field in required - {"payload"}:
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
        self._control_listener = listener
        thread = threading.Thread(
            target=self._serve_control_loop,
            args=(listener,),
            daemon=True,
        )
        thread.start()

    def _serve_control_loop(self, listener: Listener) -> None:
        while True:
            try:
                connection = listener.accept()
            except (EOFError, OSError):
                return
            self._serve_control_connection_with_deadline(connection)

    def _serve_control_connection_with_deadline(
        self,
        connection: Connection,
    ) -> None:
        try:
            self._serve_control_connection(connection)
        finally:
            connection.close()

    def _serve_control_connection(self, connection: Connection) -> None:
        request_id: str | None = None
        try:
            receive_deadline = threading.Timer(
                PUBLIC_IO_TIMEOUT_SECONDS,
                connection.close,
            )
            receive_deadline.daemon = True
            receive_deadline.start()
            try:
                request = receive_public_message(
                    connection,
                    timeout_seconds=PUBLIC_IO_TIMEOUT_SECONDS,
                )
            finally:
                receive_deadline.cancel()
            possible_id = request.get("request_id")
            if isinstance(possible_id, str):
                request_id = possible_id
            self._validate_control_request(request)
            result = self._turn_off_rehearsal()
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
                "trusted runtime rejected the Off rehearsal",
            )
        except Exception:
            response = self._control_error_response(
                request_id,
                "internal_error",
                "Witness could not complete the Off rehearsal",
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
        if request.get("operation") != "off":
            raise PublicRequestError(
                "operation_not_control",
                "operation is not available on the Off rehearsal endpoint",
            )
        if request.get("params") != {}:
            raise PublicRequestError(
                "invalid_parameters",
                "Off rehearsal accepts no caller-supplied parameters",
            )

    def _turn_off_rehearsal(self) -> dict[str, Any]:
        with self._body_guard:
            self.runtime.rehearse_turn_off()
            self._close_body()
            status = self.runtime.status()
            result = asdict(status)
            result["body_rehearsal"] = self._body_description()
            result["control_provenance"] = "control_unverified"
            return result

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
        description="Run the Agentic-Evo Pre-Genesis Witness rehearsal.",
    )
    parser.add_argument(
        "--dev-home",
        type=Path,
        required=True,
        help="Existing disposable runtime home; this command never performs Genesis.",
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
