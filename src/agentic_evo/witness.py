from __future__ import annotations

import math
import threading
import time
from typing import Callable, Mapping

from ._util import ExclusiveFileLock
from .errors import (
    AuthorityError,
    BodyLeaseError,
    HeadConflictError,
    RuntimeOffError,
)
from .runtime import DevelopmentalRuntime, RuntimeStatus


_REHEARSAL_AUTHOR = "in_process_rehearsal"
_REHEARSAL_INGRESS = "in_process_rehearsal"


class CurrentBodySession:
    """Narrow lineage channel for one volatile Current Body lease."""

    def __init__(
        self,
        witness: "WitnessCore",
        *,
        root: str,
        head: str,
        authority_epoch: int,
        expires_at: float,
        file_lock: ExclusiveFileLock,
    ) -> None:
        self._witness = witness
        self._root = root
        self._head = head
        self._authority_epoch = authority_epoch
        self._expires_at = expires_at
        self._file_lock = file_lock
        self._prepared_candidates: set[str] = set()

    def prepare_successor(
        self,
        *,
        files: Mapping[str, str | bytes],
        activation_kind: str | None = None,
        activation_artifact: str | None = None,
    ) -> str:
        return self._witness._prepare_successor(
            self,
            files=files,
            activation_kind=activation_kind,
            activation_artifact=activation_artifact,
        )

    def advance_head(self, *, candidate_head: str) -> RuntimeStatus:
        return self._witness._advance_head(
            self,
            candidate_head=candidate_head,
        )

    def close(self) -> None:
        self._witness._close_lease(self)


class WitnessCore:
    """In-process rehearsal of a private Witness-owned Body channel.

    Object identity and the held OS lock model lease semantics. They do not
    authenticate a process principal; that boundary belongs to the service/IPC
    slice.
    """

    def __init__(
        self,
        runtime: DevelopmentalRuntime,
        *,
        lease_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._runtime = runtime
        self._lease_seconds = lease_seconds
        self._clock = clock
        self._guard = threading.RLock()
        self._lease: CurrentBodySession | None = None

    def open_current_body_session(
        self,
        *,
        expected_head: str,
    ) -> CurrentBodySession:
        with self._guard:
            status, authority_epoch = self._runtime._body_lease_binding()
            if status.authority != "on":
                self._retire_lease()
                raise RuntimeOffError("runtime is off")
            if expected_head != status.head:
                raise HeadConflictError("lease must bind the exact Current Head")

            if self._lease is not None:
                if (
                    self._clock() >= self._lease._expires_at
                    or self._lease._root != status.root
                    or self._lease._head != status.head
                    or self._lease._authority_epoch != authority_epoch
                ):
                    self._retire_lease()
                else:
                    raise BodyLeaseError("a Current Body lease is already live")

            file_lock = ExclusiveFileLock(
                self._runtime.runtime_path / ".body-session.lock",
                timeout_seconds=0.0,
            )
            try:
                file_lock.__enter__()
            except TimeoutError as exc:
                raise BodyLeaseError(
                    "another Witness already holds the Current Body lease"
                ) from exc
            try:
                locked_status, locked_epoch = (
                    self._runtime._body_lease_binding()
                )
                if locked_status.authority != "on":
                    raise RuntimeOffError("runtime is off")
                if (
                    locked_status.root != status.root
                    or locked_status.head != expected_head
                ):
                    raise HeadConflictError(
                        "trusted binding changed during lease issuance"
                    )
            except BaseException:
                file_lock.__exit__(None, None, None)
                raise

            lease = CurrentBodySession(
                self,
                root=status.root,
                head=status.head,
                authority_epoch=locked_epoch,
                expires_at=self._clock() + self._lease_seconds,
                file_lock=file_lock,
            )
            self._lease = lease
            return lease

    def revoke_current_body_session(self) -> None:
        with self._guard:
            self._retire_lease()

    def turn_off(self) -> RuntimeStatus:
        with self._guard:
            status = self._runtime.turn_off()
            self._retire_lease()
            return status

    def turn_on(self, *, host_binding: str) -> RuntimeStatus:
        with self._guard:
            return self._runtime.turn_on(host_binding=host_binding)

    def close(self) -> None:
        with self._guard:
            self._retire_lease()

    def _prepare_successor(
        self,
        lease: CurrentBodySession,
        *,
        files: Mapping[str, str | bytes],
        activation_kind: str | None,
        activation_artifact: str | None,
    ) -> str:
        with self._guard:
            self._authorize(lease)
            candidate = self._runtime._prepare_successor(
                expected_parent=lease._head,
                files=files,
                author_kind=_REHEARSAL_AUTHOR,
                ingress_path=_REHEARSAL_INGRESS,
                expected_authority_epoch=lease._authority_epoch,
                activation_kind=activation_kind,
                activation_artifact=activation_artifact,
            )
            lease._prepared_candidates.add(candidate)
            return candidate

    def _advance_head(
        self,
        lease: CurrentBodySession,
        *,
        candidate_head: str,
    ) -> RuntimeStatus:
        with self._guard:
            self._authorize(lease)
            if candidate_head not in lease._prepared_candidates:
                raise AuthorityError(
                    "candidate was not prepared through this Body lease"
                )
            status = self._runtime._advance_head(
                expected_head=lease._head,
                candidate_head=candidate_head,
                author_kind=_REHEARSAL_AUTHOR,
                ingress_path=_REHEARSAL_INGRESS,
                expected_authority_epoch=lease._authority_epoch,
            )
            self._retire_lease()
            return status

    def _close_lease(self, lease: CurrentBodySession) -> None:
        with self._guard:
            if lease is self._lease:
                self._retire_lease()

    def _authorize(self, lease: CurrentBodySession) -> None:
        with self._guard:
            if lease is not self._lease:
                raise BodyLeaseError("Current Body lease is no longer active")
            if self._clock() >= lease._expires_at:
                self._retire_lease()
                raise BodyLeaseError("Current Body lease expired")

            status, authority_epoch = self._runtime._body_lease_binding()
            if (
                status.authority != "on"
                or status.root != lease._root
                or status.head != lease._head
                or authority_epoch != lease._authority_epoch
            ):
                self._retire_lease()
                raise BodyLeaseError(
                    "Current Body lease lost its trusted binding"
                )

    def _retire_lease(self) -> None:
        lease = self._lease
        self._lease = None
        if lease is not None:
            lease._file_lock.__exit__(None, None, None)
