from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any, Mapping

from ._util import utc_now
from .body import BodyStore
from .errors import AgenticEvoError
from .runtime import SURFACE_CONTEXT_ACTIVATION_KIND, DevelopmentalRuntime
from .trusted import TrustedState


ADOPT_AUTHOR_KIND = "research_instrument"
ADOPT_INGRESS_PATH = "runtime_adopt"
ADOPT_ACTIVATION_ARTIFACT = "entrypoint.md"
ADOPT_TRUSTED_FILES = ("state.sqlite3", "witness.key")


class RuntimeAdoptError(AgenticEvoError):
    """The Genesis home cannot be adopted into the runtime layout."""


def _require_valid_genesis_home(home: Path) -> None:
    for name in ADOPT_TRUSTED_FILES:
        if not (home / name).is_file():
            raise RuntimeAdoptError(
                "home is not a Genesis-born home "
                f"({name} must exist at the home root)"
            )
    if (home / "trusted" / "state.sqlite3").is_file() and (
        home / "trusted" / "witness.key"
    ).is_file():
        raise RuntimeAdoptError(
            "home is already in the runtime layout (trusted/ state exists)"
        )
    if (home / "body").exists():
        raise RuntimeAdoptError("home already contains a Body store")
    if not TrustedState.has_genesis(home):
        raise RuntimeAdoptError("home does not contain a valid Genesis")


def _initial_body_files(snapshot: Any) -> dict[str, str]:
    """The initial Body bundle for a Genesis-born lineage."""

    adopted_at = utc_now()
    entrypoint = (
        "# Genesis-born home \u2014 adopted initial Body\n"
        "\n"
        "This Body is the first servable Body of a home born by the `genesis` "
        "CLI. It was created by `agentic-evo runtime-adopt` so the born home is "
        "immediately servable by the runtime while preserving the born identity.\n"
        "\n"
        f"- born root: {snapshot.root}\n"
        f"- genesis head (lineage parent): {snapshot.head}\n"
        f"- adopted at: {adopted_at}\n"
    )
    identity = (
        "# Identity\n"
        "\n"
        f"who = {snapshot.who}\n"
        "\n"
        f"why = {snapshot.why}\n"
        "\n"
        f"root = {snapshot.root}\n"
        "\n"
        f"genesis_head = {snapshot.head}\n"
    )
    return {"entrypoint.md": entrypoint, "identity.md": identity}


def adopt_genesis_home(home: Path) -> dict[str, Any]:
    """Restructure a Genesis-born home into the servable runtime layout.

    The Genesis CLI births a home by writing the trusted state files
    (``state.sqlite3`` + ``witness.key``) at the home root and nothing else.
    The runtime instead requires ``home/trusted/`` plus ``home/body/``, and the
    trusted Head must be a genuine Body commitment (the serve path boots a
    Body subprocess that verifies ``sha256(manifest) == head``).  Adoption
    therefore commits the initial Body for the born lineage, witnesses a
    ``head_advanced`` transition from the pinned Genesis head to that initial
    Body commitment (who/why/root stay byte-identical and the Genesis evidence
    record is preserved; the pinned Genesis head remains the lineage parent),
    then relocates the trusted state into ``trusted/`` (witness key preserved).

    Returns a JSON-serializable receipt mapping.  Raises
    :class:`RuntimeAdoptError` (or ``OSError``/``ValueError``) on refusal or
    verification failure; the home is left in its pre-adoption state unless
    the failure happens after the trusted state was relocated.
    """

    home = Path(home).resolve()
    _require_valid_genesis_home(home)

    trusted = TrustedState.load(home)
    snapshot = trusted.snapshot()
    records = trusted.records()
    if not records or records[0].event_kind != "genesis":
        raise RuntimeAdoptError("home Genesis evidence is missing or malformed")
    genesis_record = records[0]
    genesis_head = snapshot.head

    body_store = BodyStore(home / "body")
    commitment = body_store.commit(
        root=snapshot.root,
        parent_head=genesis_head,
        files=_initial_body_files(snapshot),
        author_kind=ADOPT_AUTHOR_KIND,
        activation_kind=SURFACE_CONTEXT_ACTIVATION_KIND,
        activation_artifact=ADOPT_ACTIVATION_ARTIFACT,
    )
    candidate = body_store.read_manifest(commitment)

    # Witness the Head transition from the pinned Genesis head to the initial
    # Body commitment.  who/why/root are untouched; the Genesis record stays.
    TrustedState.load(home).advance_head(
        expected_head=genesis_head,
        candidate=candidate,
        author_kind=ADOPT_AUTHOR_KIND,
        ingress_path=ADOPT_INGRESS_PATH,
        human_intervention_kind=None,
    )

    advanced = TrustedState.load(home)
    post_snapshot = advanced.snapshot()
    post_records = advanced.records()
    for field, before, after in (
        ("who", snapshot.who, post_snapshot.who),
        ("why", snapshot.why, post_snapshot.why),
        ("root", snapshot.root, post_snapshot.root),
    ):
        if before != after:
            raise RuntimeAdoptError(
                f"adoption changed the born identity field {field!r}"
            )
    if post_snapshot.head != commitment:
        raise RuntimeAdoptError("adoption did not bind the initial Body as Head")
    if len(post_records) != len(records) + 1:
        raise RuntimeAdoptError("adoption evidence was not appended")
    if post_records[0] != genesis_record:
        raise RuntimeAdoptError("adoption altered the Genesis evidence record")
    adoption_record = post_records[-1]
    if adoption_record.event_kind != "head_advanced":
        raise RuntimeAdoptError("adoption evidence has an unexpected event kind")

    # Relocate the trusted state into trusted/ (copy, verify, then drop the
    # originals) so the runtime layout matches the servable home.
    trusted_dir = home / "trusted"
    trusted_dir.mkdir(parents=True, exist_ok=True)
    for name in ADOPT_TRUSTED_FILES:
        shutil.copy2(home / name, trusted_dir / name)
    relocated = TrustedState.load(trusted_dir)
    if relocated.snapshot() != post_snapshot or relocated.records() != post_records:
        raise RuntimeAdoptError("trusted state relocation failed verification")
    for name in ADOPT_TRUSTED_FILES:
        try:
            (home / name).unlink()
        except OSError:
            pass

    runtime = DevelopmentalRuntime(home)
    status = runtime.status()
    if status.head != commitment or status.root != snapshot.root:
        raise RuntimeAdoptError("adopted home is not servable by the runtime")

    return {
        "home": str(home),
        "identity": {
            "who": post_snapshot.who,
            "why": post_snapshot.why,
            "root": post_snapshot.root,
            "authority": post_snapshot.authority,
        },
        "genesis_head": genesis_head,
        "head": commitment,
        "initial_body": {
            "commitment": commitment,
            "generation": candidate.generation,
            "parent_head": candidate.parent_head,
            "author_kind": candidate.author_kind,
            "activation_kind": candidate.activation_kind,
            "activation_artifact": candidate.activation_artifact,
            "files": candidate.file_names,
        },
        "trusted_layout": {
            "state": "trusted/state.sqlite3",
            "witness_key": "trusted/witness.key",
            "witness_key_preserved": True,
        },
        "evidence": {
            "records": len(post_records),
            "genesis": {
                "sequence": genesis_record.sequence,
                "event_id": genesis_record.event_id,
                "integrity_hash": genesis_record.integrity_hash,
            },
            "adoption": {
                "sequence": adoption_record.sequence,
                "event_id": adoption_record.event_id,
                "event_kind": adoption_record.event_kind,
                "integrity_hash": adoption_record.integrity_hash,
            },
        },
    }


def verify_adopted_home(home: Path) -> Mapping[str, Any]:
    """Read-only verification that an adopted home is servable.

    Loads the runtime exactly as ``serve`` would and returns the status plus
    the evidence summary.  Raises ``AgenticEvoError``/``OSError`` on failure.
    """

    home = Path(home).resolve()
    runtime = DevelopmentalRuntime(home)
    trusted = runtime.trusted
    records = trusted.records()
    status = runtime.status()
    return {
        "servable": True,
        "root": status.root,
        "head": status.head,
        "generation": status.generation,
        "authority": status.authority,
        "lifecycle_state": status.lifecycle_state,
        "evidence_records": len(records),
        "genesis_sequence": records[0].sequence if records else None,
        "genesis_event_kind": records[0].event_kind if records else None,
    }
