from __future__ import annotations

from dataclasses import asdict
import os
import sys
from typing import BinaryIO

from .body_process import (
    BODY_BOOT_PROTOCOL,
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
        while True:
            request = read_private_frame(reader)
            if request == {
                "protocol": BODY_BOOT_PROTOCOL,
                "operation": "stop",
                "boot_session": boot.boot_session,
            }:
                return 0
            return 2
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


if __name__ == "__main__":
    raise SystemExit(main())
