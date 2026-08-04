from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agentic_evo.test_result_receipts import (
    OBSERVED_LOCAL_NAMESPACE,
    TestResultReceiptError,
    run_test_result_receipt,
    validate_test_result_receipt,
    verify_test_result_receipt,
)


class TestResultReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "project"
        self.root.mkdir()
        self.namespace = OBSERVED_LOCAL_NAMESPACE

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _run(self, run_id: str, code: str, *, timeout: int = 5) -> dict[str, object]:
        return run_test_result_receipt(
            project_root=self.root,
            receipt_namespace=self.namespace,
            run_id=run_id,
            suite_id="p0-p1-unit",
            timeout_seconds=timeout,
            argv=[sys.executable, "-c", code],
        )

    def _directory(self, run_id: str) -> Path:
        return self.root / self.namespace / run_id

    def test_pass_receipt_is_atomic_closed_and_contains_hashes_not_raw_output(self) -> None:
        run_id = "0123456789abcdef0123456789abcdef"
        receipt = self._run(
            run_id,
            "import sys; print('ordinary ' + 'stdout'); print('ordinary ' + 'stderr', file=sys.stderr)",
        )
        self.assertEqual(receipt["execution"]["outcome"], "passed")
        directory = self._directory(run_id)
        self.assertEqual({entry.name for entry in directory.iterdir()}, {"receipt.json"})
        raw = (directory / "receipt.json").read_bytes()
        self.assertNotIn(b"ordinary stdout", raw)
        self.assertNotIn(b"ordinary stderr", raw)
        verified = verify_test_result_receipt(self.root, f"{self.namespace}/{run_id}")
        self.assertEqual(verified, receipt)
        self.assertEqual(verified["provenance"]["class"], "observed_local_test")
        self.assertEqual(verified["provenance"]["claim_ceiling"], ["local test-process outcome only"])
        self.assertEqual(verified["command"]["argv_sha256"], receipt["command"]["argv_sha256"])

    def test_failed_timeout_and_runner_error_each_publish_receipts_without_becoming_passes(self) -> None:
        failed_id = "1123456789abcdef0123456789abcdef"
        failed = self._run(failed_id, "import sys; sys.exit(3)")
        self.assertEqual(failed["execution"]["outcome"], "failed")
        self.assertEqual(failed["execution"]["exit_code"], 3)
        self.assertEqual(verify_test_result_receipt(self.root, f"{self.namespace}/{failed_id}")["execution"]["outcome"], "failed")

        timed_out_id = "2123456789abcdef0123456789abcdef"
        timed_out = self._run(timed_out_id, "import time; time.sleep(2)", timeout=1)
        self.assertEqual(timed_out["execution"]["outcome"], "timed_out")
        self.assertIs(timed_out["execution"]["timed_out"], True)
        self.assertEqual(verify_test_result_receipt(self.root, f"{self.namespace}/{timed_out_id}")["execution"]["outcome"], "timed_out")

        runner_error_id = "3123456789abcdef0123456789abcdef"
        runner_error = run_test_result_receipt(
            project_root=self.root,
            receipt_namespace=self.namespace,
            run_id=runner_error_id,
            suite_id="p0-p1-unit",
            timeout_seconds=1,
            argv=["definitely-not-a-p0-p1-executable"],
        )
        self.assertEqual(runner_error["execution"]["outcome"], "runner_error")
        self.assertEqual(verify_test_result_receipt(self.root, f"{self.namespace}/{runner_error_id}")["execution"]["outcome"], "runner_error")

    def test_invalid_configuration_secret_path_escape_and_collision_fail_before_or_without_publication(self) -> None:
        with self.assertRaises(TestResultReceiptError):
            run_test_result_receipt(
                project_root=self.root,
                receipt_namespace=self.namespace,
                run_id="bad",
                suite_id="p0-p1-unit",
                timeout_seconds=1,
                argv=[sys.executable, "-c", "pass"],
            )
        self.assertFalse((self.root / "artifacts").exists())
        with self.assertRaises(TestResultReceiptError):
            run_test_result_receipt(
                project_root=self.root,
                receipt_namespace="artifacts/labs/3060-computer/windows-gate-b",
                run_id="4123456789abcdef0123456789abcdef",
                suite_id="p0-p1-unit",
                timeout_seconds=1,
                argv=[sys.executable, "-c", "pass"],
            )
        with self.assertRaises(TestResultReceiptError):
            run_test_result_receipt(
                project_root=self.root,
                receipt_namespace=self.namespace,
                run_id="5123456789abcdef0123456789abcdef",
                suite_id="p0-p1-unit",
                timeout_seconds=1,
                argv=[sys.executable, "-c", "print('token=not-allowed')"],
            )
        run_id = "6123456789abcdef0123456789abcdef"
        self._run(run_id, "pass")
        with self.assertRaises(TestResultReceiptError):
            self._run(run_id, "pass")

    def test_cli_runs_and_verifies_a_receipt_with_documented_outcome_json(self) -> None:
        run_id = "7123456789abcdef0123456789abcdef"
        repository_root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(repository_root / "src")
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentic_evo.cli",
                "run-test-receipt",
                "--project-root",
                str(self.root),
                "--receipt-namespace",
                self.namespace,
                "--run-id",
                run_id,
                "--suite-id",
                "cli-unit",
                "--timeout-seconds",
                "5",
                "--",
                sys.executable,
                "-c",
                "pass",
            ],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads(run.stdout)
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["result"]["outcome"], "passed")
        verify = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentic_evo.cli",
                "verify-test-receipt",
                "--project-root",
                str(self.root),
                "--receipt-dir",
                f"{self.namespace}/{run_id}",
            ],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertEqual(json.loads(verify.stdout)["result"]["outcome"], "passed")

    def test_tampering_closed_fields_or_directory_members_is_rejected(self) -> None:
        run_id = "7123456789abcdef0123456789abcdef"
        self._run(run_id, "pass")
        path = self._directory(run_id) / "receipt.json"
        receipt = json.loads(path.read_text(encoding="utf-8"))
        tampered = copy.deepcopy(receipt)
        tampered["execution"]["outcome"] = "passed"
        tampered["execution"]["exit_code"] = 1
        with self.assertRaises(TestResultReceiptError):
            validate_test_result_receipt(tampered)
        receipt["provenance"]["claim_ceiling"] = ["provider execution"]
        path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaises(TestResultReceiptError):
            verify_test_result_receipt(self.root, f"{self.namespace}/{run_id}")

        extra_id = "8123456789abcdef0123456789abcdef"
        self._run(extra_id, "pass")
        (self._directory(extra_id) / "extra.txt").write_text("not allowed", encoding="utf-8")
        with self.assertRaises(TestResultReceiptError):
            verify_test_result_receipt(self.root, f"{self.namespace}/{extra_id}")

    def test_failed_rename_leaves_no_visible_final_receipt_or_staging_directory(self) -> None:
        run_id = "9123456789abcdef0123456789abcdef"
        with mock.patch("agentic_evo.test_result_receipts.os.replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                self._run(run_id, "pass")
        namespace = self.root / self.namespace
        self.assertFalse((namespace / run_id).exists())
        self.assertFalse(any(entry.name.startswith(".staging-") for entry in namespace.iterdir()))

    def test_reparse_component_fails_closed_when_supported(self) -> None:
        target = self.root / "outside"
        target.mkdir()
        artifacts = self.root / "artifacts"
        try:
            os.symlink(target, artifacts, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("host cannot create a directory symlink for the fail-closed test")
        with self.assertRaises(TestResultReceiptError):
            self._run("a123456789abcdef0123456789abcdef", "pass")


if __name__ == "__main__":
    unittest.main()
