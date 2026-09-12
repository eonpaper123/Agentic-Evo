from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Mapping

from ._util import atomic_write_bytes, atomic_write_json, canonical_json_bytes, read_json, sha256_hex, utc_now
from .errors import BodyNotFoundError, IntegrityError, InvalidBodyError


BODY_SCHEMA_VERSION = "agentic-evo-body-v2"
MAX_BODY_LOGICAL_PATH_BYTES = 512
DEVELOPMENT_DESCRIPTOR_UNSET = object()


@dataclass(frozen=True)
class BodyManifest:
    commitment: str
    root: str
    parent_head: str | None
    generation: int
    author_kind: str
    created_at: str
    activation_kind: str | None
    activation_artifact: str | None
    development_kind: str | None
    development_artifact: str | None
    files: tuple[tuple[str, str], ...]

    @property
    def file_names(self) -> tuple[str, ...]:
        return tuple(path for path, _ in self.files)


class BodyStore:
    """Content-addressed opaque body bundles with a small lineage envelope."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.blob_path = self.path / "blobs"
        self.manifest_path = self.path / "manifests"
        self.blob_path.mkdir(parents=True, exist_ok=True)
        self.manifest_path.mkdir(parents=True, exist_ok=True)

    def commit(
        self,
        *,
        root: str,
        parent_head: str | None,
        files: Mapping[str, str | bytes],
        author_kind: str,
        activation_kind: str | None = None,
        activation_artifact: str | None = None,
        development_kind: str | None | object = DEVELOPMENT_DESCRIPTOR_UNSET,
        development_artifact: str | None | object = DEVELOPMENT_DESCRIPTOR_UNSET,
    ) -> str:
        if not root or not author_kind:
            raise InvalidBodyError("root and author_kind are required")
        normalized: dict[str, str] = {}
        for raw_path, value in files.items():
            relative = self._normalize_relative_path(raw_path)
            raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
            blob_hash = sha256_hex(raw)
            blob_target = self.blob_path / blob_hash
            if not blob_target.exists():
                atomic_write_bytes(blob_target, raw)
            normalized[relative] = blob_hash

        generation = 0
        inherited_activation_kind: str | None = None
        inherited_activation: str | None = None
        inherited_development_kind: str | None = None
        inherited_development_artifact: str | None = None
        if parent_head is not None:
            try:
                parent = self.read_manifest(parent_head)
                generation = parent.generation + 1
                inherited_activation_kind = parent.activation_kind
                inherited_activation = parent.activation_artifact
                inherited_development_kind = parent.development_kind
                inherited_development_artifact = parent.development_artifact
            except BodyNotFoundError:
                generation = 1
        selected_activation_kind = (
            activation_kind
            if activation_kind is not None
            else inherited_activation_kind
        )
        selected_activation = (
            activation_artifact
            if activation_artifact is not None
            else inherited_activation
        )
        if (development_kind is DEVELOPMENT_DESCRIPTOR_UNSET) != (
            development_artifact is DEVELOPMENT_DESCRIPTOR_UNSET
        ):
            raise InvalidBodyError(
                "development kind and development artifact must be supplied together"
            )
        if development_kind is DEVELOPMENT_DESCRIPTOR_UNSET:
            selected_development_kind = inherited_development_kind
            selected_development_artifact = inherited_development_artifact
        else:
            selected_development_kind = development_kind
            selected_development_artifact = development_artifact
        if selected_activation_kind is not None:
            if (
                not isinstance(selected_activation_kind, str)
                or not selected_activation_kind
                or len(selected_activation_kind) > 128
                or any(character.isspace() for character in selected_activation_kind)
            ):
                raise InvalidBodyError("activation kind must be a compact non-empty string")
        if (selected_activation_kind is None) != (selected_activation is None):
            raise InvalidBodyError(
                "activation kind and activation artifact must be specified together"
            )
        if selected_activation is not None:
            selected_activation = self._normalize_relative_path(selected_activation)
            if selected_activation not in normalized:
                raise InvalidBodyError(
                    "activation artifact is not present in body files"
                )
        if selected_development_kind is not None:
            if (
                not isinstance(selected_development_kind, str)
                or not selected_development_kind
                or len(selected_development_kind) > 128
                or any(character.isspace() for character in selected_development_kind)
            ):
                raise InvalidBodyError(
                    "development kind must be a compact non-empty string"
                )
        if (selected_development_kind is None) != (
            selected_development_artifact is None
        ):
            raise InvalidBodyError(
                "development kind and development artifact must be specified together"
            )
        if selected_development_artifact is not None:
            selected_development_artifact = self._normalize_relative_path(
                selected_development_artifact
            )
            if selected_development_artifact not in normalized:
                raise InvalidBodyError(
                    "development artifact is not present in body files"
                )

        manifest = {
            "schema_version": BODY_SCHEMA_VERSION,
            "root": root,
            "parent_head": parent_head,
            "generation": generation,
            "author_kind": author_kind,
            "created_at": utc_now(),
            "activation_kind": selected_activation_kind,
            "activation_artifact": selected_activation,
            "development_kind": selected_development_kind,
            "development_artifact": selected_development_artifact,
            "files": dict(sorted(normalized.items())),
        }
        commitment = sha256_hex(canonical_json_bytes(manifest))
        target = self.manifest_path / f"{commitment}.json"
        if not target.exists():
            atomic_write_json(target, manifest)
        return commitment

    def read_manifest(self, commitment: str) -> BodyManifest:
        target = self.manifest_path / f"{commitment}.json"
        if not target.is_file():
            raise BodyNotFoundError(f"body manifest does not exist: {commitment}")
        manifest = read_json(target)
        if manifest.get("schema_version") != BODY_SCHEMA_VERSION:
            raise IntegrityError("unsupported body manifest schema")
        actual = sha256_hex(canonical_json_bytes(manifest))
        if actual != commitment:
            raise IntegrityError("body manifest commitment mismatch")

        raw_files = manifest.get("files")
        if not isinstance(raw_files, dict):
            raise IntegrityError("body manifest files must be an object")
        files: list[tuple[str, str]] = []
        for raw_path, blob_hash in sorted(raw_files.items()):
            path = self._normalize_relative_path(str(raw_path))
            if not isinstance(blob_hash, str) or len(blob_hash) != 64:
                raise IntegrityError("invalid body blob commitment")
            blob_target = self.blob_path / blob_hash
            if not blob_target.is_file():
                raise IntegrityError(f"missing body blob: {blob_hash}")
            if sha256_hex(blob_target.read_bytes()) != blob_hash:
                raise IntegrityError(f"body blob commitment mismatch: {blob_hash}")
            files.append((path, blob_hash))

        try:
            root = str(manifest["root"])
            parent_head = manifest.get("parent_head")
            generation = int(manifest["generation"])
            author_kind = str(manifest["author_kind"])
            created_at = str(manifest["created_at"])
            activation_kind = manifest.get("activation_kind")
            activation_artifact = manifest.get("activation_artifact")
            development_kind = manifest.get("development_kind")
            development_artifact = manifest.get("development_artifact")
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrityError("invalid body manifest envelope") from exc
        if parent_head is not None:
            parent_head = str(parent_head)
        if generation < 0:
            raise IntegrityError("body generation cannot be negative")
        if activation_kind is not None:
            if (
                not isinstance(activation_kind, str)
                or not activation_kind
                or len(activation_kind) > 128
                or any(character.isspace() for character in activation_kind)
            ):
                raise IntegrityError("invalid activation kind")
        if (activation_kind is None) != (activation_artifact is None):
            raise IntegrityError(
                "activation kind and activation artifact must be specified together"
            )
        if activation_artifact is not None:
            activation_artifact = self._normalize_relative_path(
                str(activation_artifact)
            )
            if activation_artifact not in dict(files):
                raise IntegrityError("activation artifact is not present in body files")
        if development_kind is not None:
            if (
                not isinstance(development_kind, str)
                or not development_kind
                or len(development_kind) > 128
                or any(character.isspace() for character in development_kind)
            ):
                raise IntegrityError("invalid development kind")
        if (development_kind is None) != (development_artifact is None):
            raise IntegrityError(
                "development kind and development artifact must be specified together"
            )
        if development_artifact is not None:
            development_artifact = self._normalize_relative_path(
                str(development_artifact)
            )
            if development_artifact not in dict(files):
                raise IntegrityError("development artifact is not present in body files")
        return BodyManifest(
            commitment=commitment,
            root=root,
            parent_head=parent_head,
            generation=generation,
            author_kind=author_kind,
            created_at=created_at,
            activation_kind=activation_kind,
            activation_artifact=activation_artifact,
            development_kind=development_kind,
            development_artifact=development_artifact,
            files=tuple(files),
        )

    def read_file(self, commitment: str, relative_path: str) -> bytes:
        manifest = self.read_manifest(commitment)
        normalized = self._normalize_relative_path(relative_path)
        file_map = dict(manifest.files)
        if normalized not in file_map:
            raise BodyNotFoundError(
                f"body file {normalized!r} does not exist in {commitment}"
            )
        expected_digest = file_map[normalized]
        raw = (self.blob_path / expected_digest).read_bytes()
        if sha256_hex(raw) != expected_digest:
            raise IntegrityError(
                f"body blob commitment mismatch during read: {expected_digest}"
            )
        return raw

    def export_manifest(self, commitment: str) -> dict[str, object]:
        manifest = self.read_manifest(commitment)
        raw_manifest = (self.manifest_path / f"{commitment}.json").read_bytes()
        blobs = {
            blob_hash: base64.b64encode(
                (self.blob_path / blob_hash).read_bytes()
            ).decode("ascii")
            for _, blob_hash in manifest.files
        }
        return {
            "commitment": commitment,
            "manifest_base64": base64.b64encode(raw_manifest).decode("ascii"),
            "blobs": blobs,
        }

    def import_manifest(self, package: Mapping[str, object]) -> str:
        try:
            commitment = str(package["commitment"])
            manifest_bytes = base64.b64decode(str(package["manifest_base64"]))
            blobs = package["blobs"]
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidBodyError("invalid body export package") from exc
        if not isinstance(blobs, Mapping):
            raise InvalidBodyError("body export blobs must be a mapping")
        for blob_hash, encoded in blobs.items():
            raw = base64.b64decode(str(encoded))
            if sha256_hex(raw) != str(blob_hash):
                raise IntegrityError("imported body blob commitment mismatch")
            target = self.blob_path / str(blob_hash)
            if not target.exists():
                atomic_write_bytes(target, raw)
        try:
            decoded_manifest = json.loads(manifest_bytes)
        except (ValueError, TypeError) as exc:
            raise InvalidBodyError("invalid imported body manifest JSON") from exc
        if sha256_hex(canonical_json_bytes(decoded_manifest)) != commitment:
            raise IntegrityError("imported body manifest commitment mismatch")
        atomic_write_json(
            self.manifest_path / f"{commitment}.json",
            decoded_manifest,
        )
        self.read_manifest(commitment)
        return commitment

    @staticmethod
    def _normalize_relative_path(raw_path: str) -> str:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise InvalidBodyError("body file path must be a non-empty string")
        candidate = raw_path.replace("\\", "/")
        pure = PurePosixPath(candidate)
        if (
            not pure.parts
            or pure.is_absolute()
            or ".." in pure.parts
            or "." in pure.parts
        ):
            raise InvalidBodyError(f"unsafe body file path: {raw_path}")
        normalized = pure.as_posix()
        if normalized.startswith("/") or ":" in pure.parts[0]:
            raise InvalidBodyError(f"unsafe body file path: {raw_path}")
        if len(normalized.encode("utf-8")) > MAX_BODY_LOGICAL_PATH_BYTES:
            raise InvalidBodyError("body file path exceeds its portable byte bound")
        return normalized
