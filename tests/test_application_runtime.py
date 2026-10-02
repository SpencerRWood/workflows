"""Application candidate failure, timeout, cleanup and public contract checks."""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import application_runtime


class ApplicationRuntimeTests(unittest.TestCase):
    def test_mutable_image_is_rejected_before_docker(self):
        result = subprocess.run([
            sys.executable, str(ROOT / "scripts/application_runtime.py"),
            "ghcr.io/example/app:latest", "--check-module", "sample.check",
        ], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("immutable GHCR digest", result.stderr)

    def test_exact_candidate_and_isolated_database_on_success(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")
        image = "ghcr.io/example/app@sha256:" + "a" * 64
        with patch.object(application_runtime.subprocess, "run", side_effect=run):
            application_runtime.validate(image, "sample.check", 30)
        candidate = next(args for args in calls if "--env-file" in args)
        self.assertEqual(candidate[-3:], (image, "-m", "sample.check"))
        self.assertIn("--internal", calls[0])
        readiness = next(args for args in calls if "pg_isready" in args)
        self.assertIn("-h", readiness)
        self.assertIn("127.0.0.1", readiness)
        self.assertTrue(any(args[:3] == ["docker", "network", "rm"] for args in calls))
        self.assertEqual(sum(args[:3] == ["docker", "rm", "-f"] for args in calls), 2)

    def test_failure_and_timeout_fail_closed_and_clean_up(self):
        for timed_out in (False, True):
            with self.subTest(timed_out=timed_out):
                calls = []
                def run(args, **kwargs):
                    calls.append(args)
                    if "--env-file" in args:
                        if timed_out:
                            raise subprocess.TimeoutExpired(args, 30)
                        return subprocess.CompletedProcess(args, 1, "", "failed repository check")
                    return subprocess.CompletedProcess(args, 0, "", "")
                with patch.object(application_runtime.subprocess, "run", side_effect=run):
                    with self.assertRaisesRegex(application_runtime.RuntimeFailure, "candidate repository checks"):
                        application_runtime.validate("image", "sample.check", 30)
                self.assertTrue(any(args[:3] == ["docker", "network", "rm"] for args in calls))

    def test_gate_precedes_all_release_publication_and_is_required(self):
        workflow = (ROOT / ".github/workflows/release-container.yml").read_text()
        gate = workflow.index("Validate exact application candidate digest")
        self.assertLess(workflow.index("Verify exact candidate digest"), gate)
        self.assertLess(gate, workflow.index("Publish the validated digest under release image tags"))
        self.assertLess(gate, workflow.index("Finalize semantic-release and GitHub Release"))
        self.assertIn("steps.config.outputs.runtime_validation == 'true'", workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertNotIn("always()", workflow)


if __name__ == "__main__":
    unittest.main()
