from __future__ import annotations

from dataclasses import asdict
import json
import os
import sys
from typing import BinaryIO

from .body import DEVELOPMENT_DESCRIPTOR_UNSET
from .body_process import (
    BODY_BOOT_PROTOCOL,
    BODY_LINEAGE_PROTOCOL,
    BodyBootError,
    BootEnvelope,
    boot_envelope_from_mapping,
    read_private_frame,
    ready_echo_for_boot,
    write_private_frame,
)
from .development_executor import (
    BodyActionResult,
    BodyDevelopmentOpportunity,
    DevelopmentExecutorError,
    _parse_model_action,
    body_text_files_from_package,
    build_development_prompt,
)
from .development_entrypoint import (
    DEVELOPMENT_ENTRYPOINT_KIND,
    DevelopmentContext,
    DevelopmentEntrypointError,
    run_development_entrypoint,
)
from .organ_broker import OrganInvocationRequest, OrganResponse


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
                boot=boot,
                reader=reader,
                writer=writer,
            )
            write_private_frame(writer, result)
            sequence += 1
            if (
                result.get("operation") in {"advance_head", "submit_successor"}
                and result.get("ok") is True
            ):
                return 0
            if result.get("operation") == "offer_development":
                # Keep the one-opportunity Body alive only long enough for the
                # host to receive its result and send the normal private stop.
                # Exiting immediately races the host-side pipe dispatcher and
                # can turn a completed result into an EOF failure.
                command = read_private_frame(reader)
                if command == {
                    "protocol": BODY_BOOT_PROTOCOL,
                    "operation": "stop",
                    "boot_session": boot.boot_session,
                }:
                    return 0
                raise BodyBootError("development Body accepted more than one opportunity")
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
        handles = tuple(int(value) for value in arguments)
    except ValueError as exc:
        raise BodyBootError("private Body handles are invalid") from exc
    if any(handle <= 0 for handle in handles) or len(set(handles)) != len(handles):
        raise BodyBootError("private Body handles are invalid")
    read_handle, write_handle = handles
    try:
        for handle in handles:
            os.set_handle_inheritable(handle, False)
        if any(os.get_handle_inheritable(handle) for handle in handles):
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
    boot: BootEnvelope,
    reader: BinaryIO,
    writer: BinaryIO,
) -> dict[str, object]:
    if command.get("protocol") != BODY_LINEAGE_PROTOCOL:
        raise BodyBootError("private lineage rehearsal command is invalid")
    kind = command.get("kind")
    if kind == "development_offer":
        return _run_development_offer(
            command,
            sequence=sequence,
            boot=boot,
            reader=reader,
            writer=writer,
        )
    if kind == "surface_command":
        return _run_surface_submission(
            command,
            sequence=sequence,
            boot_session=boot_session,
            reader=reader,
            writer=writer,
        )
    if kind != "rehearsal_command":
        raise BodyBootError("private lineage rehearsal command is invalid")
    operation = command.get("operation")
    if operation == "prepare_successor":
        expected = {
            "protocol",
            "kind",
            "operation",
            "files",
            "activation_kind",
            "activation_artifact",
            "causation_ref",
        }
        descriptor_supplied = (
            "development_kind" in command or "development_artifact" in command
        )
        if set(command) != expected | (
            {"development_kind", "development_artifact"}
            if descriptor_supplied else set()
        ):
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
        if descriptor_supplied:
            request["development_kind"] = command["development_kind"]
            request["development_artifact"] = command["development_artifact"]
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


def _run_development_offer(
    command: dict[str, object],
    *,
    sequence: int,
    boot: BootEnvelope,
    reader: BinaryIO,
    writer: BinaryIO,
) -> dict[str, object]:
    expected = {
        "protocol",
        "kind",
        "operation",
        "opportunity",
    }
    if (
        set(command) != expected
        or command.get("operation") != "offer_development"
        or not isinstance(command.get("opportunity"), dict)
    ):
        raise BodyBootError("development offer command is invalid")

    try:
        opportunity = BodyDevelopmentOpportunity.from_mapping(command["opportunity"])
    except DevelopmentExecutorError as error:
        raise BodyBootError("development opportunity is invalid") from error
    if (
        opportunity.root != boot.root
        or opportunity.current_body_ref != boot.head
        or not opportunity.lineage_facts.matches_boot(
            head=boot.head,
            generation=boot.generation,
            activation_kind=boot.activation_kind,
            activation_artifact=boot.activation_artifact,
            development_kind=boot.development_kind,
            development_artifact=boot.development_artifact,
        )
    ):
        return _development_result(
            sequence=sequence,
            result=BodyActionResult(
                action="failed",
                opportunity_id=opportunity.id,
                current_body_ref=opportunity.current_body_ref,
                failure="opportunity_current_body_mismatch",
            ),
        )

    organ_call_ref: str | None = None
    recall_trace = ()
    response: OrganResponse | None = None
    try:
        body_files = body_text_files_from_package(boot.body_package)
        if boot.development_kind is None:
            activation_context = body_files[boot.activation_artifact]
            prompt = build_development_prompt(
                opportunity=opportunity,
                activation_context=activation_context,
                body_files=body_files,
            )
            response = _invoke_organ_round_trip(
                sequence=sequence,
                boot=boot,
                opportunity=opportunity,
                prompt=prompt,
                reader=reader,
                writer=writer,
            )
            organ_call_ref = response.organ_call_ref
            recall_trace = response.recall_trace
            if response.failure is not None:
                return _development_result(
                    sequence=sequence,
                    result=BodyActionResult(
                        action="failed",
                        opportunity_id=opportunity.id,
                        current_body_ref=opportunity.current_body_ref,
                        organ_call_ref=organ_call_ref,
                        recall_trace=recall_trace,
                        failure=response.failure,
                    ),
                )
            assert response.final_text is not None
            directive = _parse_model_action(response.final_text)
        else:
            if boot.development_kind != DEVELOPMENT_ENTRYPOINT_KIND:
                raise DevelopmentExecutorError("development_kind_unsupported")
            artifact = boot.development_artifact
            if not isinstance(artifact, str) or not artifact:
                raise DevelopmentExecutorError("development_artifact_unavailable")
            try:
                source = body_files[artifact]
            except KeyError as error:
                raise DevelopmentExecutorError(
                    "development_artifact_unavailable"
                ) from error

            def recall_port(
                before_sequence: int | None,
                limit: int,
            ) -> dict[str, object]:
                return _development_recall_round_trip(
                    sequence=sequence,
                    boot=boot,
                    opportunity=opportunity,
                    before_sequence=before_sequence,
                    limit=limit,
                    reader=reader,
                    writer=writer,
                )

            def organ_port(prompt: str) -> dict[str, object]:
                nonlocal organ_call_ref, recall_trace, response
                response = _invoke_organ_round_trip(
                    sequence=sequence,
                    boot=boot,
                    opportunity=opportunity,
                    prompt=prompt,
                    reader=reader,
                    writer=writer,
                )
                if (
                    organ_call_ref is not None
                    and response.organ_call_ref != organ_call_ref
                ):
                    raise DevelopmentEntrypointError("organ_call_ref_changed")
                if response.organ_call_ref is not None:
                    organ_call_ref = response.organ_call_ref
                recall_trace = response.recall_trace
                return response.to_mapping()

            action = run_development_entrypoint(
                source,
                context=DevelopmentContext(
                    opportunity=opportunity.to_mapping(),
                    body_files=body_files,
                    recall_port=recall_port,
                    organ_port=organ_port,
                ),
            )
            directive = _parse_model_action(
                json.dumps(
                    action,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
    except (DevelopmentExecutorError, DevelopmentEntrypointError) as error:
        return _development_result(
            sequence=sequence,
            result=BodyActionResult(
                action="failed",
                opportunity_id=opportunity.id,
                current_body_ref=opportunity.current_body_ref,
                organ_call_ref=organ_call_ref,
                recall_trace=recall_trace,
                failure=error.code,
            ),
        )

    if directive.action == "submit_successor":
        candidate_head: str | None = None
        try:
            descriptor = {}
            if directive.development_kind is not DEVELOPMENT_DESCRIPTOR_UNSET:
                descriptor = {
                    "development_kind": directive.development_kind,
                    "development_artifact": directive.development_artifact,
                }
            prepared = _lineage_round_trip(
                operation="prepare_successor",
                sequence=sequence,
                boot_session=boot.boot_session,
                reader=reader,
                writer=writer,
                extra={
                    "files": directive.files,
                    "activation_kind": directive.activation_kind,
                    "activation_artifact": directive.activation_artifact,
                    "causation_ref": directive.causation_ref,
                    **descriptor,
                },
            )
            candidate = prepared.get("candidate_head")
            if not isinstance(candidate, str) or not candidate:
                raise BodyBootError("development prepare response is invalid")
            candidate_head = candidate
            advanced = _lineage_round_trip(
                operation="advance_head",
                sequence=sequence + 1,
                boot_session=boot.boot_session,
                reader=reader,
                writer=writer,
                extra={"candidate_head": candidate_head},
            )
            head = advanced.get("head")
            generation = advanced.get("generation")
            if (
                not isinstance(head, str)
                or not head
                or not isinstance(generation, int)
                or isinstance(generation, bool)
            ):
                raise BodyBootError("development advance response is invalid")
        except BodyBootError:
            return _development_result(
                sequence=sequence,
                result=BodyActionResult(
                    action="failed",
                    opportunity_id=opportunity.id,
                    current_body_ref=opportunity.current_body_ref,
                    candidate_head=candidate_head,
                    organ_call_ref=organ_call_ref,
                    recall_trace=recall_trace,
                    failure="candidate_transition_failed",
                ),
            )
        return _development_result(
            sequence=sequence,
            result=BodyActionResult(
                action="candidate_submitted",
                opportunity_id=opportunity.id,
                current_body_ref=opportunity.current_body_ref,
                candidate_head=head,
                generation=generation,
                organ_call_ref=organ_call_ref,
                recall_trace=recall_trace,
            ),
        )

    if directive.action == "no_change":
        result = BodyActionResult(
            action="no_change",
            opportunity_id=opportunity.id,
            current_body_ref=opportunity.current_body_ref,
            organ_call_ref=organ_call_ref,
            recall_trace=recall_trace,
        )
    elif directive.action == "request_later":
        result = BodyActionResult(
            action="request_later",
            opportunity_id=opportunity.id,
            current_body_ref=opportunity.current_body_ref,
            not_before=directive.not_before,
            reason=directive.reason,
            organ_call_ref=organ_call_ref,
            recall_trace=recall_trace,
        )
    elif directive.action in {"retain", "withdraw"}:
        result = BodyActionResult(
            action=directive.action,
            opportunity_id=opportunity.id,
            current_body_ref=opportunity.current_body_ref,
            candidate_head=directive.candidate_head,
            evidence_refs=directive.evidence_refs,
            organ_call_ref=organ_call_ref,
            recall_trace=recall_trace,
        )
    else:
        raise BodyBootError("development organ returned an unsupported action")
    return _development_result(sequence=sequence, result=result)


def _development_recall_round_trip(
    *,
    sequence: int,
    boot: BootEnvelope,
    opportunity: BodyDevelopmentOpportunity,
    before_sequence: int | None,
    limit: int,
    reader: BinaryIO,
    writer: BinaryIO,
) -> dict[str, object]:
    request = {
        "protocol": BODY_LINEAGE_PROTOCOL,
        "kind": "development_recall",
        "operation": "recall_experiences",
        "boot_session": boot.boot_session,
        "root": boot.root,
        "opportunity_id": opportunity.id,
        "bound_head": boot.head,
        "sequence": sequence,
        "before_sequence": before_sequence,
        "limit": limit,
    }
    write_private_frame(writer, request)
    response = read_private_frame(reader)
    expected = {
        "protocol": BODY_LINEAGE_PROTOCOL,
        "kind": "development_recall_result",
        "operation": "recall_experiences",
        "boot_session": boot.boot_session,
        "root": boot.root,
        "opportunity_id": opportunity.id,
        "bound_head": boot.head,
        "sequence": sequence,
        "before_sequence": before_sequence,
        "limit": limit,
    }
    if any(response.get(field) != value for field, value in expected.items()):
        raise BodyBootError("private development recall response lost its request binding")
    if set(response) == {*expected, "failure"}:
        failure = response.get("failure")
        if not isinstance(failure, str) or not failure:
            raise BodyBootError("private development recall response is invalid")
        raise DevelopmentEntrypointError(failure)
    if set(response) != {*expected, "experiences", "has_more"}:
        raise BodyBootError("private development recall response is invalid")
    experiences = response.get("experiences")
    has_more = response.get("has_more")
    if not isinstance(experiences, list) or not isinstance(has_more, bool):
        raise BodyBootError("private development recall response is invalid")
    return {"experiences": experiences, "has_more": has_more}


def _invoke_organ_round_trip(
    *,
    sequence: int,
    boot: BootEnvelope,
    opportunity: BodyDevelopmentOpportunity,
    prompt: str,
    reader: BinaryIO,
    writer: BinaryIO,
) -> OrganResponse:
    try:
        request = OrganInvocationRequest(
            boot_session=boot.boot_session,
            opportunity_id=opportunity.id,
            bound_head=boot.head,
            sequence=sequence,
            prompt=prompt,
        )
    except ValueError as error:
        raise BodyBootError("private organ request is invalid") from error
    frame = {
        "protocol": BODY_LINEAGE_PROTOCOL,
        "kind": "invoke_organ",
        "operation": "invoke_organ",
        **request.to_mapping(),
    }
    write_private_frame(writer, frame)
    response = read_private_frame(reader)
    expected = {
        "protocol": BODY_LINEAGE_PROTOCOL,
        "kind": "organ_response",
        "operation": "invoke_organ",
        "boot_session": boot.boot_session,
        "opportunity_id": opportunity.id,
        "bound_head": boot.head,
        "sequence": sequence,
    }
    if any(response.get(field) != value for field, value in expected.items()):
        raise BodyBootError("private organ response lost its request binding")
    try:
        return OrganResponse.from_mapping(
            {
                key: value
                for key, value in response.items()
                if key not in expected
            }
        )
    except ValueError as error:
        raise BodyBootError("private organ response is invalid") from error


def _development_result(
    *,
    sequence: int,
    result: BodyActionResult,
) -> dict[str, object]:
    return {
        "protocol": BODY_LINEAGE_PROTOCOL,
        "kind": "development_result",
        "operation": "offer_development",
        "sequence": sequence,
        **result.to_mapping(),
    }


def _run_surface_submission(
    command: dict[str, object],
    *,
    sequence: int,
    boot_session: str,
    reader: BinaryIO,
    writer: BinaryIO,
) -> dict[str, object]:
    if set(command) != {
        "protocol",
        "kind",
        "operation",
        "files",
        "activation_kind",
        "activation_artifact",
        "causation_ref",
        "execution_surface",
        "session_id",
        "expected_head",
    } or command.get("operation") != "submit_successor":
        raise BodyBootError("surface submission command is invalid")
    files = command.get("files")
    if not isinstance(files, dict) or not files or any(
        not isinstance(path, str) or not path or not isinstance(value, str)
        for path, value in files.items()
    ):
        raise BodyBootError("surface submission files are invalid")
    for field in ("activation_kind", "activation_artifact", "execution_surface", "session_id", "expected_head"):
        if not isinstance(command.get(field), str) or not command[field]:
            raise BodyBootError("surface submission binding is invalid")
    if command.get("causation_ref") is not None and not isinstance(command.get("causation_ref"), str):
        raise BodyBootError("surface submission causation is invalid")
    binding = {
        "execution_surface": command["execution_surface"],
        "session_id": command["session_id"],
        "expected_head": command["expected_head"],
    }
    candidate = _lineage_round_trip(
        operation="prepare_successor",
        sequence=sequence,
        boot_session=boot_session,
        reader=reader,
        writer=writer,
        extra={
            "files": files,
            "activation_kind": command["activation_kind"],
            "activation_artifact": command["activation_artifact"],
            "causation_ref": command["causation_ref"],
            **binding,
        },
    ).get("candidate_head")
    if not isinstance(candidate, str) or not candidate:
        raise BodyBootError("surface submission prepare response is invalid")
    advanced = _lineage_round_trip(
        operation="advance_head",
        sequence=sequence + 1,
        boot_session=boot_session,
        reader=reader,
        writer=writer,
        extra={"candidate_head": candidate, **binding},
    )
    if not isinstance(advanced.get("head"), str) or not isinstance(advanced.get("generation"), int) or isinstance(advanced.get("generation"), bool) or not isinstance(advanced.get("authority"), str):
        raise BodyBootError("surface submission advance response is invalid")
    return {
        "protocol": BODY_LINEAGE_PROTOCOL,
        "kind": "rehearsal_result",
        "operation": "submit_successor",
        "sequence": sequence,
        "ok": True,
        "head": advanced["head"],
        "generation": advanced["generation"],
        "authority": advanced["authority"],
    }


def _lineage_round_trip(
    *,
    operation: str,
    sequence: int,
    boot_session: str,
    reader: BinaryIO,
    writer: BinaryIO,
    extra: dict[str, object],
) -> dict[str, object]:
    request = {
        "protocol": BODY_LINEAGE_PROTOCOL,
        "kind": "lineage_request",
        "boot_session": boot_session,
        "sequence": sequence,
        "operation": operation,
        **extra,
    }
    write_private_frame(writer, request)
    response = read_private_frame(reader)
    expected_common = {
        "protocol": BODY_LINEAGE_PROTOCOL,
        "kind": "lineage_response",
        "boot_session": boot_session,
        "sequence": sequence,
        "operation": operation,
    }
    if any(response.get(key) != value for key, value in expected_common.items()):
        raise BodyBootError("surface lineage response lost its request binding")
    if response.get("ok") is False:
        raise BodyBootError("surface lineage request was rejected")
    if response.get("ok") is not True:
        raise BodyBootError("surface lineage response is invalid")
    return response


if __name__ == "__main__":
    raise SystemExit(main())
