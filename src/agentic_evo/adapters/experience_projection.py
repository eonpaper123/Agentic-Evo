from __future__ import annotations

import re
from typing import Any, Mapping

from .._util import canonical_json_bytes
from ..evidence import EvidenceLedger
from ..errors import SensitiveContentError


MAX_VISIBLE_TEXT_BYTES = 2 * 1024
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_AUTHORIZATION_LINE = re.compile(
    r"(?im)^[ \t]*(?:authorization\s*:|bearer\s+).*$"
)
_INLINE_BEARER_AUTHORIZATION = re.compile(
    r"(?i)(?P<prefix>authorization\s*:\s*bearer\s+)(?P<credential>[^\s'\"]+)"
)
_JSON_API_KEY_VALUE = re.compile(
    r'(?i)(?P<prefix>"api_key"\s*:\s*")'
    r'(?P<credential>(?:\\.|[^"\\])*)'
    r'(?P<suffix>")'
)
_ASSIGNMENT_LINE = re.compile(
    r"^(?P<prefix>[ \t]*)(?P<name>[A-Za-z_][A-Za-z0-9_-]*)\s*=.*$"
)
_POWERSHELL_ENV_ASSIGNMENT_LINE = re.compile(
    r"^[ \t]*\$env:(?P<name>[A-Za-z_][A-Za-z0-9_-]*)\s*=.*$",
    re.IGNORECASE,
)
_ADDITIONAL_SENSITIVE_KEYS = frozenset(
    {"api_key", "apikey", "openai_api_key"}
)


def visible_json(value: Any) -> tuple[str, bool, bool]:
    """Return bounded JSON plus redaction and truncation facts."""

    scrubbed, redacted = _scrub_visible_value(value)
    text = canonical_json_bytes(scrubbed).decode("utf-8")
    bounded, truncated = _truncate_text(text)
    return bounded, redacted, truncated


def visible_text(value: Any) -> tuple[str, bool, bool]:
    """Return bounded task-visible text plus redaction and truncation facts."""

    text, redacted = _scrub_visible_text(str(value or ""))
    bounded, truncated = _truncate_text(text)
    return bounded, redacted, truncated


def bounded_text(value: Any, limit: int = 256) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def _scrub_visible_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        redacted = False
        for key, item in value.items():
            if not isinstance(key, str) or _is_sensitive_key(key):
                redacted = True
                continue
            clean_item, item_redacted = _scrub_visible_value(item)
            result[key] = clean_item
            redacted = redacted or item_redacted
        return result, redacted
    if isinstance(value, (list, tuple)):
        result: list[Any] = []
        redacted = False
        for item in value:
            clean_item, item_redacted = _scrub_visible_value(item)
            result.append(clean_item)
            redacted = redacted or item_redacted
        return result, redacted
    if isinstance(value, str):
        return _scrub_visible_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    return None, True


def _scrub_visible_text(value: str) -> tuple[str, bool]:
    redacted = False

    def replace_private_key(_: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        return "[redacted private key]"

    text = _PRIVATE_KEY_BLOCK.sub(replace_private_key, value)

    def replace_inline_bearer(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        return f"{match['prefix']}[redacted credential]"

    text = _INLINE_BEARER_AUTHORIZATION.sub(replace_inline_bearer, text)

    def replace_json_api_key(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        return f'{match["prefix"]}[redacted credential]{match["suffix"]}'

    text = _JSON_API_KEY_VALUE.sub(replace_json_api_key, text)
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        if _AUTHORIZATION_LINE.fullmatch(line.rstrip("\r\n")):
            lines.append("[redacted credential]" + _line_ending(line))
            redacted = True
            continue
        powershell_assignment = _POWERSHELL_ENV_ASSIGNMENT_LINE.fullmatch(
            line.rstrip("\r\n")
        )
        if (
            powershell_assignment is not None
            and _is_sensitive_key(powershell_assignment["name"])
        ):
            lines.append("[redacted credential]" + _line_ending(line))
            redacted = True
            continue
        assignment = _ASSIGNMENT_LINE.fullmatch(line.rstrip("\r\n"))
        if assignment is not None and _is_sensitive_key(assignment["name"]):
            lines.append("[redacted credential]" + _line_ending(line))
            redacted = True
            continue
        lines.append(line)
    return "".join(lines), redacted


def _is_sensitive_key(key: str) -> bool:
    if key.casefold() in _ADDITIONAL_SENSITIVE_KEYS:
        return True
    try:
        EvidenceLedger._reject_sensitive_keys({key: None})
    except SensitiveContentError:
        return True
    return False


def _truncate_text(value: str) -> tuple[str, bool]:
    raw = value.encode("utf-8")
    if len(raw) <= MAX_VISIBLE_TEXT_BYTES:
        return value, False
    return raw[:MAX_VISIBLE_TEXT_BYTES].decode("utf-8", errors="ignore"), True


def _line_ending(value: str) -> str:
    if value.endswith("\r\n"):
        return "\r\n"
    if value.endswith("\n"):
        return "\n"
    return ""
