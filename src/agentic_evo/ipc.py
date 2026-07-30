from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from multiprocessing.connection import Client, Connection

from ._util import canonical_json_bytes, sha256_hex
from .errors import AgenticEvoError


PUBLIC_PROTOCOL = "agentic-evo-public-v1"
CONTROL_PROTOCOL = "agentic-evo-off-rehearsal-v1"
MAX_PUBLIC_FRAME_BYTES = 64 * 1024
PUBLIC_IO_TIMEOUT_SECONDS = 2.0
PUBLIC_RESPONSE_TIMEOUT_SECONDS = 12.0
CONTROL_RESPONSE_TIMEOUT_SECONDS = 12.0


class ServiceIPCError(AgenticEvoError):
    """Base class for the Pre-Genesis public service transport."""


class ServiceUnavailableError(ServiceIPCError):
    """Raised when no foreground Witness rehearsal is reachable."""


class ServiceRejectedError(ServiceIPCError):
    """Raised when the Witness rejects a well-formed public request."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class InvalidPublicFrame(ServiceIPCError):
    """Raised when a public IPC frame is malformed or outside its bound."""


@dataclass(frozen=True)
class Endpoint:
    family: str
    address: str


def service_endpoint(
    home: Path,
    *,
    platform: str = sys.platform,
) -> Endpoint:
    """Derive the one public development endpoint for a canonical runtime home."""

    canonical_home = Path(home).expanduser().resolve(strict=False)
    identity = os.path.normcase(str(canonical_home))
    digest = sha256_hex(identity)

    if platform == "win32":
        return Endpoint(
            family="AF_PIPE",
            address=rf"\\.\pipe\agentic-evo-dev-{digest[:32]}",
        )

    if platform == "darwin":
        maximum = 104
    elif platform.startswith("linux"):
        maximum = 108
    else:
        raise ValueError(f"unsupported service platform: {platform}")

    candidate = canonical_home / "trusted" / "surface.sock"
    if len(os.fsencode(candidate)) < maximum:
        address = str(candidate)
    else:
        address = str(
            Path(tempfile.gettempdir()) / f"agentic-evo-{digest[:24]}.sock"
        )
    if len(os.fsencode(address)) >= maximum:
        raise ValueError("cannot derive a bounded AF_UNIX endpoint")
    return Endpoint(family="AF_UNIX", address=address)


def control_endpoint(
    home: Path,
    *,
    platform: str = sys.platform,
) -> Endpoint:
    """Derive the separate unauthenticated Off rehearsal endpoint."""

    canonical_home = Path(home).expanduser().resolve(strict=False)
    identity = os.path.normcase(str(canonical_home))
    digest = sha256_hex(identity)

    if platform == "win32":
        return Endpoint(
            family="AF_PIPE",
            address=rf"\\.\pipe\agentic-evo-dev-off-{digest[:32]}",
        )

    if platform == "darwin":
        maximum = 104
    elif platform.startswith("linux"):
        maximum = 108
    else:
        raise ValueError(f"unsupported control platform: {platform}")

    candidate = canonical_home / "trusted" / "control.sock"
    if len(os.fsencode(candidate)) < maximum:
        address = str(candidate)
    else:
        address = str(
            Path(tempfile.gettempdir()) / f"agentic-evo-off-{digest[:24]}.sock"
        )
    if len(os.fsencode(address)) >= maximum:
        raise ValueError("cannot derive a bounded AF_UNIX control endpoint")
    return Endpoint(family="AF_UNIX", address=address)


def send_public_message(
    connection: Connection,
    value: Mapping[str, Any],
) -> None:
    raw = canonical_json_bytes(dict(value))
    if len(raw) > MAX_PUBLIC_FRAME_BYTES:
        raise InvalidPublicFrame("public IPC frame exceeds its byte bound")
    connection.send_bytes(raw)


def receive_public_message(
    connection: Connection,
    *,
    timeout_seconds: float | None = None,
    close_on_timeout: Callable[[], None] | None = None,
) -> dict[str, Any]:
    deadline: threading.Timer | None = None
    interrupt_socket: socket.socket | None = None
    if timeout_seconds is not None:
        close = close_on_timeout or connection.close
        if sys.platform != "win32":
            try:
                interrupt_socket = socket.fromfd(
                    connection.fileno(),
                    socket.AF_UNIX,
                    socket.SOCK_STREAM,
                )
            except (AttributeError, OSError, ValueError):
                pass

        def interrupt_receive() -> None:
            if interrupt_socket is not None:
                try:
                    interrupt_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            close()

        deadline = threading.Timer(
            timeout_seconds,
            interrupt_receive,
        )
        deadline.daemon = True
        deadline.start()
    try:
        if timeout_seconds is not None and not connection.poll(timeout_seconds):
            raise InvalidPublicFrame("public IPC frame timed out")
        raw = connection.recv_bytes(MAX_PUBLIC_FRAME_BYTES)
    except InvalidPublicFrame:
        raise
    except (EOFError, OSError, ValueError) as exc:
        raise InvalidPublicFrame("cannot receive a bounded public IPC frame") from exc
    finally:
        if deadline is not None:
            deadline.cancel()
            deadline.join()
        if interrupt_socket is not None:
            interrupt_socket.close()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidPublicFrame("public IPC frame is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise InvalidPublicFrame("public IPC frame must contain one JSON object")
    return value


@contextmanager
def open_public_connection(
    endpoint: Endpoint,
    *,
    native_windows: bool = False,
) -> Iterator[Connection]:
    try:
        if native_windows and sys.platform == "win32":
            from .windows_pipe import connect_windows_public_pipe

            connection = connect_windows_public_pipe(
                endpoint.address,
                timeout_seconds=PUBLIC_IO_TIMEOUT_SECONDS,
            )
        else:
            connection = Client(
                endpoint.address,
                family=endpoint.family,
                authkey=None,
            )
    except (EOFError, OSError) as exc:
        raise ServiceUnavailableError(
            "Witness foreground rehearsal is unavailable"
        ) from exc
    try:
        yield connection
    finally:
        connection.close()


class SurfaceClient:
    """Unprivileged client for the public Surface allowlist."""

    def __init__(self, home: Path) -> None:
        self.endpoint = service_endpoint(home)

    def status(self) -> dict[str, Any]:
        return self._request("status", {})

    def wake(
        self,
        *,
        execution_surface: str,
        session_id: str,
        project_environment: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "wake",
            {
                "execution_surface": execution_surface,
                "session_id": session_id,
                "project_environment": project_environment,
                "model": model,
            },
        )

    def sleep(self, *, session_id: str) -> dict[str, Any]:
        return self._request("sleep", {"session_id": session_id})

    def observe(
        self,
        *,
        event_kind: str,
        payload: Mapping[str, Any],
        execution_surface: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        tool_call_id: str | None = None,
        project_environment: str | None = None,
        coverage_gap: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "observe",
            {
                "event_kind": event_kind,
                "payload": dict(payload),
                "execution_surface": execution_surface,
                "session_id": session_id,
                "turn_id": turn_id,
                "tool_call_id": tool_call_id,
                "project_environment": project_environment,
                "coverage_gap": coverage_gap,
            },
        )

    def _request(
        self,
        operation: str,
        params: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_id = uuid4().hex
        request = {
            "protocol": PUBLIC_PROTOCOL,
            "request_id": request_id,
            "operation": operation,
            "params": dict(params),
        }
        with open_public_connection(
            self.endpoint,
            native_windows=True,
        ) as connection:
            try:
                send_public_message(connection, request)
                response = receive_public_message(
                    connection,
                    timeout_seconds=PUBLIC_RESPONSE_TIMEOUT_SECONDS,
                )
            except InvalidPublicFrame as exc:
                raise ServiceUnavailableError(
                    "Witness returned an invalid public response"
                ) from exc

        if response.get("protocol") != PUBLIC_PROTOCOL:
            raise ServiceUnavailableError("Witness returned the wrong protocol")
        if response.get("request_id") != request_id:
            raise ServiceUnavailableError("Witness returned the wrong request id")
        if response.get("ok") is True:
            result = response.get("result")
            if not isinstance(result, dict):
                raise ServiceUnavailableError("Witness returned an invalid result")
            return result

        error = response.get("error")
        if not isinstance(error, dict):
            raise ServiceUnavailableError("Witness returned an invalid error")
        code = error.get("code")
        message = error.get("message")
        if not isinstance(code, str) or not isinstance(message, str):
            raise ServiceUnavailableError("Witness returned an invalid error")
        raise ServiceRejectedError(code, message)


class OffRehearsalClient:
    """Client for the separate unauthenticated Off-only rehearsal protocol."""

    def __init__(self, home: Path) -> None:
        self.endpoint = control_endpoint(home)

    def off(self) -> dict[str, Any]:
        request_id = uuid4().hex
        request = {
            "protocol": CONTROL_PROTOCOL,
            "request_id": request_id,
            "operation": "off",
            "params": {},
        }
        with open_public_connection(self.endpoint) as connection:
            try:
                send_public_message(connection, request)
                response = receive_public_message(
                    connection,
                    timeout_seconds=CONTROL_RESPONSE_TIMEOUT_SECONDS,
                )
            except InvalidPublicFrame as exc:
                raise ServiceUnavailableError(
                    "Witness returned an invalid control response"
                ) from exc

        if response.get("protocol") != CONTROL_PROTOCOL:
            raise ServiceUnavailableError("Witness returned the wrong control protocol")
        if response.get("request_id") != request_id:
            raise ServiceUnavailableError("Witness returned the wrong control request id")
        if response.get("ok") is True:
            result = response.get("result")
            if not isinstance(result, dict):
                raise ServiceUnavailableError(
                    "Witness returned an invalid control result"
                )
            return result

        error = response.get("error")
        if not isinstance(error, dict):
            raise ServiceUnavailableError("Witness returned an invalid control error")
        code = error.get("code")
        message = error.get("message")
        if not isinstance(code, str) or not isinstance(message, str):
            raise ServiceUnavailableError("Witness returned an invalid control error")
        raise ServiceRejectedError(code, message)
