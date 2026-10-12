"""Credential separation and no-bypass destructive-rule regressions."""

import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import release_credentials
import release_writer_policy

ROOT = Path(__file__).parents[1]


class CredentialsTests(unittest.TestCase):
    def test_finalization_shell_requires_app_token_even_when_workflow_token_exists(
        self,
    ):
        document = yaml.safe_load(
            (ROOT / ".github/workflows/release-container.yml").read_text()
        )
        final = next(
            step
            for step in document["jobs"]["release"]["steps"]
            if step.get("id") == "publish"
        )
        selection = final["run"].split(
            'python "$RUNNER_TEMP/workflow-contract/scripts/release_head.py"'
        )[0]
        for mode, app_token, expected_code in (
            ("github-app", "", 1),
            ("github-app", "test-app-token", 0),
            ("github-token", "", 0),
            ("unsupported", "", 1),
        ):
            environment = {
                **os.environ,
                "RELEASE_GIT_AUTH": mode,
                "RELEASE_APP_TOKEN": app_token,
                "WORKFLOW_TOKEN": "test-workflow-token",
                "EXPECTED_TOKEN": app_token
                if mode == "github-app"
                else "test-workflow-token",
            }
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    selection
                    + 'test "$GH_TOKEN" = "$EXPECTED_TOKEN"\ntest "$GITHUB_TOKEN" = "$EXPECTED_TOKEN"\n',
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            with self.subTest(mode=mode, token_present=bool(app_token)):
                self.assertEqual(result.returncode, expected_code)
                self.assertEqual(result.stdout + result.stderr, "")

    def test_requested_app_never_accepts_missing_credentials_or_unknown_mode(self):
        for env in (
            {},
            {"RELEASE_GIT_AUTH": "unknown"},
            {"RELEASE_GIT_AUTH": "github-app"},
        ):
            with self.subTest(env=env), self.assertRaises(ValueError):
                release_credentials.validate(env)
        env = {
            "RELEASE_GIT_AUTH": "github-app",
            "RELEASE_APP_CLIENT_ID": "client",
            "RELEASE_APP_SLUG": "release",
            "RELEASE_APP_PRIVATE_KEY": "fake-key",
        }
        for missing in (
            "RELEASE_APP_CLIENT_ID",
            "RELEASE_APP_SLUG",
            "RELEASE_APP_PRIVATE_KEY",
        ):
            changed = dict(env, **{missing: ""})
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                release_credentials.validate(changed)
        self.assertEqual(release_credentials.validate(env), "github-app")
        self.assertEqual(
            release_credentials.validate({"RELEASE_GIT_AUTH": "github-token"}),
            "github-token",
        )

    def test_cli_does_not_emit_supplied_private_key(self):
        marker = "test-private-key-must-not-appear"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/release_credentials.py")],
            env={
                **os.environ,
                "RELEASE_GIT_AUTH": "github-app",
                "RELEASE_APP_PRIVATE_KEY": marker,
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "blocked")

    def test_workflow_keeps_git_app_out_of_registry_and_attestation_auth(self):
        workflow = (ROOT / ".github/workflows/release-container.yml").read_text()
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("permission-contents: write", workflow)
        self.assertIn("skip-token-revoke: false", workflow)
        self.assertIn("repositories: ${{ github.event.repository.name }}", workflow)
        self.assertIn('test -n "$RELEASE_APP_TOKEN"', workflow)
        self.assertIn('export GH_TOKEN="$RELEASE_APP_TOKEN"', workflow)
        self.assertIn("password: ${{ github.token }}", workflow)
        self.assertLess(
            workflow.index("Require explicit release Git credentials"),
            workflow.index("Build candidate image once"),
        )
        self.assertLess(
            workflow.index("Verify reviewed Git writer identity"),
            workflow.index("Publish the validated digest under release image tags"),
        )
        self.assertLess(
            workflow.index("Validate exact Dagster candidate digest"),
            workflow.index("Mint repository-scoped Git writer token"),
        )
        app = workflow[
            workflow.index("Mint repository-scoped Git writer token") : workflow.index(
                "Publish the validated digest under release image tags"
            )
        ]
        self.assertNotIn("permission-administration", app)
        self.assertNotIn("permission-workflows", app)
        self.assertIn("timeout-minutes: 10", workflow)


class WriterPolicyTests(unittest.TestCase):
    def setUp(self):
        base = {
            "target": "branch",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
        }
        self.rules = [
            {
                **base,
                "rules": [{"type": "update"}],
                "bypass_actors": [
                    {
                        "actor_id": 123,
                        "actor_type": "Integration",
                        "bypass_mode": "always",
                    }
                ],
            },
            {
                **base,
                "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
                "bypass_actors": [],
            },
        ]

    def test_app_can_update_but_cannot_bypass_destructive_rules(self):
        release_writer_policy.validate(self.rules, 123)
        all_branches = copy.deepcopy(self.rules)
        all_branches[1]["conditions"] = {
            "ref_name": {"include": ["~ALL"], "exclude": []}
        }
        release_writer_policy.validate(all_branches, 123)
        merged = copy.deepcopy(self.rules[:1])
        merged[0]["rules"] += self.rules[1]["rules"]
        with self.assertRaises(ValueError):
            release_writer_policy.validate(merged, 123)
        changed = copy.deepcopy(self.rules)
        changed[1]["bypass_actors"] = changed[0]["bypass_actors"]
        with self.assertRaises(ValueError):
            release_writer_policy.validate(changed, 123)

    def test_inactive_wrong_app_extra_bypass_and_wrong_ref_are_rejected(self):
        for change in ("disabled", "wrong-app", "human-bypass", "wrong-ref"):
            rules = copy.deepcopy(self.rules)
            if change == "disabled":
                rules[0]["enforcement"] = "disabled"
            elif change == "wrong-app":
                rules[0]["bypass_actors"][0]["actor_id"] = 456
            elif change == "human-bypass":
                rules[0]["bypass_actors"].append(
                    {
                        "actor_type": "RepositoryRole",
                        "actor_id": 5,
                        "bypass_mode": "always",
                    }
                )
            else:
                rules[0]["conditions"]["ref_name"]["include"] = [
                    "refs/heads/release/v0.6.0"
                ]
            with self.subTest(change=change), self.assertRaises(ValueError):
                release_writer_policy.validate(rules, 123)


if __name__ == "__main__":
    unittest.main()
