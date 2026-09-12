from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import uuid4

from agentic_evo.observer import observe, report_json, report_markdown


ROOT = Path(__file__).resolve().parents[1]


class ObserverTests(unittest.TestCase):
    def _path(self, suffix: str) -> Path:
        path = ROOT / "tests" / f".observer-{uuid4().hex}{suffix}"
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_both_agents_map_structured_test_failure_without_leaking_secrets(self) -> None:
        secret = "token-super-private"
        fixtures = {
            "codex": {
                "provider": "codex",
                "hook_event_name": "PostToolUse",
                "session_id": "s1",
                "tool_name": "pytest",
                "tool_input": {"authorization": secret},
                "tool_response": {"status": "failed", "exit_code": 1, "body": secret},
            },
            "lingtai": {
                "provider": "lingtai",
                "event_kind": "test_result",
                "session_id": "s1",
                "status": "failed",
                "exit_code": 1,
                "body": secret,
            },
        }
        categories = []
        for provider, fixture in fixtures.items():
            path = self._path(f"-{provider}.json")
            path.write_text(json.dumps(fixture), encoding="utf-8")
            report = observe(path)
            categories.append(report["observations"][0]["category"])
            rendered = report_json(report) + report_markdown(report)
            self.assertNotIn(secret, rendered)
            self.assertEqual(report["observations"][0]["confidence"], "high")
        self.assertEqual(categories, ["test_failure", "test_failure"])

    def test_unknown_post_tool_and_lingtai_lifecycle_are_not_failures(self) -> None:
        records = [
            {"provider": "codex", "hook_event_name": "PostToolUse", "session_id": "c"},
            {"provider": "lingtai", "event_kind": "heartbeat", "session_id": "l"},
        ]
        path = self._path("-events.json")
        path.write_text(json.dumps(records), encoding="utf-8")
        report = observe(path)
        self.assertEqual(report["observations"], [])
        self.assertEqual(len(report["coverage_gaps"]), 2)

    def test_detector_uses_only_structured_status_or_nonzero_exit(self) -> None:
        records = [
            {
                "provider": "codex",
                "event_kind": "command_result",
                "status": "succeeded",
                "output": "FAILED text is not structured evidence",
            },
            {
                "provider": "lingtai",
                "event_kind": "command_result",
                "exit_code": 7,
            },
        ]
        path = self._path("-detector.json")
        path.write_text(json.dumps(records), encoding="utf-8")

        report = observe(path)

        self.assertEqual(len(report["observations"]), 1)
        observation = report["observations"][0]
        self.assertEqual(observation["category"], "command_failure")
        self.assertEqual(observation["evidence_reason"], "explicit non-zero exit code 7")

    def test_malformed_jsonl_warns_and_repeat_output_is_byte_stable(self) -> None:
        path = self._path("-receipts.jsonl")
        path.write_text(
            '{"provider":"lingtai","event_kind":"command_result","status":"error"}\n'
            "{malformed secret-body}\n",
            encoding="utf-8",
        )
        first = report_json(observe(path))
        second = report_json(observe(path))
        self.assertEqual(first, second)
        report = json.loads(first)
        self.assertEqual(report["skipped_count"], 1)
        self.assertEqual(report["warnings"], ["line 2: malformed JSON skipped"])
        self.assertNotIn("secret-body", first)

    def test_oversize_jsonl_record_is_skipped_without_retaining_its_body(self) -> None:
        secret = "oversize-record-secret"
        path = self._path("-oversize.jsonl")
        oversize_line = json.dumps(
            {
                "provider": "codex",
                "event_kind": "command_result",
                "status": "failed",
                "body": secret + ("x" * (256 * 1024)),
            }
        )
        self.assertGreater(len(oversize_line.encode("utf-8")), 256 * 1024)
        path.write_bytes(
            (
                '{"provider":"lingtai","event_kind":"command_result","status":"error"}\n'
                + oversize_line
                + "\n"
            ).encode("utf-8")
        )
        before = path.read_bytes()

        first = observe(path)
        second = observe(path)

        self.assertEqual(report_json(first), report_json(second))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(first["skipped_count"], 1)
        self.assertEqual(first["coverage_gaps"], ["oversize JSONL record skipped"])
        rendered = report_json(first) + report_markdown(first)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret, "\n".join(first["warnings"]))

    def test_unsupported_adapter_projection_is_skipped_with_coverage_gap(self) -> None:
        secret = "unsupported-shape-secret"
        path = self._path("-unsupported.json")
        path.write_text(
            json.dumps(
                {
                    "provider": "codex",
                    "event_kind": "unsupported_shape",
                    "body": secret,
                }
            ),
            encoding="utf-8",
        )
        before = path.read_bytes()

        first = observe(path)
        second = observe(path)

        self.assertEqual(report_json(first), report_json(second))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(first["skipped_count"], 1)
        self.assertEqual(
            first["coverage_gaps"],
            ["event shape not supported by bounded adapter projection"],
        )
        rendered = report_json(first) + report_markdown(first)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret, "\n".join(first["warnings"]))

    def test_unknown_provider_is_skipped_with_coverage_gap_without_leaking_body(self) -> None:
        secret = "unknown-provider-secret"
        path = self._path("-unknown-provider.json")
        path.write_text(
            json.dumps(
                {
                    "provider": "unsupported-provider",
                    "body": secret,
                }
            ),
            encoding="utf-8",
        )
        before = path.read_bytes()

        first = observe(path)
        second = observe(path)

        self.assertEqual(report_json(first), report_json(second))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(first["skipped_count"], 1)
        self.assertEqual(
            first["coverage_gaps"],
            ["event shape not supported by bounded adapter projection"],
        )
        rendered = report_json(first) + report_markdown(first)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret, "\n".join(first["warnings"]))

    def _run_cli(self, source: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "agentic_evo.cli",
                "observe",
                str(source),
                *arguments,
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def _failure_source(self) -> tuple[Path, bytes]:
        source = self._path("-alias-source.json")
        source.write_text(
            json.dumps(
                {
                    "provider": "codex",
                    "event_kind": "command_result",
                    "status": "failed",
                    "exit_code": 2,
                }
            ),
            encoding="utf-8",
        )
        return source, source.read_bytes()

    def _assert_alias_error(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertEqual(result.stdout, "")
        error = json.loads(result.stderr)
        self.assertFalse(error["ok"])
        self.assertEqual(error["error"]["code"], "observer_input_error")
        self.assertEqual(
            error["error"]["message"],
            "Observer source or output could not be processed",
        )

    def test_cli_rejects_output_aliasing_source_without_mutation(self) -> None:
        source, before = self._failure_source()
        markdown_output = self._path("-must-not-exist.md")

        result = self._run_cli(
            source,
            "--json-output",
            os.fspath(source.parent / "unused" / ".." / source.name),
            "--markdown-output",
            os.fspath(markdown_output),
        )

        self._assert_alias_error(result)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(markdown_output.exists())
        self.assertNotIn(os.fspath(source), result.stderr)

    def test_cli_rejects_json_and_markdown_output_alias(self) -> None:
        source, before = self._failure_source()
        shared_output = self._path("-shared-report")

        result = self._run_cli(
            source,
            "--json-output",
            os.fspath(shared_output),
            "--markdown-output",
            os.fspath(shared_output.parent / "." / shared_output.name),
        )

        self._assert_alias_error(result)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(shared_output.exists())

    def test_cli_rejects_existing_hardlink_alias(self) -> None:
        source, before = self._failure_source()
        hardlink_output = self._path("-hardlink-report.json")
        os.link(source, hardlink_output)

        result = self._run_cli(
            source,
            "--json-output",
            os.fspath(hardlink_output),
        )

        self._assert_alias_error(result)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(hardlink_output.read_bytes(), before)
        self.assertTrue(os.path.samefile(source, hardlink_output))

    def test_cli_writes_consistent_outputs_without_mutating_source(self) -> None:
        source = self._path("-codex.json")
        json_output = self._path("-report.json")
        markdown_output = self._path("-report.md")
        source.write_text(
            json.dumps(
                {
                    "provider": "codex",
                    "event_kind": "command_result",
                    "status": "failed",
                    "exit_code": 2,
                }
            ),
            encoding="utf-8",
        )
        before = source.read_bytes()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentic_evo.cli",
                "observe",
                str(source),
                "--json-output",
                str(json_output),
                "--markdown-output",
                str(markdown_output),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, json_output.read_text(encoding="utf-8"))
        report = json.loads(result.stdout)
        problem_id = report["observations"][0]["problem_id"]
        self.assertIn(problem_id, markdown_output.read_text(encoding="utf-8"))
        self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
