from __future__ import annotations

from dataclasses import asdict
import os
import sys
from typing import BinaryIO

from .body_process import (
    BODY_BOOT_PROTOCOL,
    BODY_LINEAGE_PROTOCOL,
    BodyBootError,
    boot_envelope_from_mapping,
    read_private_frame,
    ready_echo_for_boot,
    write_private_frame,
)


def main() -> int:
    reader: BinaryIO | None = None
    writer: BinaryIO | None = None
    owns_streams = False
    try:
        reader, writer, owns_streams = _private_streams(tuple(sys.argv[1:]))
        boot = boot_envelope_from_mapping(
            read_private_frame(reader, max_bytes=None)
        )
        ready = ready_echo_for_boot(boot)
        write_private_frame(writer, asdict(ready))
        sequence = 1
        while True:
            command = read_private_frame(reader)
            if command == {
                "protocol": BODY_BOOT_PROTOCOL,
                "operation": "stop",
                "boot_session": boot.boot_session,
            }:
                return 0
            result = _run_lineage_rehearsal(
                command,
                sequence=sequence,
                boot_session=boot.boot_session,
                reader=reader,
                writer=writer,
            )
            write_private_frame(writer, result)
            sequence += 1
            if (
                result.get("operation") == "advance_head"
                and result.get("ok") is True
            ):
                return 0
    except BodyBootError:
        return 2
    finally:
        if owns_streams:
            if writer is not None:
                writer.close()
            if reader is not None:
                reader.close()


def _private_streams(
    arguments: tuple[str, ...],
) -> tuple[BinaryIO, BinaryIO, bool]:
    if not arguments:
        return sys.stdin.buffer, sys.stdout.buffer, False
    if sys.platform != "win32" or len(arguments) != 2:
        raise BodyBootError("private Body handles are invalid")

    import msvcrt

    try:
        read_handle, write_handle = (int(value) for value in arguments)
    except ValueError as exc:
        raise BodyBootError("private Body handles are invalid") from exc
    if read_handle <= 0 or write_handle <= 0 or read_handle == write_handle:
        raise BodyBootError("private Body handles are invalid")
    try:
        os.set_handle_inheritable(read_handle, False)
        os.set_handle_inheritable(write_handle, False)
        if os.get_handle_inheritable(read_handle) or os.get_handle_inheritable(
            write_handle
        ):
            raise BodyBootError("private Body handles could still be inherited")
        read_fd = msvcrt.open_osfhandle(
            read_handle,
            os.O_RDONLY | os.O_BINARY,
        )
        try:
            write_fd = msvcrt.open_osfhandle(
                write_handle,
                os.O_WRONLY | os.O_BINARY,
            )
        except OSError:
            os.close(read_fd)
            raise
        try:
            reader = os.fdopen(read_fd, "rb", buffering=0)
            writer = os.fdopen(write_fd, "wb", buffering=0)
        except OSError:
            os.close(read_fd)
            os.close(write_fd)
            raise
    except (OSError, ValueError) as exc:
        raise BodyBootError("private Body handles could not be sealed") from exc
    return reader, writer, True


def _run_lineage_rehearsal(
    command: dict[str, object],
    *,
    sequence: int,
    boot_session: str,
    reader: BinaryIO,
    writer: BinaryIO,
) -> dict[str, object]:
    if (
        command.get("protocol") != BODY_LINEAGE_PROTOCOL
        or command.get("kind") != "rehearsal_command"
    ):
        raise BodyBootError("private lineage rehearsal command is invalid")
    operation = command.get("operation")
    if operation == "prepare_successor":
        if set(command) != {
            "protocol",
            "kind",
            "operation",
            "files",
            "activation_kind",
            "activation_artifact",
            "causation_ref",
        }:
            raise BodyBootError("private prepare rehearsal has unexpected fields")
        request = {
            "protocol": BODY_LINEAGE_PROTOCOL,
            "kind": "lineage_request",
            "boot_session": boot_session,
            "sequence": sequence,
            "operation": operation,
            "files": command["files"],
            "activation_kind": command["activation_kind"],
            "activation_artifact": command["activation_artifact"],
            "causation_ref": command["causation_ref"],
        }
    elif operation == "advance_head":
        if set(command) != {
            "protocol",
            "kind",
            "operation",
            "candidate_head",
        }:
            raise BodyBootError("private advance rehearsal has unexpected fields")
        request = {
            "protocol": BODY_LINEAGE_PROTOCOL,
            "kind": "lineage_request",
            "boot_session": boot_session,
            "sequence": sequence,
            "operation": operation,
            "candidate_head": command["candidate_head"],
        }
    else:
        raise BodyBootError("private lineage rehearsal operation is not allowed")

    write_private_frame(writer, request)
    response = read_private_frame(reader)
    expected_common = {
        "protocol": BODY_LINEAGE_PROTOCOL,
        "kind": "lineage_response",
        "boot_session": boot_session,
        "sequence": sequence,
        "operation": operation,
    }
    response_sequence = response.get("sequence")
    if (
        not isinstance(response_sequence, int)
        or isinstance(response_sequence, bool)
        or response_sequence != sequence
        or any(
            response.get(key) != value
            for key, value in expected_common.items()
        )
    ):
        raise BodyBootError("private lineage response lost its request binding")
    if response.get("ok") is False:
        if set(response) != {*expected_common, "ok", "error"} or response.get(
            "error"
        ) != "lineage_request_rejected":
            raise BodyBootError("private lineage rejection is malformed")
        return {
            "protocol": BODY_LINEAGE_PROTOCOL,
            "kind": "rehearsal_result",
            "operation": operation,
            "sequence": sequence,
            "ok": False,
            "error": "lineage_request_rejected",
        }
    if response.get("ok") is not True:
        raise BodyBootError("private lineage response has an invalid result")

    if operation == "prepare_successor":
        if set(response) != {*expected_common, "ok", "candidate_head"} or not isinstance(
            response.get("candidate_head"),
            str,
        ):
            raise BodyBootError("private prepare response is malformed")
        return {
            "protocol": BODY_LINEAGE_PROTOCOL,
            "kind": "rehearsal_result",
            "operation": operation,
            "sequence": sequence,
            "ok": True,
            "candidate_head": response["candidate_head"],
        }
    if set(response) != {
        *expected_common,
        "ok",
        "head",
        "generation",
        "authority",
    } or (
        not isinstance(response.get("head"), str)
        or not isinstance(response.get("generation"), int)
        or isinstance(response.get("generation"), bool)
        or not isinstance(response.get("authority"), str)
    ):
        raise BodyBootError("private advance response is malformed")
    return {
        "protocol": BODY_LINEAGE_PROTOCOL,
        "kind": "rehearsal_result",
        "operation": operation,
        "sequence": sequence,
        "ok": True,
        "head": response["head"],
        "generation": response["generation"],
        "authority": response["authority"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
