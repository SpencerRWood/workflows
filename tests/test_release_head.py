"""Release freshness checks against a branch that changes during validation."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "release_head.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-ansible.yml"


def git(directory: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()


class ReleaseHeadTests(unittest.TestCase):
    def test_newer_main_supersedes_validated_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote, checkout = root / "remote.git", root / "checkout"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(checkout)], check=True, capture_output=True)
            git(checkout, "config", "user.email", "test@example.com")
            git(checkout, "config", "user.name", "Test")
            git(checkout, "remote", "add", "origin", str(remote))
            (checkout / "file").write_text("A", encoding="utf-8")
            git(checkout, "add", "file")
            git(checkout, "commit", "-m", "chore(deps): update image")
            git(checkout, "push", "origin", "main")
            first = git(checkout, "rev-parse", "HEAD")
            output = root / "outputs"

            def check() -> subprocess.CompletedProcess[str]:
                output.write_text("", encoding="utf-8")
                return subprocess.run(
                    [sys.executable, str(SCRIPT)],
                    cwd=checkout,
                    env={**os.environ, "RELEASE_REF": "refs/heads/main", "GITHUB_OUTPUT": str(output)},
                    text=True,
                    capture_output=True,
                    check=False,
                )

            self.assertEqual(check().returncode, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "current=true\n")
            (checkout / "file").write_text("B", encoding="utf-8")
            git(checkout, "commit", "-am", "feat: newer change")
            git(checkout, "push", "origin", "main")
            newer = git(checkout, "rev-parse", "HEAD")
            git(checkout, "checkout", "--detach", first)
            stale = check()
            self.assertEqual(stale.returncode, 0, stale.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "current=false\n")
            self.assertIn(newer, stale.stdout)
            git(checkout, "checkout", "--detach", newer)
            self.assertEqual(check().returncode, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "current=true\n")

    def test_workflow_keeps_validation_and_serializes_release_without_cancelling_active_run(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("group: release-${{ github.repository }}-${{ github.ref }}", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("needs: validation", workflow)
        self.assertLess(workflow.index("scripts/release_head.py"), workflow.index("Run semantic-release"))
        self.assertIn("if: steps.current_head.outputs.current == 'true'", workflow)

    def test_automatic_deploy_guard_runs_after_validation_and_before_apply(self) -> None:
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("skip_superseded:", workflow)
        self.assertIn("default: false", workflow)
        self.assertLess(
            workflow.index("- name: Validate release and target"),
            workflow.index("- name: Check release is still the default branch head"),
        )
        self.assertLess(
            workflow.index("- name: Check release is still the default branch head"),
            workflow.index("- name: Apply release configuration"),
        )
        self.assertIn("if: steps.current_head.outputs.current == 'true'", workflow)


if __name__ == "__main__":
    unittest.main()
