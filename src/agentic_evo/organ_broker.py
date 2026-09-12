from __future__ import annotations

"""Witness-owned, bounded model-organ broker.

The restricted Body cannot choose a program, arguments, environment, or working
directory for an organ.  It can only send a bound prompt over its private pipe.
This module owns the host-side process and returns either bounded final text or
a short failure code; it never interprets or applies a Body action.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from queue import Empty, Queue
import signal
import subprocess
import sys
import threading
from time import monotonic
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence


_MAX_BINDING_TEXT_BYTES = 4 * 1024
_MAX_PROMPT_BYTES = 512 * 1024
_MAX_FINAL_TEXT_BYTES = 128 * 1024
_MAX_RECALL_BYTES = 256 * 1024
_MAX_RECALL_TRACE_BYTES = 256 * 1024
_MAX_ORGAN_ARG_BYTES = 16 * 1024


def _required_text(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid_{field}")
    if len(value.encode("utf-8")) > maximum:
        raise ValueError(f"oversize_{field}")
    return value


@dataclass(frozen=True)
class OrganInvocationRequest:
    """The entire private Body-to-Witness organ request contract."""

    boot_session: str
    opportunity_id: str
    bound_head: str
    sequence: int
    prompt: str

    def __post_init__(self) -> None:
        _required_text(self.boot_session, "boot_session", maximum=_MAX_BINDING_TEXT_BYTES)
        _required_text(
            self.opportunity_id,
            "opportunity_id",
            maximum=_MAX_BINDING_TEXT_BYTES,
        )
        _required_text(self.bound_head, "bound_head", maximum=_MAX_BINDING_TEXT_BYTES)
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 1
        ):
            raise ValueError("invalid_sequence")
        _required_text(self.prompt, "prompt", maximum=_MAX_PROMPT_BYTES)

    def to_mapping(self) -> dict[str, object]:
        return {
            "boot_session": self.boot_session,
            "opportunity_id": self.opportunity_id,
            "bound_head": self.bound_head,
            "sequence": self.sequence,
            "prompt": self.prompt,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "OrganInvocationRequest":
        expected = {
            "boot_session",
            "opportunity_id",
            "bound_head",
            "sequence",
            "prompt",
        }
        if set(value) != expected:
            raise ValueError("invalid_organ_request_fields")
        return cls(
            boot_session=value["boot_session"],
            opportunity_id=value["opportunity_id"],
            bound_head=value["bound_head"],
            sequence=value["sequence"],
            prompt=value["prompt"],
        )


@dataclass(frozen=True)
class OrganResponse:
    """A bounded organ outcome, deliberately not a development decision."""

    final_text: str | None = None
    failure: str | None = None
    organ_call_ref: str | None = None
    recall_trace: tuple["RecallTrace", ...] = ()

    def __post_init__(self) -> None:
        if (self.final_text is None) == (self.failure is None):
            raise ValueError("organ_response_requires_one_outcome")
        if self.final_text is not None:
            _required_text(
                self.final_text,
                "organ_final_text",
                maximum=_MAX_FINAL_TEXT_BYTES,
            )
        if self.failure is not None:
            _required_text(
                self.failure,
                "organ_failure",
                maximum=_MAX_BINDING_TEXT_BYTES,
            )
        if self.organ_call_ref is not None:
            _required_text(
                self.organ_call_ref,
                "organ_call_ref",
                maximum=_MAX_BINDING_TEXT_BYTES,
            )
        if not isinstance(self.recall_trace, tuple):
            raise ValueError("invalid_recall_trace")
        if any(not isinstance(trace, RecallTrace) for trace in self.recall_trace):
            raise ValueError("invalid_recall_trace")
        if _recall_trace_wire_bytes(self.recall_trace) > _MAX_RECALL_TRACE_BYTES:
            raise ValueError("oversize_recall_trace")

    def to_mapping(self) -> dict[str, object]:
        if self.final_text is not None:
            result = {"final_text": self.final_text}
        else:
            assert self.failure is not None
            result = {"failure": self.failure}
        if self.organ_call_ref is not None:
            result["organ_call_ref"] = self.organ_call_ref
        result["recall_trace"] = [trace.to_mapping() for trace in self.recall_trace]
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "OrganResponse":
        fields = set(value)
        if "recall_trace" not in fields:
            raise ValueError("invalid_organ_response_fields")
        raw_trace = value.get("recall_trace")
        if not isinstance(raw_trace, list):
            raise ValueError("invalid_organ_response_fields")
        try:
            recall_trace = tuple(RecallTrace.from_mapping(item) for item in raw_trace)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid_organ_response_fields") from error
        if fields in (
            {"final_text", "recall_trace"},
            {"final_text", "organ_call_ref", "recall_trace"},
        ):
            return cls(
                final_text=value["final_text"],
                organ_call_ref=value.get("organ_call_ref"),
                recall_trace=recall_trace,
            )
        if fields in (
            {"failure", "recall_trace"},
            {"failure", "organ_call_ref", "recall_trace"},
        ):
            return cls(
                failure=value["failure"],
                organ_call_ref=value.get("organ_call_ref"),
                recall_trace=recall_trace,
            )
        raise ValueError("invalid_organ_response_fields")


@dataclass(frozen=True)
class RecallTrace:
    """One host-performed, same-Root read page for the eventual ledger entry."""

    before_sequence: int | None
    limit: int
    returned_event_refs: tuple[str, ...]
    has_more: bool

    def __post_init__(self) -> None:
        if self.before_sequence is not None and (
            not isinstance(self.before_sequence, int)
            or isinstance(self.before_sequence, bool)
            or self.before_sequence < 1
        ):
            raise ValueError("invalid_recall_before_sequence")
        if (
            not isinstance(self.limit, int)
            or isinstance(self.limit, bool)
            or self.limit < 1
            or self.limit > 12
        ):
            raise ValueError("invalid_recall_limit")
        if not isinstance(self.returned_event_refs, tuple):
            raise ValueError("invalid_recall_event_refs")
        if len(self.returned_event_refs) > self.limit:
            raise ValueError("invalid_recall_event_refs")
        for event_ref in self.returned_event_refs:
            _required_text(
                event_ref,
                "recall_event_ref",
                maximum=_MAX_BINDING_TEXT_BYTES,
            )
        if not isinstance(self.has_more, bool):
            raise ValueError("invalid_recall_has_more")

    def to_mapping(self) -> dict[str, object]:
        return {
            "before_sequence": self.before_sequence,
            "limit": self.limit,
            "returned_event_refs": list(self.returned_event_refs),
            "has_more": self.has_more,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "RecallTrace":
        if not isinstance(value, Mapping) or set(value) != {
            "before_sequence",
            "limit",
            "returned_event_refs",
            "has_more",
        }:
            raise ValueError("invalid_recall_trace")
        refs = value["returned_event_refs"]
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise ValueError("invalid_recall_trace")
        return cls(
            before_sequence=value["before_sequence"],
            limit=value["limit"],
            returned_event_refs=tuple(refs),
            has_more=value["has_more"],
        )


def _recall_trace_wire_bytes(trace: Sequence[RecallTrace]) -> int:
    return len(
        json.dumps(
            [item.to_mapping() for item in trace],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


@dataclass(frozen=True)
class _RecallRequest:
    before_sequence: int | None
    limit: int


class OrganInvoker(Protocol):
    """The only host capability visible to the private Body transport."""

    def invoke(
        self,
        request: OrganInvocationRequest,
        cancel_event: threading.Event,
    ) -> OrganResponse:
        ...

    def cancel(self, opportunity_id: str) -> None:
        ...


@dataclass
class _ActiveOrganRun:
    cancel_event: threading.Event
    deadline: float = 0.0
    recall_trace: list[RecallTrace] = field(default_factory=list)
    first_call_ref: str | None = None
    initial_invocation_started: bool = False
    invocation_guard: threading.Lock = field(default_factory=threading.Lock)
    process_guard: threading.Lock = field(default_factory=threading.Lock)
    process: subprocess.Popen[str] | None = None
    process_fence: Any | None = None


class WitnessOrganBroker:
    """Run one fixed, tool-isolated model organ under the normal host account.

    ``organ_argv`` and ``working_dir`` are supplied by trusted host setup when
    this broker is constructed.  Neither crosses the private Body channel.
    """

    def __init__(
        self,
        *,
        runtime: "DevelopmentalRuntime",
        opportunity_id: str,
        root: str,
        current_body_ref: str,
        organ_argv: tuple[str, ...],
        working_dir: Path | str,
        timeout_seconds: float,
        deadline: float | None = None,
        recall_limit: int = 12,
    ) -> None:
        _required_text(opportunity_id, "opportunity_id", maximum=_MAX_BINDING_TEXT_BYTES)
        _required_text(root, "root", maximum=_MAX_BINDING_TEXT_BYTES)
        _required_text(
            current_body_ref,
            "current_body_ref",
            maximum=_MAX_BINDING_TEXT_BYTES,
        )
        if not isinstance(organ_argv, tuple) or not organ_argv:
            raise ValueError("invalid_organ_argv")
        total_arg_bytes = 0
        for argument in organ_argv:
            total_arg_bytes += len(
                _required_text(argument, "organ_argv", maximum=_MAX_ORGAN_ARG_BYTES).encode(
                    "utf-8"
                )
            )
        if total_arg_bytes > _MAX_ORGAN_ARG_BYTES:
            raise ValueError("oversize_organ_argv")
        if timeout_seconds <= 0:
            raise ValueError("organ timeout must be positive")
        if (
            not isinstance(recall_limit, int)
            or isinstance(recall_limit, bool)
            or recall_limit < 1
            or recall_limit > 12
        ):
            raise ValueError("recall_limit must be between 1 and 12")

        self._runtime = runtime
        self._opportunity_id = opportunity_id
        self._root = root
        self._current_body_ref = current_body_ref
        self._organ_argv = organ_argv
        self._working_dir = Path(working_dir)
        self._timeout_seconds = timeout_seconds
        self._opportunity_deadline = (
            monotonic() + timeout_seconds if deadline is None else deadline
        )
        self._recall_limit = recall_limit
        self._guard = threading.Lock()
        self._active: dict[str, _ActiveOrganRun] = {}
        self._revoked: set[str] = set()

    def invoke(
        self,
        request: OrganInvocationRequest,
        cancel_event: threading.Event,
    ) -> OrganResponse:
        """Run one serial turn in the opportunity's single native organ thread."""

        if not isinstance(request, OrganInvocationRequest):
            return OrganResponse(failure="organ_request_rejected")
        if not isinstance(cancel_event, threading.Event):
            return OrganResponse(failure="organ_request_rejected")
        if not self._matches_opportunity(request):
            return OrganResponse(failure="organ_request_rejected")
        if cancel_event.is_set() or self._is_revoked(request.opportunity_id):
            return OrganResponse(failure="organ_opportunity_revoked")
        if not self._opportunity_is_valid():
            return OrganResponse(failure="organ_opportunity_invalid")

        with self._guard:
            if request.opportunity_id in self._revoked:
                return OrganResponse(failure="organ_opportunity_revoked")
            active = self._active.get(request.opportunity_id)
            if active is None:
                active = _ActiveOrganRun(
                    cancel_event=cancel_event,
                    deadline=self._opportunity_deadline,
                )
                self._active[request.opportunity_id] = active
            elif active.cancel_event is not cancel_event:
                return self._response_for_active(
                    failure="organ_request_rejected",
                    active=active,
                )

        if not active.invocation_guard.acquire(blocking=False):
            return self._response_for_active(
                failure="organ_request_rejected",
                active=active,
            )

        try:
            if cancel_event.is_set() or self._is_revoked(request.opportunity_id):
                return self._response_for_active(
                    failure="organ_opportunity_revoked",
                    active=active,
                )
            if monotonic() >= active.deadline:
                return self._response_for_active(
                    failure="organ_timed_out",
                    active=active,
                )
            if not self._opportunity_is_valid():
                return self._response_for_active(
                    failure="organ_opportunity_invalid",
                    active=active,
                )

            if active.initial_invocation_started:
                if active.first_call_ref is None:
                    return self._response_for_active(
                        failure="organ_stream_incomplete",
                        active=active,
                    )
                response = self._invoke_host_organ(
                    request=request,
                    wrapped_prompt=request.prompt,
                    active=active,
                    resume_thread_id=active.first_call_ref,
                )
            else:
                active.initial_invocation_started = True
                initial_page = self._read_recall_page(
                    request=request,
                    active=active,
                    before_sequence=None,
                    limit=self._recall_limit,
                )
                if isinstance(initial_page, str):
                    return self._response_for_active(
                        failure=initial_page,
                        active=active,
                    )
                experiences, has_more, trace = initial_page
                if not self._append_recall_trace(active, trace):
                    return self._response_for_active(
                        failure="organ_recall_trace_oversize",
                        active=active,
                    )
                try:
                    wrapped_prompt = _host_prompt(
                        request.prompt,
                        experiences=experiences,
                        has_more=has_more,
                    )
                except ValueError:
                    return self._response_for_active(
                        failure="organ_recall_oversize",
                        active=active,
                    )
                if (
                    cancel_event.is_set()
                    or self._is_revoked(request.opportunity_id)
                    or not self._opportunity_is_valid()
                ):
                    return self._response_for_active(
                        failure="organ_opportunity_revoked",
                        active=active,
                    )
                response = self._invoke_host_organ(
                    request=request,
                    wrapped_prompt=wrapped_prompt,
                    active=active,
                )

            if response.organ_call_ref is not None:
                if (
                    active.first_call_ref is not None
                    and response.organ_call_ref != active.first_call_ref
                ):
                    self._cancel_active(active)
                    return self._response_for_active(
                        failure="organ_protocol_invalid",
                        active=active,
                    )
                active.first_call_ref = response.organ_call_ref
            if (
                cancel_event.is_set()
                or self._is_revoked(request.opportunity_id)
                or not self._opportunity_is_valid()
            ):
                return self._response_for_active(
                    failure="organ_result_revoked",
                    active=active,
                    organ_call_ref=response.organ_call_ref,
                )
            return self._response_for_active(
                final_text=response.final_text,
                failure=response.failure,
                active=active,
                organ_call_ref=active.first_call_ref,
            )
        finally:
            active.invocation_guard.release()

    def cancel(self, opportunity_id: str) -> None:
        """Revoke one opportunity forever and terminate its host process tree."""

        if not isinstance(opportunity_id, str) or not opportunity_id:
            return
        with self._guard:
            self._revoked.add(opportunity_id)
            active = self._active.pop(opportunity_id, None)
            if active is not None:
                active.cancel_event.set()
        if active is not None:
            self._cancel_active(active)

    def _matches_opportunity(self, request: OrganInvocationRequest) -> bool:
        return (
            request.opportunity_id == self._opportunity_id
            and request.bound_head == self._current_body_ref
        )

    def _is_revoked(self, opportunity_id: str) -> bool:
        with self._guard:
            return opportunity_id in self._revoked

    def _opportunity_is_valid(self) -> bool:
        try:
            status = self._runtime.status()
        except Exception:
            return False
        return (
            status.authority == "on"
            and status.root == self._root
            and status.head == self._current_body_ref
        )

    def _invoke_host_organ(
        self,
        *,
        request: OrganInvocationRequest,
        wrapped_prompt: str,
        active: _ActiveOrganRun,
        resume_thread_id: str | None = None,
    ) -> OrganResponse:
        """Run and, only on a strict recall request, resume one organ thread."""

        turn = self._run_host_turn(
            request=request,
            wrapped_prompt=wrapped_prompt,
            active=active,
            resume_thread_id=resume_thread_id,
        )
        if turn.failure is not None:
            return self._response_for_active(
                failure=turn.failure,
                active=active,
                organ_call_ref=turn.organ_call_ref,
            )
        if turn.final_text is None or turn.organ_call_ref is None:
            return self._response_for_active(
                failure="organ_stream_incomplete",
                active=active,
                organ_call_ref=turn.organ_call_ref,
            )
        if (
            resume_thread_id is not None
            and turn.organ_call_ref != resume_thread_id
        ):
            return self._response_for_active(
                failure="organ_protocol_invalid",
                active=active,
                organ_call_ref=resume_thread_id,
            )
        organ_call_ref = turn.organ_call_ref
        final_text = turn.final_text

        while True:
            try:
                recall_request = _parse_recall_transport(final_text)
            except ValueError:
                return self._response_for_active(
                    failure="organ_transport_invalid",
                    active=active,
                    organ_call_ref=organ_call_ref,
                )
            if recall_request is None:
                return self._response_for_active(
                    final_text=final_text,
                    active=active,
                    organ_call_ref=organ_call_ref,
                )
            page = self._read_recall_page(
                request=request,
                active=active,
                before_sequence=recall_request.before_sequence,
                limit=recall_request.limit,
            )
            if isinstance(page, str):
                return self._response_for_active(
                    failure=page,
                    active=active,
                    organ_call_ref=organ_call_ref,
                )
            experiences, has_more, trace = page
            if not self._append_recall_trace(active, trace):
                return self._response_for_active(
                    failure="organ_recall_trace_oversize",
                    active=active,
                    organ_call_ref=organ_call_ref,
                )
            try:
                resume_prompt = _resume_prompt(
                    experiences=experiences,
                    has_more=has_more,
                )
            except ValueError:
                return self._response_for_active(
                    failure="organ_recall_oversize",
                    active=active,
                    organ_call_ref=organ_call_ref,
                )
            turn = self._run_host_turn(
                request=request,
                wrapped_prompt=resume_prompt,
                active=active,
                resume_thread_id=organ_call_ref,
            )
            if turn.failure is not None:
                return self._response_for_active(
                    failure=turn.failure,
                    active=active,
                    organ_call_ref=organ_call_ref,
                )
            if turn.final_text is None:
                return self._response_for_active(
                    failure="organ_result_missing",
                    active=active,
                    organ_call_ref=organ_call_ref,
                )
            if turn.organ_call_ref is not None and turn.organ_call_ref != organ_call_ref:
                return self._response_for_active(
                    failure="organ_protocol_invalid",
                    active=active,
                    organ_call_ref=organ_call_ref,
                )
            final_text = turn.final_text

    def _run_host_turn(
        self,
        *,
        request: OrganInvocationRequest,
        wrapped_prompt: str,
        active: _ActiveOrganRun,
        resume_thread_id: str | None,
    ) -> OrganResponse:
        """Own one cancellable host Codex process without interpreting its text."""

        if not self._working_dir.is_dir():
            return OrganResponse(failure="working_directory_unavailable")
        if monotonic() >= active.deadline:
            return OrganResponse(failure="organ_timed_out")
        try:
            executable, args = _read_only_organ_args(self._organ_argv)
        except ValueError as error:
            return OrganResponse(failure=str(error))
        try:
            process, fence = self._start_process(
                executable=executable,
                args=args,
                resume_thread_id=resume_thread_id,
            )
        except (OSError, ValueError):
            return OrganResponse(failure="organ_invocation_failed")
        self._set_active_process(active, process=process, fence=fence)
        try:
            if active.cancel_event.is_set() or self._is_revoked(request.opportunity_id):
                self._cancel_active(active)
                return OrganResponse(failure="organ_opportunity_revoked")

            try:
                stdin = process.stdin
                if stdin is None:
                    return OrganResponse(failure="organ_invocation_failed")
                stdin.write(wrapped_prompt)
                stdin.close()
            except (BrokenPipeError, OSError, ValueError, UnicodeError):
                self._cancel_active(active)
                return OrganResponse(failure="organ_invocation_failed")

            return self._collect_result(
                process=process,
                active=active,
                expected_call_ref=resume_thread_id,
                require_call_ref=resume_thread_id is None,
            )
        finally:
            self._release_host_turn(active, process=process, fence=fence)

    def _read_recall_page(
        self,
        *,
        request: OrganInvocationRequest,
        active: _ActiveOrganRun,
        before_sequence: int | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool, RecallTrace] | str:
        if monotonic() >= active.deadline:
            return "organ_timed_out"
        if (
            active.cancel_event.is_set()
            or self._is_revoked(request.opportunity_id)
            or not self._opportunity_is_valid()
        ):
            return "organ_opportunity_revoked"
        try:
            kwargs: dict[str, Any] = {
                "execution_surface": "agentic-evo-body",
                "session_id": request.opportunity_id,
                "limit": limit,
            }
            if before_sequence is not None:
                kwargs["before_sequence"] = before_sequence
            experiences, has_more = self._runtime.recall_experiences(**kwargs)
        except Exception:
            return "organ_recall_unavailable"
        if not isinstance(experiences, list) or not isinstance(has_more, bool):
            return "organ_recall_unavailable"
        try:
            event_refs = tuple(
                _required_text(
                    experience.get("event_id"),
                    "recall_event_ref",
                    maximum=_MAX_BINDING_TEXT_BYTES,
                )
                for experience in experiences
                if isinstance(experience, Mapping)
            )
            if len(event_refs) != len(experiences):
                return "organ_recall_unavailable"
            trace = RecallTrace(
                before_sequence=before_sequence,
                limit=limit,
                returned_event_refs=event_refs,
                has_more=has_more,
            )
        except ValueError:
            return "organ_recall_unavailable"
        if monotonic() >= active.deadline:
            return "organ_timed_out"
        if (
            active.cancel_event.is_set()
            or self._is_revoked(request.opportunity_id)
            or not self._opportunity_is_valid()
        ):
            return "organ_opportunity_revoked"
        return experiences, has_more, trace

    @staticmethod
    def _response_for_active(
        *,
        active: _ActiveOrganRun,
        final_text: str | None = None,
        failure: str | None = None,
        organ_call_ref: str | None = None,
    ) -> OrganResponse:
        if organ_call_ref is None:
            organ_call_ref = active.first_call_ref
        return OrganResponse(
            final_text=final_text,
            failure=failure,
            organ_call_ref=organ_call_ref,
            recall_trace=tuple(active.recall_trace),
        )

    @staticmethod
    def _append_recall_trace(active: _ActiveOrganRun, trace: RecallTrace) -> bool:
        candidate = (*active.recall_trace, trace)
        if _recall_trace_wire_bytes(candidate) > _MAX_RECALL_TRACE_BYTES:
            return False
        active.recall_trace.append(trace)
        return True

    def _start_process(
        self,
        *,
        executable: str,
        args: Sequence[str],
        resume_thread_id: str | None = None,
    ) -> tuple[subprocess.Popen[str], Any | None]:
        command = _organ_command(
            executable=executable,
            args=args,
            resume_thread_id=resume_thread_id,
        )
        process_kwargs: dict[str, Any] = {
            "cwd": str(self._working_dir),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "text": True,
            "encoding": "utf-8",
            "errors": "strict",
            "bufsize": 1,
        }
        fence: Any | None = None
        if sys.platform == "win32":
            from .windows_native import KillOnCloseJob

            process_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
            fence = KillOnCloseJob()
        else:
            process_kwargs["start_new_session"] = True
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(command, **process_kwargs)
            if fence is not None:
                process_handle = getattr(process, "_handle", None)
                if not isinstance(process_handle, int) or process_handle <= 0:
                    raise OSError("host organ process handle is unavailable")
                fence.assign_handle(process_handle)
            return process, fence
        except Exception:
            if process is not None:
                _kill_process_tree(process)
                try:
                    process.wait(timeout=1.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if fence is not None:
                self._close_fence(_ActiveOrganRun(threading.Event(), process_fence=fence))
            raise

    def _collect_result(
        self,
        *,
        process: subprocess.Popen[str],
        active: _ActiveOrganRun,
        expected_call_ref: str | None,
        require_call_ref: bool,
    ) -> OrganResponse:
        stdout = process.stdout
        if stdout is None:
            self._cancel_active(active)
            return OrganResponse(failure="organ_invocation_failed")
        queue: Queue[tuple[str, object]] = Queue()

        def read_stdout() -> None:
            try:
                for line in stdout:
                    queue.put(("line", line))
            except (OSError, UnicodeError, ValueError) as error:
                queue.put(("reader_error", error))
            finally:
                queue.put(("eof", None))

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()
        deadline = active.deadline
        observed_call_ref: str | None = None
        turn_completed = False
        final_text: str | None = None
        reader_error = False
        eof = False
        try:
            while not eof:
                if active.cancel_event.is_set():
                    self._cancel_active(active)
                    return OrganResponse(
                        failure="organ_cancelled",
                        organ_call_ref=observed_call_ref or expected_call_ref,
                    )
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self._cancel_active(active)
                    return OrganResponse(
                        failure="organ_timed_out",
                        organ_call_ref=observed_call_ref or expected_call_ref,
                    )
                try:
                    kind, payload = queue.get(timeout=min(remaining, 0.1))
                except Empty:
                    continue
                if kind == "reader_error":
                    reader_error = True
                    continue
                if kind == "eof":
                    eof = True
                    continue
                assert kind == "line"
                try:
                    call_ref, completed, message = _consume_jsonl_record(
                        payload,
                    )
                except ValueError:
                    self._cancel_active(active)
                    return OrganResponse(
                        failure="organ_protocol_invalid",
                        organ_call_ref=observed_call_ref or expected_call_ref,
                    )
                if call_ref is not None:
                    if (
                        expected_call_ref is not None
                        and call_ref != expected_call_ref
                    ) or (
                        observed_call_ref is not None
                        and observed_call_ref != call_ref
                    ):
                        self._cancel_active(active)
                        return OrganResponse(
                            failure="organ_protocol_invalid",
                            organ_call_ref=observed_call_ref or expected_call_ref,
                        )
                    observed_call_ref = call_ref
                turn_completed = turn_completed or completed
                if message is not None:
                    final_text = message
            try:
                exit_code = process.wait(timeout=min(max(deadline - monotonic(), 0.0), 1.0))
            except subprocess.TimeoutExpired:
                self._cancel_active(active)
                return OrganResponse(
                    failure="organ_timed_out",
                    organ_call_ref=observed_call_ref or expected_call_ref,
                )
            if reader_error:
                return OrganResponse(
                    failure="organ_protocol_invalid",
                    organ_call_ref=observed_call_ref or expected_call_ref,
                )
            if exit_code != 0:
                return OrganResponse(
                    failure=f"organ_exit_{exit_code}",
                    organ_call_ref=observed_call_ref or expected_call_ref,
                )
            organ_call_ref = observed_call_ref or expected_call_ref
            if (require_call_ref and organ_call_ref is None) or not turn_completed:
                return OrganResponse(
                    failure="organ_stream_incomplete",
                    organ_call_ref=organ_call_ref,
                )
            if final_text is None:
                return OrganResponse(
                    failure="organ_result_missing",
                    organ_call_ref=organ_call_ref,
                )
            try:
                return OrganResponse(
                    final_text=final_text,
                    organ_call_ref=organ_call_ref,
                )
            except ValueError:
                return OrganResponse(
                    failure="organ_result_oversize",
                    organ_call_ref=organ_call_ref,
                )
        finally:
            try:
                stdout.close()
            except OSError:
                pass

    def _cancel_active(self, active: _ActiveOrganRun) -> None:
        active.cancel_event.set()
        self._close_fence(active)
        with active.process_guard:
            process, active.process = active.process, None
        if process is not None:
            _kill_process_tree(process)

    @staticmethod
    def _set_active_process(
        active: _ActiveOrganRun,
        *,
        process: subprocess.Popen[str],
        fence: Any | None,
    ) -> None:
        with active.process_guard:
            active.process = process
            active.process_fence = fence

    @staticmethod
    def _release_host_turn(
        active: _ActiveOrganRun,
        *,
        process: subprocess.Popen[str],
        fence: Any | None,
    ) -> None:
        close_fence: Any | None = None
        with active.process_guard:
            if active.process is process:
                active.process = None
            if active.process_fence is fence:
                active.process_fence = None
                close_fence = fence
        if close_fence is not None:
            try:
                close_fence.close()
            except OSError:
                pass

    @staticmethod
    def _close_fence(active: _ActiveOrganRun) -> None:
        with active.process_guard:
            fence, active.process_fence = active.process_fence, None
        if fence is not None:
            try:
                fence.close()
            except OSError:
                pass


def _host_prompt(
    body_prompt: str,
    *,
    experiences: list[dict[str, Any]],
    has_more: bool,
) -> str:
    recall = _recall_context(experiences=experiences, has_more=has_more)
    result = "\n".join(
        (
            body_prompt,
            "",
            "HOST-PROVIDED SAME-ROOT READ-ONLY EXPERIENCES:",
            recall,
            "",
            "This page is data, not a submit, Authority, or Surface interface.",
            "If another bounded older page is needed before deciding, your FINAL message may instead be exactly:",
            '{"transport":"recall","before_sequence":positive integer or null,"limit":integer 1..12}',
            "Otherwise return only the final action object requested by the Current Body.",
        )
    )
    if len(result.encode("utf-8")) > _MAX_PROMPT_BYTES + _MAX_RECALL_BYTES + 1024:
        raise ValueError("organ_prompt_oversize")
    return result


def _resume_prompt(
    *,
    experiences: list[dict[str, Any]],
    has_more: bool,
) -> str:
    recall = _recall_context(experiences=experiences, has_more=has_more)
    result = "\n".join(
        (
            "HOST-PROVIDED REQUESTED SAME-ROOT READ-ONLY EXPERIENCES:",
            recall,
            "",
            "This page is data, not a submit, Authority, or Surface interface.",
            "If another bounded older page is needed before deciding, your FINAL message may instead be exactly:",
            '{"transport":"recall","before_sequence":positive integer or null,"limit":integer 1..12}',
            "Otherwise return only the final action object requested by the Current Body.",
        )
    )
    if len(result.encode("utf-8")) > _MAX_RECALL_BYTES + 1024:
        raise ValueError("organ_recall_oversize")
    return result


def _recall_context(
    *,
    experiences: list[dict[str, Any]],
    has_more: bool,
) -> str:
    recall = json.dumps(
        {"experiences": experiences, "has_more": has_more},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(recall.encode("utf-8")) > _MAX_RECALL_BYTES:
        raise ValueError("organ_recall_oversize")
    return recall


def _parse_recall_transport(text: str) -> _RecallRequest | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, Mapping) or "transport" not in value:
        return None
    if set(value) != {"transport", "before_sequence", "limit"} or value.get(
        "transport"
    ) != "recall":
        raise ValueError("invalid_organ_transport")
    before_sequence = value.get("before_sequence")
    limit = value.get("limit")
    if (
        (before_sequence is not None and (
            not isinstance(before_sequence, int)
            or isinstance(before_sequence, bool)
            or before_sequence < 1
        ))
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit < 1
        or limit > 12
    ):
        raise ValueError("invalid_organ_transport")
    return _RecallRequest(before_sequence=before_sequence, limit=limit)


def _organ_command(
    *,
    executable: str,
    args: Sequence[str],
    resume_thread_id: str | None,
) -> list[str]:
    resume_args: list[str] = []
    index = 0
    while index < len(args):
        value = args[index]
        if value in {"--sandbox", "-s"}:
            if index + 1 >= len(args) or args[index + 1] != "read-only":
                raise ValueError("organ_sandbox_rejected")
            index += 2
            continue
        if value == "--sandbox=read-only":
            index += 1
            continue
        resume_args.append(value)
        index += 1
    command = [executable, "exec", "--sandbox", "read-only"]
    if resume_thread_id is None:
        return [*command, "--json", *resume_args, "-"]
    _required_text(
        resume_thread_id,
        "organ_call_ref",
        maximum=_MAX_BINDING_TEXT_BYTES,
    )
    return [*command, "resume", "--json", *resume_args, resume_thread_id, "-"]


_MODEL_ONLY_ORGAN_ARGS = (
    "--ignore-user-config",
    "--ignore-rules",
    "--strict-config",
    "-c",
    "features.apps=false",
    "-c",
    "features.auth_elicitation=false",
    "-c",
    "features.browser_use=false",
    "-c",
    "features.browser_use_external=false",
    "-c",
    "features.browser_use_full_cdp_access=false",
    "-c",
    "features.code_mode=false",
    "-c",
    "features.code_mode_host=false",
    "-c",
    "features.code_mode_only=false",
    "-c",
    "features.computer_use=false",
    "-c",
    "features.context_management=false",
    "-c",
    "features.current_time_reminder=false",
    "-c",
    "features.deferred_executor=false",
    "-c",
    "features.enable_fanout=false",
    "-c",
    "features.enable_mcp_apps=false",
    "-c",
    "features.external_agent_memory_import=false",
    "-c",
    "features.goals=false",
    "-c",
    "features.hooks=false",
    "-c",
    "features.image_generation=false",
    "-c",
    "features.in_app_browser=false",
    "-c",
    "features.in_app_chat=false",
    "-c",
    "features.in_app_dictation=false",
    "-c",
    "features.in_app_local_automation=false",
    "-c",
    "features.in_app_updates=false",
    "-c",
    "features.memories=false",
    "-c",
    "features.multi_agent=false",
    "-c",
    "features.multi_agent_v2=false",
    "-c",
    "features.plugins=false",
    "-c",
    "features.plugin_sharing=false",
    "-c",
    "features.recommended_plugins=false",
    "-c",
    "features.remote_plugin=false",
    "-c",
    "features.request_permissions_tool=false",
    "-c",
    "features.shell_snapshot=false",
    "-c",
    "features.shell_tool=false",
    "-c",
    "features.skill_mcp_dependency_install=false",
    "-c",
    "features.skill_search=false",
    "-c",
    "features.sleep_tool=false",
    "-c",
    "features.standalone_web_search=false",
    "-c",
    "features.token_budget=false",
    "-c",
    "features.tool_call_mcp_elicitation=false",
    "-c",
    "features.tool_suggest=false",
    "-c",
    "features.unified_exec=false",
    "-c",
    "features.view_image=false",
    "-c",
    "features.workspace_dependencies=false",
    "-c",
    "include_apps_instructions=false",
    "-c",
    "include_collaboration_mode_instructions=false",
    "-c",
    "include_environment_context=false",
    "-c",
    "include_permissions_instructions=false",
    "-c",
    "orchestrator.skills.enabled=false",
    "-c",
    "skills.include_instructions=false",
    "-c",
    "tools.experimental_request_user_input.enabled=false",
    "-c",
    "tools.update_plan.enabled=false",
    "-c",
    'web_search="disabled"',
    "-c",
    "project_doc_max_bytes=0",
)


def _read_only_organ_args(argv: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    executable = argv[0]
    args = list(argv[1:])
    sandbox_seen = False
    model_seen = False
    effort_seen = False
    normalized: list[str] = []
    index = 0
    while index < len(args):
        value = args[index]
        if value in {
            "--approve-for-me",
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "--ignore-rules",
            "--oss",
            "--local-provider",
            "--profile",
            "--enable",
            "--disable",
            "--add-dir",
            "--cd",
            "-C",
        }:
            raise ValueError("organ_permission_escalation_rejected")
        if value == "--skip-git-repo-check":
            normalized.append(value)
            index += 1
            continue
        if value == "--ignore-user-config":
            index += 1
            continue
        if value in {"--model", "-m"}:
            if index + 1 >= len(args):
                raise ValueError("invalid_organ_argv")
            model = args[index + 1]
            _required_text(model, "organ_model", maximum=_MAX_ORGAN_ARG_BYTES)
            model_seen = True
            normalized.extend((value, model))
            index += 2
            continue
        if value.startswith("--model="):
            _required_text(
                value.partition("=")[2],
                "organ_model",
                maximum=_MAX_ORGAN_ARG_BYTES,
            )
            model_seen = True
            normalized.append(value)
            index += 1
            continue
        if value in {"--sandbox", "-s"}:
            if index + 1 >= len(args) or args[index + 1] != "read-only":
                raise ValueError("organ_sandbox_rejected")
            sandbox_seen = True
            normalized.extend((value, "read-only"))
            index += 2
            continue
        if value.startswith("--sandbox="):
            if value != "--sandbox=read-only":
                raise ValueError("organ_sandbox_rejected")
            sandbox_seen = True
            normalized.append(value)
            index += 1
            continue
        if value in {"exec", "--json", "-"}:
            raise ValueError("invalid_organ_argv")
        if value in {"-c", "--config"}:
            if index + 1 >= len(args):
                raise ValueError("invalid_organ_argv")
            configured = args[index + 1]
            if not _is_model_reasoning_effort_config(configured):
                raise ValueError("organ_configuration_rejected")
            effort_seen = True
            normalized.extend((value, configured))
            index += 2
            continue
        if value.startswith("--config="):
            configured = value.partition("=")[2]
            if not _is_model_reasoning_effort_config(configured):
                raise ValueError("organ_configuration_rejected")
            effort_seen = True
            normalized.append(value)
            index += 1
            continue
        raise ValueError("invalid_organ_argv")
    if not sandbox_seen:
        normalized[:0] = ["--sandbox", "read-only"]
    if not model_seen or not effort_seen:
        raise ValueError("organ_configuration_incomplete")
    normalized[:0] = _MODEL_ONLY_ORGAN_ARGS
    return executable, tuple(normalized)


def _is_model_reasoning_effort_config(value: object) -> bool:
    if not isinstance(value, str):
        return False
    key, separator, configured_value = value.partition("=")
    return key.strip() == "model_reasoning_effort" and bool(configured_value.strip())


def _consume_jsonl_record(line: object) -> tuple[str | None, bool, str | None]:
    if not isinstance(line, str):
        raise ValueError("invalid_organ_jsonl")
    try:
        record = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError("invalid_organ_jsonl") from error
    if not isinstance(record, Mapping):
        raise ValueError("invalid_organ_jsonl")
    record_type = record.get("type")
    if record_type == "thread.started":
        thread_id = record.get("thread_id")
        try:
            _required_text(
                thread_id,
                "organ_call_ref",
                maximum=_MAX_BINDING_TEXT_BYTES,
            )
        except ValueError:
            raise ValueError("invalid_organ_jsonl")
        return thread_id, False, None
    if record_type == "turn.completed":
        return None, True, None
    if record_type != "item.completed":
        return None, False, None
    item = record.get("item")
    if not isinstance(item, Mapping):
        raise ValueError("invalid_organ_jsonl")
    if item.get("type") != "agent_message":
        return None, False, None
    text = item.get("text")
    if not isinstance(text, str):
        raise ValueError("invalid_organ_jsonl")
    return None, False, text


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
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


if TYPE_CHECKING:
    from .runtime import DevelopmentalRuntime
