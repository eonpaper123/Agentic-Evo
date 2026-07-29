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
from .errors import AgenticEvoError, IntegrityError, RuntimeOffError
from .ipc import (
    PUBLIC_PROTOCOL,
    PUBLIC_IO_TIMEOUT_SECONDS,
    InvalidPublicFrame,
    receive_public_message,
    send_public_message,
    service_endpoint,
)
from .runtime import DevelopmentalRuntime


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
        self._connection_slots = threading.BoundedSemaphore(
            self._MAX_CONCURRENT_CONNECTIONS
        )

    def serve_forever(self) -> None:
        service_lock = ExclusiveFileLock(
            self.runtime.runtime_path / ".witness-service.lock",
            timeout_seconds=0.0,
        )
        with service_lock:
            self._remove_stale_unix_endpoint()
            listener: Listener | None = None
            try:
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
                self._remove_stale_unix_endpoint()

    def dispatch_public(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._validate_request_envelope(request)
        operation = request["operation"]
        params = request["params"]
        self._validate_parameters(operation, params)

        if operation == "status":
            return asdict(self.runtime.status())
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

    def _remove_stale_unix_endpoint(self) -> None:
        if self.endpoint.family != "AF_UNIX":
            return
        path = Path(self.endpoint.address)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


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
