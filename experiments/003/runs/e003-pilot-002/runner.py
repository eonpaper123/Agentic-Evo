from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


RUN_DIR = Path(__file__).resolve().parent
REPO = RUN_DIR.parents[3]
MANIFEST_PATH = RUN_DIR / "manifest.json"
TASKS_PATH = RUN_DIR / "tasks.json"
RESULTS = RUN_DIR / "results"
HISTORIES_PATH = RESULTS / "histories.json"
LEDGER_PATH = RESULTS / "interventions.jsonl"
RESERVED_CHECK = "external_checks.py"


class RunError(RuntimeError):
    pass


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise RunError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _lines(value: list[str]) -> str:
    return "\n".join(value) + "\n"


def _materialize_files(root: Path, files: dict[str, list[str]]) -> None:
    for relative, lines in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_lines(lines), encoding="utf-8")


def _task_index(tasks: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = tasks["source_episodes"] + tasks["evaluation_tasks"]
    return {item["task_id"]: item for item in items}


def _expected_units(manifest: dict[str, Any], tasks: dict[str, Any], phase: str):
    index = _task_index(tasks)
    order = manifest["run_order"]
    replicas = order["replica_order"]
    arm_orders = order["counterbalanced_arm_orders"]
    for task_position, task_id in enumerate(order["task_order_by_phase"][phase]):
        task = index[task_id]
        for replica_position, replica in enumerate(replicas):
            arms = arm_orders[task_position * len(replicas) + replica_position]
            for arm in arms:
                unit_id = manifest["arm_assignment"]["unit_id_format"].format(
                    phase=phase,
                    task_id=task_id,
                    arm=arm,
                    replica=replica,
                )
                yield unit_id, task, arm, replica


def _validate_registration(manifest: dict[str, Any], tasks: dict[str, Any]) -> None:
    prereg = _read_object(REPO / manifest["protocol_ref"])
    missing = [
        field
        for field in prereg["future_run_registration_required_fields"]
        if field not in manifest
    ]
    if missing:
        raise RunError(f"missing required manifest fields: {missing}")
    if manifest["run_id"] != "e003-pilot-002":
        raise RunError("unexpected run_id")
    if manifest["execution_status"] != "not_run":
        raise RunError("registered manifest must remain not_run")
    if manifest["replication_count"] != len(manifest["arm_assignment"]["replicas"]):
        raise RunError("replication_count does not match assignments")

    index = _task_index(tasks)
    ordered = manifest["run_order"]
    expected_task_ids = set(ordered["source_task_ids"])
    for phase in ordered["phase_order"]:
        expected_task_ids.update(ordered["task_order_by_phase"][phase])
    if expected_task_ids != set(index):
        raise RunError("run order and task pool differ")
    if len(index) != len(tasks["source_episodes"]) + len(tasks["evaluation_tasks"]):
        raise RunError("duplicate task_id")

    source_pairs = {item["pair_id"] for item in tasks["source_episodes"]}
    evaluation_pairs = {item["pair_id"] for item in tasks["evaluation_tasks"]}
    mapping = manifest["shuffled_memory_derangement_rule_and_frozen_mapping"]["mapping"]
    if source_pairs != evaluation_pairs or set(mapping) != source_pairs:
        raise RunError("pair sets do not match")
    if any(target == pair or mapping.get(target) != pair for pair, target in mapping.items()):
        raise RunError("mapping is not a two-way derangement")
    arms = set(manifest["arm_assignment"]["arms"])
    arm_orders = ordered["counterbalanced_arm_orders"]
    if len(arm_orders) != 4 or any(set(order) != arms for order in arm_orders):
        raise RunError("counterbalanced arm orders are incomplete")
    for position in range(4):
        if {order[position] for order in arm_orders} != arms:
            raise RunError("arm orders are not position-balanced")
    expected_permissions = {
        "*": "deny",
        "read": "allow",
        "edit": "allow",
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
    }
    surface = manifest["execution_surface"]
    config = surface["config"]
    if (
        surface["arguments"]
        != ["run", "--pure", "--auto", "--format", "json", "--agent", "e003-pilot"]
        or config.get("share") != "disabled"
        or config["agent"]["e003-pilot"]["permission"] != expected_permissions
    ):
        raise RunError("OpenCode invocation boundary differs from registered policy")
    run_path = "experiments/003/runs/e003-pilot-002/"
    if manifest["target_episode_refs"] != [
        run_path + "tasks.json#/source_episodes/0",
        run_path + "tasks.json#/source_episodes/1",
    ]:
        raise RunError("source episode references differ from task pool")
    if manifest["held_out_task_pack_ref"] != run_path + "tasks.json#/evaluation_tasks":
        raise RunError("held-out task reference differs from task pool")
    expected_evaluators = [
        run_path + f"tasks.json#/evaluation_tasks/{index}/external_checks"
        for index in range(len(tasks["evaluation_tasks"]))
    ]
    if manifest["acceptance_evaluator_ref_and_version"]["refs"] != expected_evaluators:
        raise RunError("evaluator references differ from task pool")
    if manifest["claim_ceiling_ref"] != "experiments/003/prereg.json#/claim_ceiling":
        raise RunError("claim ceiling reference is invalid")


def _baseline_fails(task: dict[str, Any]) -> bool:
    with tempfile.TemporaryDirectory(prefix="e003-preflight-") as temporary:
        unit = Path(temporary)
        _materialize_files(unit, task["visible_files"])
        _materialize_files(unit, task["external_checks"])
        result = subprocess.run(
            [sys.executable, RESERVED_CHECK],
            cwd=unit,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        return result.returncode != 0


def _opencode_command() -> list[str]:
    command_shim = shutil.which("opencode")
    powershell = shutil.which("powershell.exe")
    if command_shim is None or powershell is None:
        raise RunError("opencode or powershell.exe is not installed")
    script = Path(command_shim).with_suffix(".ps1")
    if not script.is_file():
        raise RunError(f"expected OpenCode PowerShell launcher: {script}")
    return [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]


def _opencode_env(manifest: dict[str, Any]) -> dict[str, str]:
    environment = os.environ.copy()
    environment["OPENCODE_CONFIG_CONTENT"] = _canonical(
        manifest["execution_surface"]["config"]
    )
    environment["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
    return environment


def _check_registered_state(manifest: dict[str, Any]) -> None:
    reference = manifest["registration_git_ref"]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    registered = subprocess.run(
        ["git", "rev-parse", reference],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if head != registered:
        raise RunError("HEAD is not the registered experiment commit")

    registered_paths = [
        MANIFEST_PATH.relative_to(REPO).as_posix(),
        TASKS_PATH.relative_to(REPO).as_posix(),
        Path(__file__).resolve().relative_to(REPO).as_posix(),
        Path(manifest["protocol_ref"]).as_posix(),
        Path(manifest["lab_ref"]).as_posix(),
    ]
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", *registered_paths],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    changed = subprocess.run(
        ["git", "diff", "--quiet", reference, "--", *registered_paths],
        cwd=REPO,
        check=False,
    )
    if tracked.returncode != 0 or changed.returncode != 0:
        raise RunError("registered experiment files are untracked or changed")
    lab = _read_object(REPO / manifest["lab_ref"])
    if lab.get("lab_id") != "3060-computer" or lab.get("machine_is_host") is not True:
        raise RunError("3060-computer is not the active reference lab")

    parent = manifest["frozen_parent_ref"]
    if (
        parent.get("kind") != "prospective_externalized_history_harness.v1"
        or parent.get("registration_git_ref") != reference
    ):
        raise RunError("frozen harness parent differs from registration")


def _check_opencode_config(manifest: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="e003-config-") as temporary:
        resolved = subprocess.run(
            [*_opencode_command(), "debug", "config", "--pure"],
            cwd=temporary,
            env=_opencode_env(manifest),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    if resolved.returncode != 0:
        raise RunError("cannot resolve registered OpenCode config")
    try:
        actual = json.loads(resolved.stdout)
    except json.JSONDecodeError as exc:
        raise RunError("OpenCode resolved config is not JSON") from exc
    expected = manifest["execution_surface"]["config"]["agent"]["e003-pilot"]
    observed = actual.get("agent", {}).get("e003-pilot", {})
    if observed.get("permission") != expected["permission"]:
        raise RunError("resolved OpenCode deny policy differs from manifest")


def preflight(require_registered: bool = False) -> None:
    manifest = _read_object(MANIFEST_PATH)
    tasks = _read_object(TASKS_PATH)
    _validate_registration(manifest, tasks)
    registered = manifest["registration_status"] == "run_registered"
    if require_registered and (
        manifest["registration_status"] != "run_registered"
        or not manifest["registered_at_utc"]
    ):
        raise RunError("run is not registered")
    if registered:
        if not manifest["registered_at_utc"]:
            raise RunError("registered_at_utc is absent")
        _check_registered_state(manifest)
    if platform.python_version() != manifest["tool_manifest"]["python"]:
        raise RunError("Python version differs from manifest")
    _check_opencode_config(manifest)
    version = subprocess.run(
        [*_opencode_command(), "--version"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if version.returncode != 0 or version.stdout.strip() != manifest["execution_surface"]["version"]:
        raise RunError("opencode version differs from manifest")
    failures = [
        task["task_id"]
        for task in tasks["source_episodes"] + tasks["evaluation_tasks"]
        if not _baseline_fails(task)
    ]
    if failures:
        raise RunError(f"registered baselines unexpectedly pass: {failures}")


def _append_ledger(phase: str) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "at_utc": datetime.now(UTC).isoformat(),
                    "kind": "operator_invocation",
                    "phase": phase,
                },
                separators=(",", ":"),
            )
            + "\n"
        )


def _lab_id(manifest: dict[str, Any]) -> str:
    return str(_read_object(REPO / manifest["lab_ref"])["lab_id"])


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _session_ids(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {"sessionid", "session_id"} and isinstance(child, str):
                yield child
            yield from _session_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from _session_ids(child)


def _audit_events(raw: str) -> tuple[list[str], bool]:
    session_ids: set[str] = set()
    lines = [line for line in raw.splitlines() if line.strip()]
    valid = bool(lines)
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            valid = False
            continue
        if not isinstance(event, dict):
            valid = False
            continue
        session_ids.update(_session_ids(event))
    return sorted(session_ids), valid


def _run_model(unit: Path, prompt: str, manifest: dict[str, Any]) -> dict[str, Any]:
    command = [
        *_opencode_command(),
        *manifest["execution_surface"]["arguments"],
        "--model",
        manifest["model_id_and_version"]["route"],
        "--dir",
        str(unit),
        prompt,
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=unit,
            env=_opencode_env(manifest),
            text=True,
            capture_output=True,
            timeout=manifest["token_time_and_tool_budgets"]["wall_time_seconds_per_call"],
            check=False,
        )
        provider_exit = completed.returncode
        raw = completed.stdout
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        provider_exit = None
        raw = exc.stdout if isinstance(exc.stdout, str) else ""
        timed_out = True
    session_ids, event_stream_valid = _audit_events(raw)
    return {
        "provider_exit": provider_exit,
        "timed_out": timed_out,
        "duration_seconds": round(time.monotonic() - started, 3),
        "session_ids": session_ids,
        "event_stream_valid": event_stream_valid,
    }


def _evaluate(
    unit: Path,
    task: dict[str, Any],
    expected_history_text: str | None = None,
) -> int:
    if (unit / RESERVED_CHECK).exists():
        raise RunError(f"reserved evaluator path was created by model: {unit}")
    allowed = set(task["visible_files"])
    history_relative = ".agentic-evo/prior-experience.json"
    history_path = unit / history_relative
    if expected_history_text is not None:
        allowed.add(history_relative)
        if history_path.read_text(encoding="utf-8") != expected_history_text:
            raise RunError(f"assigned history was modified by model: {unit}")
    observed = {
        path.relative_to(unit).as_posix()
        for path in unit.rglob("*")
        if path.is_file()
    }
    if observed != allowed:
        raise RunError(f"model created unexpected files: {sorted(observed - allowed)}")
    _materialize_files(unit, task["external_checks"])
    result = subprocess.run(
        [sys.executable, RESERVED_CHECK],
        cwd=unit,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    return result.returncode


def _capture_visible(unit: Path, task: dict[str, Any]) -> dict[str, list[str]]:
    captured: dict[str, list[str]] = {}
    for relative in task["visible_files"]:
        path = unit / relative
        captured[relative] = path.read_text(encoding="utf-8").splitlines()
    return captured


def run_source() -> None:
    preflight(require_registered=True)
    manifest = _read_object(MANIFEST_PATH)
    tasks = _read_object(TASKS_PATH)
    if HISTORIES_PATH.exists() or (RESULTS / "source").exists():
        raise RunError("source result already exists")
    _append_ledger("source")
    index = _task_index(tasks)
    histories = []
    for task_id in manifest["run_order"]["source_task_ids"]:
        task = index[task_id]
        result_dir = RESULTS / "source" / task_id
        result_dir.mkdir(parents=True, exist_ok=False)
        with tempfile.TemporaryDirectory(prefix=f"e003-{task_id}-") as temporary:
            unit = Path(temporary)
            _materialize_files(unit, task["visible_files"])
            before = task["visible_files"]
            prompt = manifest["task_envelope_text"] + task["instruction"]
            model = _run_model(unit, prompt, manifest)
            try:
                external_exit = _evaluate(unit, task)
                after = _capture_visible(unit, task)
            except (OSError, UnicodeError, RunError) as exc:
                external_exit = None
                after = {}
                model["capture_error"] = str(exc)
        history = {
            "history_id": f"history-{task['pair_id']}",
            "pair_id": task["pair_id"],
            "source_task_id": task_id,
            "instruction": task["instruction"],
            "before": before,
            "after": after,
            "provider_exit": model["provider_exit"],
            "external_check_exit": external_exit,
            "timed_out": model["timed_out"],
            "event_stream_valid": model["event_stream_valid"],
        }
        histories.append(history)
        _write_json(
            result_dir / "result.json",
            {
                "task_id": task_id,
                "lab_id": _lab_id(manifest),
                **model,
                "external_check_exit": external_exit,
            },
        )
        if (
            model["provider_exit"] != 0
            or model["timed_out"]
            or not model["event_stream_valid"]
            or external_exit is None
        ):
            raise RunError(f"source episode invalid: {task_id}")
    _write_json(
        HISTORIES_PATH,
        {
            "schema": "agentic-evo.e003-prospective-histories.v1",
            "lab_id": _lab_id(manifest),
            "source_completed_at_utc": datetime.now(UTC).isoformat(),
            "histories": histories,
        },
    )


def _load_histories(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = _read_object(HISTORIES_PATH)
    histories = {item["pair_id"]: item for item in value["histories"]}
    pairs = set(manifest["shuffled_memory_derangement_rule_and_frozen_mapping"]["mapping"])
    if set(histories) != pairs:
        raise RunError("source histories do not match frozen pairs")
    return histories


def _projection(
    unit: Path,
    task: dict[str, Any],
    arm: str,
    histories: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
) -> tuple[str, str | None]:
    base = manifest["task_envelope_text"] + task["instruction"]
    if arm == "no_memory":
        return base, None
    matching = histories[task["pair_id"]]
    if arm == "equivalent_instruction":
        prefix = manifest["equivalent_instruction_text_and_assignment"]["prefix"]
        return prefix.replace("{canonical_matching_history}", _canonical(matching)) + base, None
    if arm == "retained_history":
        history = matching
    elif arm == "shuffled_memory":
        mapping = manifest["shuffled_memory_derangement_rule_and_frozen_mapping"]["mapping"]
        history = histories[mapping[task["pair_id"]]]
    else:
        raise RunError(f"unknown arm: {arm}")
    history_path = unit / ".agentic-evo" / "prior-experience.json"
    _write_json(history_path, history)
    return manifest["file_history_instruction_text"] + base, history_path.read_text(encoding="utf-8")


def _phase_window_open(manifest: dict[str, Any], phase: str) -> bool:
    source = _read_object(HISTORIES_PATH)
    anchor = datetime.fromisoformat(source["source_completed_at_utc"])
    schedule = manifest[
        "delayed_retest_schedule" if phase == "delayed" else "immediate_test_schedule"
    ]
    now = datetime.now(UTC)
    return anchor + timedelta(hours=schedule["earliest_offset_hours"]) <= now <= anchor + timedelta(hours=schedule["latest_offset_hours"])


def run_phase(phase: str) -> None:
    preflight(require_registered=True)
    manifest = _read_object(MANIFEST_PATH)
    tasks = _read_object(TASKS_PATH)
    histories = _load_histories(manifest)
    phase_root = RESULTS / phase
    if phase_root.exists():
        raise RunError(f"phase result already exists: {phase}")
    if not _phase_window_open(manifest, phase):
        raise RunError(f"{phase} phase is outside its registered window")
    if phase == "immediate" and (RESULTS / "delayed").exists():
        raise RunError("delayed phase already exists")
    if phase == "delayed":
        missing_immediate = [
            unit_id
            for unit_id, _, _, _ in _expected_units(manifest, tasks, "immediate")
            if not (RESULTS / "immediate" / unit_id / "result.json").is_file()
        ]
        if missing_immediate:
            raise RunError("immediate phase is incomplete")
    _append_ledger(phase)
    for unit_id, task, arm, replica in _expected_units(manifest, tasks, phase):
        result_dir = phase_root / unit_id
        result_dir.mkdir(parents=True, exist_ok=False)
        with tempfile.TemporaryDirectory(prefix=f"e003-{unit_id}-") as temporary:
            unit = Path(temporary)
            _materialize_files(unit, task["visible_files"])
            prompt, expected_history_text = _projection(unit, task, arm, histories, manifest)
            model = _run_model(unit, prompt, manifest)
            try:
                external_exit = _evaluate(unit, task, expected_history_text)
                after = _capture_visible(unit, task)
            except (OSError, UnicodeError, RunError) as exc:
                external_exit = None
                after = {}
                model["capture_error"] = str(exc)
        result = {
            "schema": "agentic-evo.e003-unit-result.v1",
            "unit_id": unit_id,
            "phase": phase,
            "task_id": task["task_id"],
            "pair_id": task["pair_id"],
            "arm": arm,
            "replica": replica,
            "lab_id": _lab_id(manifest),
            **model,
            "external_check_exit": external_exit,
            "after_files": after,
        }
        _write_json(result_dir / "result.json", result)
        if (
            model["provider_exit"] != 0
            or model["timed_out"]
            or not model["event_stream_valid"]
            or external_exit is None
        ):
            raise RunError(f"unit invalid: {unit_id}")


def _recheck_after_files(task: dict[str, Any], after_files: Any) -> int:
    if not isinstance(after_files, dict) or set(after_files) != set(task["visible_files"]):
        raise RunError("recorded after_files do not match the task")
    if any(
        not isinstance(lines, list) or not all(isinstance(line, str) for line in lines)
        for lines in after_files.values()
    ):
        raise RunError("recorded after_files have an invalid shape")
    with tempfile.TemporaryDirectory(prefix="e003-finalize-") as temporary:
        unit = Path(temporary)
        _materialize_files(unit, after_files)
        _materialize_files(unit, task["external_checks"])
        checked = subprocess.run(
            [sys.executable, RESERVED_CHECK],
            cwd=unit,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        return checked.returncode


def finalize() -> None:
    preflight(require_registered=True)
    manifest = _read_object(MANIFEST_PATH)
    tasks = _read_object(TASKS_PATH)
    final_path = RESULTS / "final.json"
    if final_path.exists():
        raise RunError("final result already exists")
    _append_ledger("finalize")
    results: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    invalid = False
    for phase in manifest["run_order"]["phase_order"]:
        for unit_id, task, arm, replica in _expected_units(manifest, tasks, phase):
            path = RESULTS / phase / unit_id / "result.json"
            if not path.exists():
                invalid = True
                continue
            result = _read_object(path)
            identity = (
                result.get("unit_id") == unit_id
                and result.get("phase") == phase
                and result.get("task_id") == task["task_id"]
                and result.get("pair_id") == task["pair_id"]
                and result.get("arm") == arm
                and result.get("replica") == replica
            )
            try:
                final_exit = _recheck_after_files(task, result.get("after_files"))
            except (OSError, RunError, subprocess.SubprocessError):
                final_exit = None
            result["finalize_external_exit"] = final_exit
            results[(phase, task["pair_id"], arm, replica)] = result
            invalid |= (
                not identity
                or result["provider_exit"] != 0
                or result["timed_out"]
                or not result["event_stream_valid"]
                or result["external_check_exit"] is None
                or final_exit is None
                or final_exit != result["external_check_exit"]
            )
    if invalid:
        outcome = "inconclusive"
    else:
        replica_disagreement = False
        threshold_met = True
        pairs = {task["pair_id"] for task in tasks["source_episodes"]}
        for phase in manifest["run_order"]["phase_order"]:
            for pair in pairs:
                for arm in manifest["arm_assignment"]["arms"]:
                    values = [
                        results[(phase, pair, arm, replica)]["finalize_external_exit"] == 0
                        for replica in manifest["arm_assignment"]["replicas"]
                    ]
                    replica_disagreement |= values[0] != values[1]
                replicas = manifest["arm_assignment"]["replicas"]
                threshold_met &= all(results[(phase, pair, "retained_history", r)]["finalize_external_exit"] == 0 for r in replicas)
                threshold_met &= all(results[(phase, pair, "no_memory", r)]["finalize_external_exit"] != 0 for r in replicas)
                threshold_met &= all(results[(phase, pair, "shuffled_memory", r)]["finalize_external_exit"] != 0 for r in replicas)
        outcome = "passed" if threshold_met and not replica_disagreement else "failed"
    _write_json(
        final_path,
        {
            "schema": "agentic-evo.e003-run-result.v1",
            "run_id": manifest["run_id"],
            "lab_id": _lab_id(manifest),
            "execution_status": outcome,
            "claim_ceiling_ref": manifest["claim_ceiling_ref"],
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="E003 pilot-002 registered runner")
    parser.add_argument("command", choices=("preflight", "source", "immediate", "delayed", "finalize"))
    args = parser.parse_args()
    try:
        if args.command == "preflight":
            preflight()
        elif args.command == "source":
            run_source()
        elif args.command in {"immediate", "delayed"}:
            run_phase(args.command)
        else:
            finalize()
    except (OSError, RunError, subprocess.SubprocessError) as exc:
        print(f"E003 {args.command} blocked: {exc}", file=sys.stderr)
        return 1
    print(f"E003 {args.command}: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
