from __future__ import annotations

"""Pure Body-development entrypoint contract.

This module deliberately does not make Python source safe to execute.  The
caller must provide the established LPAC process boundary; this module only
validates the source, context, and return transport contract.
"""

from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Mapping, Protocol


DEVELOPMENT_ENTRYPOINT_KIND = "python-development-v1"
MAX_DEVELOPMENT_ENTRYPOINT_SOURCE_BYTES = 512 * 1024
MAX_DEVELOPMENT_ENTRYPOINT_RESULT_BYTES = 128 * 1024
_MAX_RECALL_LIMIT = 12
_MAX_ORGAN_PROMPT_BYTES = 512 * 1024


class DevelopmentEntrypointError(RuntimeError):
    """A stable failure at the Body-development entrypoint boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RecallPort(Protocol):
    def __call__(
        self,
        before_sequence: int | None,
        limit: int,
    ) -> Mapping[str, object]:
        ...


class OrganPort(Protocol):
    def __call__(self, prompt: str) -> Mapping[str, object]:
        ...


@dataclass(frozen=True)
class DevelopmentContext:
    """The only facts and host ports offered to a Body development source."""

    opportunity: MappingProxyType
    body_files: MappingProxyType
    recall_port: RecallPort
    organ_port: OrganPort

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "opportunity",
            _freeze_mapping(self.opportunity, code="invalid_opportunity"),
        )
        object.__setattr__(
            self,
            "body_files",
            _freeze_mapping(self.body_files, code="invalid_body_files"),
        )
        if not callable(self.recall_port):
            raise DevelopmentEntrypointError("invalid_recall_port")
        if not callable(self.organ_port):
            raise DevelopmentEntrypointError("invalid_organ_port")

    def recall(
        self,
        *,
        before_sequence: int | None = None,
        limit: int = _MAX_RECALL_LIMIT,
    ) -> MappingProxyType:
        if before_sequence is not None and (
            not isinstance(before_sequence, int)
            or isinstance(before_sequence, bool)
            or before_sequence < 1
        ):
            raise DevelopmentEntrypointError("invalid_recall_before_sequence")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= _MAX_RECALL_LIMIT
        ):
            raise DevelopmentEntrypointError("invalid_recall_limit")
        try:
            result = self.recall_port(before_sequence, limit)
        except Exception as error:
            raise DevelopmentEntrypointError("recall_port_failed") from error
        return _freeze_mapping(result, code="invalid_recall_result")

    def invoke_organ(self, prompt: str) -> MappingProxyType:
        _bounded_utf8_text(
            prompt,
            code="invalid_organ_prompt",
            maximum=_MAX_ORGAN_PROMPT_BYTES,
        )
        try:
            result = self.organ_port(prompt)
        except Exception as error:
            raise DevelopmentEntrypointError("organ_port_failed") from error
        return _freeze_mapping(result, code="invalid_organ_result")


def run_development_entrypoint(
    source: str,
    *,
    context: DevelopmentContext,
) -> dict[str, object]:
    """Load a Body artifact and call its explicit ``develop(context)`` entrypoint."""

    if not isinstance(context, DevelopmentContext):
        raise DevelopmentEntrypointError("invalid_context")
    _bounded_utf8_text(
        source,
        code="source_invalid",
        maximum=MAX_DEVELOPMENT_ENTRYPOINT_SOURCE_BYTES,
        oversize_code="source_oversize",
    )
    try:
        code = compile(
            source,
            "<agentic-evo-development-entrypoint>",
            "exec",
        )
    except (SyntaxError, ValueError, OverflowError) as error:
        raise DevelopmentEntrypointError("source_compile_failed") from error

    namespace: dict[str, object] = {
        "__name__": "__agentic_evo_development_entrypoint__",
    }
    try:
        exec(code, namespace, namespace)
    except Exception as error:
        raise DevelopmentEntrypointError("entrypoint_execution_failed") from error
    develop = namespace.get("develop")
    if not callable(develop):
        raise DevelopmentEntrypointError("entrypoint_missing")

    try:
        result = develop(context)
    except DevelopmentEntrypointError:
        raise
    except Exception as error:
        raise DevelopmentEntrypointError("entrypoint_invocation_failed") from error
    return _round_trip_result(result)


def _bounded_utf8_text(
    value: object,
    *,
    code: str,
    maximum: int,
    oversize_code: str | None = None,
) -> str:
    if not isinstance(value, str) or not value:
        raise DevelopmentEntrypointError(code)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise DevelopmentEntrypointError(code) from error
    if len(encoded) > maximum:
        raise DevelopmentEntrypointError(oversize_code or code)
    return value


def _freeze_mapping(value: object, *, code: str) -> MappingProxyType:
    if not isinstance(value, Mapping):
        raise DevelopmentEntrypointError(code)
    frozen: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise DevelopmentEntrypointError(code)
        frozen[key] = _freeze_value(item, code=code)
    return MappingProxyType(frozen)


def _freeze_value(value: object, *, code: str) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping(value, code=code)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item, code=code) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise DevelopmentEntrypointError(code)


def _round_trip_result(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise DevelopmentEntrypointError("entrypoint_return_invalid")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise DevelopmentEntrypointError("entrypoint_return_not_json") from error
    if len(encoded) > MAX_DEVELOPMENT_ENTRYPOINT_RESULT_BYTES:
        raise DevelopmentEntrypointError("entrypoint_return_oversize")
    try:
        result = json.loads(encoded)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DevelopmentEntrypointError("entrypoint_return_not_json") from error
    if not isinstance(result, dict):
        raise DevelopmentEntrypointError("entrypoint_return_invalid")
    return result
