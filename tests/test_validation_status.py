"""Commit-status adapter and opt-in reusable workflow contract tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validation_status import status_for_phase, status_request  # noqa: E402


class ValidationStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sha = "a" * 40
        self.environment = {
            "GITHUB_EVENT_NAME": "pull_request",
            "PR_HEAD_SHA": self.sha,
            "STATUS_CONTEXT": "infrastructure-validation",
            "GITHUB_REPOSITORY": "SpencerRWood/infrastructure",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_SERVER_URL": "https://github.com",
        }

    def test_pending_targets_exact_pr_head(self) -> None:
        command = status_request("pending", self.environment)
        self.assertIn(f"repos/SpencerRWood/infrastructure/statuses/{self.sha}", command)
        self.assertIn("state=pending", command)
        self.assertIn("context=infrastructure-validation", command)

    def test_success_and_failure_follow_validation_result(self) -> None:
        self.assertEqual(status_for_phase("final", "success")[0], "success")
        self.assertEqual(status_for_phase("final", "failure")[0], "failure")

    def test_cancellation_and_unknown_result_never_succeed(self) -> None:
        self.assertEqual(status_for_phase("final", "cancelled")[0], "error")
        self.assertEqual(status_for_phase("final", "skipped")[0], "error")
        self.assertEqual(status_for_phase("final", "")[0], "error")

    def test_missing_head_or_context_is_rejected(self) -> None:
        for key in ("PR_HEAD_SHA", "STATUS_CONTEXT"):
            environment = {**self.environment, key: ""}
            with self.subTest(key=key), self.assertRaises(ValueError):
                status_request("pending", environment)

    def test_non_pr_event_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "pull_request event"):
            status_request("pending", {**self.environment, "GITHUB_EVENT_NAME": "push"})

    def test_publishing_is_opt_in_and_wraps_existing_validation(self) -> None:
        workflow = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        self.assertIn("publish_commit_status:", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("default: validation", workflow)
        self.assertNotIn("statuses: write", workflow)
        self.assertNotIn("\npermissions:\n", workflow)
        self.assertIn("if: ${{ inputs.publish_commit_status }}", workflow)
        self.assertIn("if: ${{ always() && inputs.publish_commit_status }}", workflow)
        self.assertLess(
            workflow.index("Publish pending validation status"),
            workflow.index("Check out consumer repository"),
        )
        self.assertLess(
            workflow.index("Publish pending validation status"),
            workflow.index("Load release configuration"),
        )
        self.assertLess(
            workflow.index("Run pre-commit"),
            workflow.index("Publish final validation status"),
        )


if __name__ == "__main__":
    unittest.main()
