from __future__ import annotations

import unittest

from agentic_evo.organ_broker import (
    _MODEL_ONLY_ORGAN_ARGS,
    _organ_command,
    _read_only_organ_args,
)


class OrganBrokerIsolationTests(unittest.TestCase):
    def test_host_config_is_reduced_to_model_choice_plus_fixed_tool_isolation(self) -> None:
        executable, arguments = _read_only_organ_args(
            (
                "C:/Codex/codex.exe",
                "--skip-git-repo-check",
                "--model",
                "gpt-5.6-sol",
                "-c",
                'model_reasoning_effort="xhigh"',
                "--sandbox",
                "read-only",
            )
        )

        self.assertEqual(executable, "C:/Codex/codex.exe")
        self.assertEqual(arguments[: len(_MODEL_ONLY_ORGAN_ARGS)], _MODEL_ONLY_ORGAN_ARGS)
        self.assertEqual(
            arguments[len(_MODEL_ONLY_ORGAN_ARGS) :],
            (
                "--skip-git-repo-check",
                "--model",
                "gpt-5.6-sol",
                "-c",
                'model_reasoning_effort="xhigh"',
                "--sandbox",
                "read-only",
            ),
        )

    def test_resume_keeps_fixed_isolation_on_the_exact_native_thread(self) -> None:
        executable, arguments = _read_only_organ_args(
            (
                "C:/Codex/codex.exe",
                "--model=gpt-5.6-sol",
                '--config=model_reasoning_effort="xhigh"',
            )
        )

        command = _organ_command(
            executable=executable,
            args=arguments,
            resume_thread_id="thread-123",
        )

        self.assertEqual(command[:4], ["C:/Codex/codex.exe", "exec", "--sandbox", "read-only"])
        self.assertEqual(command[4:6], ["resume", "--json"])
        self.assertEqual(command[-2:], ["thread-123", "-"])
        for argument in _MODEL_ONLY_ORGAN_ARGS:
            self.assertIn(argument, command)
        self.assertNotIn("--last", command)


if __name__ == "__main__":
    unittest.main()
