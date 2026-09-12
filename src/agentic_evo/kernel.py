from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from pathlib import Path
import secrets
from typing import Any

from ._util import ExclusiveFileLock, atomic_write_bytes, atomic_write_json, canonical_json_bytes, read_json, sha256_hex
from .body import BodyManifest
from .errors import (
    AuthorityError,
    GenesisExistsError,
    HeadConflictError,
    IntegrityError,
    RootBindingError,
    RuntimeOffError,
)


KERNEL_SCHEMA_VERSION = "agentic-evo-kernel-v1"


@dataclass(frozen=True)
class KernelSnapshot:
    who: str
    why: str
    authority: str
    root: str
    head: str

    @property
    def is_on(self) -> bool:
        return self.authority == "on"


class MicroLifeKernel:
    """Constant-size identity and Head commitment; it does not evaluate body semantics."""

    def __init__(self, path: Path, key: bytes, state: dict[str, Any]) -> None:
        self.path = Path(path)
        self._key = key
        self._state = state

    @classmethod
    def generate_root(cls) -> str:
        return secrets.token_hex(32)

    @classmethod
    def genesis(
        cls,
        path: Path,
        *,
        host_binding: str,
        purpose_anchor: str,
        root: str,
        initial_head: str,
    ) -> "MicroLifeKernel":
        path = Path(path)
        state_path = path / "state.json"
        key_path = path / "authority.key"
        if state_path.exists() or key_path.exists():
            raise GenesisExistsError("kernel Genesis already exists")
        path.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        atomic_write_bytes(key_path, key)
        unsigned = {
            "schema_version": KERNEL_SCHEMA_VERSION,
            "who": sha256_hex(host_binding),
            "why": sha256_hex(purpose_anchor),
            "authority": "on",
            "root": root,
            "head": initial_head,
        }
        state = cls._signed_state(unsigned, key)
        atomic_write_json(state_path, state)
        return cls(path, key, state)

    @classmethod
    def load(cls, path: Path) -> "MicroLifeKernel":
        path = Path(path)
        try:
            key = (path / "authority.key").read_bytes()
        except OSError as exc:
            raise IntegrityError("kernel authority key is unavailable") from exc
        if len(key) != 32:
            raise IntegrityError("kernel authority key has an invalid length")
        state = read_json(path / "state.json")
        cls._verify_state(state, key)
        return cls(path, key, state)

    def snapshot(self) -> KernelSnapshot:
        self._reload()
        return KernelSnapshot(
            who=str(self._state["who"]),
            why=str(self._state["why"]),
            authority=str(self._state["authority"]),
            root=str(self._state["root"]),
            head=str(self._state["head"]),
        )

    def gate(self) -> KernelSnapshot:
        snapshot = self.snapshot()
        if not snapshot.is_on:
            raise RuntimeOffError("runtime is off")
        return snapshot

    def bind(self, *, root: str, head: str) -> KernelSnapshot:
        snapshot = self.gate()
        if root != snapshot.root:
            raise RootBindingError("body Root does not match kernel Root")
        if head != snapshot.head:
            raise HeadConflictError("body Head does not match current kernel Head")
        return snapshot

    def advance_head(
        self,
        *,
        expected_head: str,
        candidate: BodyManifest,
    ) -> KernelSnapshot:
        with ExclusiveFileLock(self.path / ".kernel.lock"):
            state = read_json(self.path / "state.json")
            self._verify_state(state, self._key)
            if state["authority"] != "on":
                raise RuntimeOffError("runtime is off")
            if state["head"] != expected_head:
                raise HeadConflictError("Head changed before this transition")
            if candidate.root != state["root"]:
                raise RootBindingError("candidate body belongs to another Root")
            if candidate.parent_head != expected_head:
                raise HeadConflictError("candidate parent is not the expected Head")
            unsigned = dict(state)
            unsigned.pop("signature", None)
            unsigned["head"] = candidate.commitment
            state = self._signed_state(unsigned, self._key)
            atomic_write_json(self.path / "state.json", state)
            self._state = state
        return self.snapshot()

    def turn_off(self) -> KernelSnapshot:
        return self._set_authority("off")

    def turn_on(self, *, host_binding: str) -> KernelSnapshot:
        snapshot = self.snapshot()
        if not hmac.compare_digest(snapshot.who, sha256_hex(host_binding)):
            raise AuthorityError("host binding does not match kernel authority")
        return self._set_authority("on")

    def _set_authority(self, value: str) -> KernelSnapshot:
        with ExclusiveFileLock(self.path / ".kernel.lock"):
            state = read_json(self.path / "state.json")
            self._verify_state(state, self._key)
            unsigned = dict(state)
            unsigned.pop("signature", None)
            unsigned["authority"] = value
            state = self._signed_state(unsigned, self._key)
            atomic_write_json(self.path / "state.json", state)
            self._state = state
        return self.snapshot()

    def _reload(self) -> None:
        state = read_json(self.path / "state.json")
        self._verify_state(state, self._key)
        self._state = state

    @staticmethod
    def _signed_state(unsigned: dict[str, Any], key: bytes) -> dict[str, Any]:
        value = dict(unsigned)
        signature = hmac.new(
            key,
            canonical_json_bytes(value),
            hashlib.sha256,
        ).hexdigest()
        value["signature"] = signature
        return value

    @staticmethod
    def _verify_state(state: dict[str, Any], key: bytes) -> None:
        signature = state.get("signature")
        unsigned = dict(state)
        unsigned.pop("signature", None)
        if unsigned.get("schema_version") != KERNEL_SCHEMA_VERSION:
            raise IntegrityError("unsupported kernel schema")
        if unsigned.get("authority") not in {"on", "off"}:
            raise IntegrityError("invalid kernel authority state")
        for field in ("who", "why", "root", "head"):
            if not isinstance(unsigned.get(field), str) or not unsigned[field]:
                raise IntegrityError(f"invalid kernel field: {field}")
        expected = hmac.new(
            key,
            canonical_json_bytes(unsigned),
            hashlib.sha256,
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature,
            expected,
        ):
            raise IntegrityError("kernel state signature mismatch")
