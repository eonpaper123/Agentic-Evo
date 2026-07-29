from __future__ import annotations

from dataclasses import asdict
import sys

from .body_process import (
    BODY_BOOT_PROTOCOL,
    BodyBootError,
    boot_envelope_from_mapping,
    read_private_frame,
    ready_echo_for_boot,
    write_private_frame,
)


def main() -> int:
    reader = sys.stdin.buffer
    writer = sys.stdout.buffer
    try:
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


if __name__ == "__main__":
    raise SystemExit(main())
