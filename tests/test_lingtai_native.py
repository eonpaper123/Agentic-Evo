from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch

from agentic_evo.ipc import ServiceRejectedError


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NATIVE_ENTRY = (
    REPOSITORY_ROOT / "src" / "agentic_evo" / "native" / "agentic-evo-lingtai.py"
)


def _load_native_module():
    spec = importlib.util.spec_from_file_location("agentic_evo_lingtai_native", NATIVE_ENTRY)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load LingTai native entry")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Surface:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.failure_observation_error: BaseException | None = None
        self.sleep_error: BaseException | None = None

    def wake(self, **kwargs):
        self.calls.append(("wake", kwargs))
        return {
            "root": "root-1",
            "head": "head-1",
            "generation": 2,
            "body_files": ["entrypoint.md"],
            "activation_kind": "markdown",
            "activation_artifact": "entrypoint.md",
            "activation_digest": "digest-1",
            "activation_context": "Current Body content.",
        }

    def recall_experiences(self, **kwargs):
        self.calls.append(("recall", kwargs))
        return {
            "experiences": [
                {
                    "sequence": 7,
                    "event_kind": "tool_use_finished",
                    "execution_surface": "codex",
                    "session_id": "prior-session",
                    "occurred_at": "2026-09-12T00:00:00Z",
                    "loaded_body_head": "head-0",
                }
            ],
            "has_more": False,
            "next_before_sequence": None,
        }

    def observe(self, **kwargs):
        self.calls.append(("observe", kwargs))
        if (
            kwargs.get("event_kind") == "turn_stopped"
            and self.failure_observation_error is not None
        ):
            raise self.failure_observation_error
        return {}

    def sleep(self, **kwargs):
        self.calls.append(("sleep", kwargs))
        if self.sleep_error is not None:
            raise self.sleep_error
        return {}

    def submit_successor(self, **kwargs):
        self.calls.append(("submit", kwargs))
        return {}


class _ParentAgent:
    def __init__(self, *args, **kwargs) -> None:
        self._sealed = False
        self._asleep = threading.Event()
        self._state = SimpleNamespace(name="IDLE")
        self.started = False
        self.stopped = False
        self.sent: list[str] = []

    @property
    def state(self):
        return self._state

    def _pre_request(self, msg):
        return msg.content

    def _post_request(self, msg, result) -> None:
        self.parent_posted = True

    def start(self) -> None:
        self.started = True

    def send(self, content: str, sender: str = "user") -> None:
        self.sent.append(content)
        message = SimpleNamespace(id="native-task-1", content=content)
        self.wrapped_prompt = self._pre_request(message)
        self._post_request(
            message,
            {"text": "Actual parent result.", "failed": False, "errors": []},
        )

    def stop(self) -> None:
        self.stopped = True


class _AsleepParentAgent(_ParentAgent):
    """Mimic LingTai's terminal AED path: ASLEEP without _post_request."""

    def send(self, content: str, sender: str = "user") -> None:
        self.sent.append(content)
        message = SimpleNamespace(id="native-task-1", content=content)
        self.wrapped_prompt = self._pre_request(message)
        self._state = SimpleNamespace(name="ASLEEP")
        self._asleep.set()


class _NativeJournal:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append(self, event: dict):
        self.events.append(dict(event))
        return "native-position"

    def close(self) -> None:
        return None


class _ToolAgent:
    def __init__(self, surface: _Surface) -> None:
        self._evo_surface = surface
        self._evo_session_id = "run-1"
        self._evo_wake = {"head": "head-1"}
        self.tools: dict[str, dict] = {}

    def add_tool(self, name: str, **kwargs) -> None:
        self.tools[name] = kwargs


class LingTaiNativeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_native_module()

    def test_native_lifecycle_preserves_original_task_and_sleeps(self) -> None:
        surface = _Surface()
        agent_class = self.module._make_agent_class(_ParentAgent)
        agents: list[_ParentAgent] = []
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            home = root / "evo-home"
            home.mkdir()
            project = root / "target-project"
            project.mkdir()
            preset = root / "preset.json"
            preset.write_text("{}", encoding="utf-8")
            request = {
                "home": str(home),
                "prompt": "Preserve this exact task.",
                "working_dir": str(project),
                "preset": str(preset),
            }

            def build_agent(*args, **kwargs):
                kwargs["evo_surface"] = surface
                agent = agent_class(*args, **kwargs)
                agents.append(agent)
                return agent

            with patch.object(self.module, "SurfaceClient", return_value=surface), patch.object(
                self.module, "_prepare_station", return_value={"manifest": {"llm": {}}}
            ), patch.object(self.module, "_build_agent", side_effect=build_agent):
                result = self.module.run_native_task(request)

        agent = agents[0]

        self.assertTrue(agent.started)
        self.assertTrue(agent.stopped)
        self.assertEqual(agent.sent, ["Preserve this exact task."])
        self.assertIn("=== ORIGINAL USER TASK (UNCHANGED) ===", agent.wrapped_prompt)
        self.assertIn("Preserve this exact task.", agent.wrapped_prompt)
        self.assertIn(str(project.resolve()), agent.wrapped_prompt)
        self.assertEqual(result["native_task_id"], "native-task-1")
        self.assertTrue(result["success"])
        self.assertEqual(result["readable_final"], "Actual parent result.")
        self.assertFalse(
            [
                payload
                for name, payload in surface.calls
                if name == "observe" and payload["event_kind"] == "turn_stopped"
            ]
        )
        self.assertEqual(surface.calls[-1][0], "sleep")

    def test_asleep_without_parent_result_fails_and_cleans_up(self) -> None:
        surface = _Surface()
        agent_class = self.module._make_agent_class(_AsleepParentAgent)
        agents: list[_AsleepParentAgent] = []
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            home = root / "evo-home"
            home.mkdir()
            project = root / "target-project"
            project.mkdir()
            preset = root / "preset.json"
            preset.write_text("{}", encoding="utf-8")
            request = {
                "home": str(home),
                "prompt": "Preserve this exact task.",
                "working_dir": str(project),
                "preset": str(preset),
            }

            def build_agent(*args, **kwargs):
                kwargs["evo_surface"] = surface
                agent = agent_class(*args, **kwargs)
                agents.append(agent)
                return agent

            errors: list[BaseException] = []
            completed = threading.Event()

            def run_task() -> None:
                try:
                    self.module.run_native_task(request)
                except BaseException as error:
                    errors.append(error)
                finally:
                    completed.set()

            with patch.object(self.module, "SurfaceClient", return_value=surface), patch.object(
                self.module, "_prepare_station", return_value={"manifest": {"llm": {}}}
            ), patch.object(self.module, "_build_agent", side_effect=build_agent):
                runner = threading.Thread(target=run_task, daemon=True)
                runner.start()
                self.assertTrue(
                    completed.wait(1),
                    "ASLEEP without a parent result must not leave the native run blocked",
                )

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], self.module.NativeLingTaiError)
        self.assertIn("ASLEEP", str(errors[0]))
        self.assertTrue(agents[0].stopped)
        failure_observations = [
            payload
            for name, payload in surface.calls
            if name == "observe" and payload["event_kind"] == "turn_stopped"
        ]
        self.assertEqual(len(failure_observations), 1)
        failure = failure_observations[0]
        self.assertEqual(failure["execution_surface"], "lingtai")
        self.assertEqual(failure["turn_id"], "native-task-1")
        self.assertEqual(
            failure["payload"],
            {
                "ingress": "lingtai_native",
                "failure_code": "asleep_without_parent_result",
                "native_run_id": failure["session_id"],
                "station_id": failure["session_id"],
                "target_project": failure["project_environment"],
            },
        )
        failure_index = next(
            index
            for index, (name, payload) in enumerate(surface.calls)
            if name == "observe" and payload["event_kind"] == "turn_stopped"
        )
        sleep_index = next(
            index for index, (name, _) in enumerate(surface.calls) if name == "sleep"
        )
        self.assertLess(failure_index, sleep_index)
        self.assertEqual(surface.calls[-1][0], "sleep")

    def test_runtime_off_rejection_keeps_asleep_failure_and_cleanup(self) -> None:
        surface = _Surface()
        surface.failure_observation_error = ServiceRejectedError(
            "runtime_off", "runtime is off"
        )
        surface.sleep_error = ServiceRejectedError("runtime_off", "runtime is off")
        agent_class = self.module._make_agent_class(_AsleepParentAgent)
        agents: list[_AsleepParentAgent] = []
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            home = root / "evo-home"
            home.mkdir()
            project = root / "target-project"
            project.mkdir()
            preset = root / "preset.json"
            preset.write_text("{}", encoding="utf-8")
            request = {
                "home": str(home),
                "prompt": "Preserve this exact task.",
                "working_dir": str(project),
                "preset": str(preset),
            }

            def build_agent(*args, **kwargs):
                kwargs["evo_surface"] = surface
                agent = agent_class(*args, **kwargs)
                agents.append(agent)
                return agent

            with patch.object(self.module, "SurfaceClient", return_value=surface), patch.object(
                self.module, "_prepare_station", return_value={"manifest": {"llm": {}}}
            ), patch.object(self.module, "_build_agent", side_effect=build_agent):
                with self.assertRaisesRegex(self.module.NativeLingTaiError, "ASLEEP"):
                    self.module.run_native_task(request)

        self.assertTrue(agents[0].stopped)
        self.assertEqual(surface.calls[-1][0], "sleep")
        self.assertEqual(
            [payload["event_kind"] for name, payload in surface.calls if name == "observe"],
            ["user_prompt_submitted", "turn_stopped"],
        )

    def test_registers_model_callable_recall_and_successor_tools(self) -> None:
        surface = _Surface()
        agent = _ToolAgent(surface)

        self.module._register_evo_tools(agent)

        recalled = agent.tools["agentic_evo_recall_experiences"]["handler"](
            {"limit": 5, "before_sequence": 9}
        )
        submitted = agent.tools["agentic_evo_submit_successor"]["handler"](
            {
                "expected_head": "head-1",
                "files": {"next.md": "candidate body"},
                "activation_kind": "markdown",
                "activation_artifact": "next.md",
            }
        )

        self.assertEqual(recalled["experiences"][0]["session_id"], "prior-session")
        self.assertEqual(submitted, {})
        recall_call = next(payload for name, payload in surface.calls if name == "recall")
        self.assertEqual(recall_call["limit"], 5)
        self.assertEqual(recall_call["before_sequence"], 9)
        submit_call = next(payload for name, payload in surface.calls if name == "submit")
        self.assertEqual(submit_call["execution_surface"], "lingtai")
        self.assertEqual(submit_call["session_id"], "run-1")
        self.assertEqual(submit_call["expected_head"], "head-1")

    def test_journal_keeps_native_event_first_and_projects_scrubbed_result(self) -> None:
        surface = _Surface()
        journal = _NativeJournal()
        tee = self.module.EvoJournalTee(
            journal,
            surface=surface,
            session_id="run-1",
            native_run_id="run-1",
            station_id="station-run-1",
            project_environment="D:/target-project",
        )
        event = {
            "type": "tool_result",
            "tool_name": "bash",
            "tool_call_id": "call-1",
            "tool_args": {
                "command": "run focused test",
                "reasoning": "hidden chain of thought",
                "api_key": "SENTINEL",
            },
            "status": "ok",
            "result": {"output": "passed", "Authorization": "Bearer SENTINEL"},
        }

        position = tee.append(event)

        self.assertEqual(position, "native-position")
        self.assertEqual(journal.events, [event])
        observed = [payload for name, payload in surface.calls if name == "observe"]
        self.assertEqual(len(observed), 1)
        projected = observed[0]
        self.assertEqual(projected["project_environment"], "D:/target-project")
        self.assertEqual(projected["payload"]["target_project"], "D:/target-project")
        self.assertEqual(projected["payload"]["station_id"], "station-run-1")
        self.assertNotIn("SENTINEL", repr(projected))
        self.assertNotIn("hidden chain of thought", repr(projected))

    def test_journal_records_codex_child_relation_without_child_transcript(self) -> None:
        surface = _Surface()
        tee = self.module.EvoJournalTee(
            _NativeJournal(),
            surface=surface,
            session_id="run-1",
            native_run_id="run-1",
            station_id="station-run-1",
            project_environment="D:/target-project",
        )

        tee.append(
            {
                "type": "tool_result",
                "tool_name": "daemon",
                "tool_call_id": "parent-call-1",
                "tool_args": {"input": {"backend": "codex", "task": "delegate"}},
                "status": "dispatched",
                "result": {
                    "backend": "codex",
                    "ids": ["child-1"],
                    "agent_message": "child transcript must not duplicate here",
                },
            }
        )

        observed = [payload for name, payload in surface.calls if name == "observe"]
        self.assertEqual([payload["event_kind"] for payload in observed], [
            "tool_use_finished",
            "subagent_started",
        ])
        self.assertNotIn("child transcript", observed[0]["payload"]["tool_response"])
        self.assertEqual(observed[1]["payload"]["child_ids"], ["child-1"])


if __name__ == "__main__":
    unittest.main()
