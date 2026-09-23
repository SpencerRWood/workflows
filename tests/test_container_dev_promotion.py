"""Safety and idempotency contract for container-to-dev promotion."""

from __future__ import annotations

import copy
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from update_environment_image_pin import (
    update_pin,
    validate_artifact,
)
from verify_container_promotion_pr import (
    Config,
    prepare,
    validate_pull,
    validation_result,
    verify_and_merge,
)

DIGEST = "sha256:" + "a" * 64
OLD_DIGEST = "sha256:" + "b" * 64
REPOSITORY = "ghcr.io/spencerrwood/portfolio-website"
VERSION = "v1.2.3"
REFERENCE = f"{REPOSITORY}:{VERSION}@{DIGEST}"
KEY = "portfolio_website_image_ref"
OLD = f"{KEY}: {REPOSITORY}:v1.2.2@{OLD_DIGEST}"
ENV = {
    "APPLICATION_REPOSITORY": "SpencerRWood/portfolio-website",
    "INFRASTRUCTURE_REPOSITORY": "SpencerRWood/infrastructure",
    "INFRASTRUCTURE_BASE_BRANCH": "main",
    "ENVIRONMENT_FILE": "environments/dev.yml",
    "IMAGE_KEY": KEY,
    "IMAGE_NAME": "portfolio-website",
    "RELEASE_TAG": VERSION,
    "IMAGE_REPOSITORY": REPOSITORY,
    "VERSION_IMAGE": f"{REPOSITORY}:{VERSION}",
    "IMAGE_DIGEST": DIGEST,
    "VERSION_IMAGE_DIGEST": REFERENCE,
    "STATUS_CONTEXT": "infrastructure-validation",
}


class NamingTests(unittest.TestCase):
    def test_v2_public_input_contract(self) -> None:
        workflow = (
            Path(__file__).parents[1] / ".github/workflows/promote-container-to-dev.yml"
        ).read_text(encoding="utf-8")
        inputs = workflow.split("    inputs:\n", 1)[1].split("    secrets:\n", 1)[0]
        self.assertEqual(
            re.findall(r"^      ([a-z_]+):$", inputs, re.MULTILINE),
            [
                "infrastructure_repository",
                "infrastructure_base_branch",
                "image_key",
                "image_name",
                "release_tag",
                "image_repository",
                "version_image",
                "image_digest",
                "version_image_digest",
                "status_context",
            ],
        )

    def test_only_canonical_dev_manifest_is_allowed(self) -> None:
        self.assertEqual(Config.from_environment(ENV).file, "environments/dev.yml")
        with self.assertRaisesRegex(ValueError, "only environments/dev.yml"):
            Config.from_environment(ENV | {"ENVIRONMENT_FILE": "environments/prod.yml"})
        workflow = (
            Path(__file__).parents[1] / ".github/workflows/promote-container-to-dev.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("ENVIRONMENT_FILE: environments/dev.yml", workflow)

    def test_portfolio_release_names(self) -> None:
        version = "v0.8.3"
        repository = "ghcr.io/spencerrwood/portfolio-website"
        cfg = Config.from_environment(
            ENV
            | {
                "RELEASE_TAG": version,
                "VERSION_IMAGE": f"{repository}:{version}",
                "VERSION_IMAGE_DIGEST": f"{repository}:{version}@{DIGEST}",
            }
        )
        self.assertEqual(cfg.branch, "chore/portfolio-website-v0.8.3")
        self.assertEqual(cfg.title, "chore(deps): update portfolio-website to v0.8.3")

    def test_other_image_uses_same_convention(self) -> None:
        name = "wood-events-service"
        repository = f"ghcr.io/spencerrwood/{name}"
        cfg = Config.from_environment(
            ENV
            | {
                "IMAGE_NAME": name,
                "IMAGE_REPOSITORY": repository,
                "VERSION_IMAGE": f"{repository}:{VERSION}",
                "VERSION_IMAGE_DIGEST": f"{repository}:{VERSION}@{DIGEST}",
            }
        )
        self.assertEqual(cfg.branch, "chore/wood-events-service-v1.2.3")
        self.assertEqual(cfg.title, "chore(deps): update wood-events-service to v1.2.3")

    def test_invalid_image_names_and_release_tags_are_rejected(self) -> None:
        for name in ("Portfolio-Website", "invalid/name", "-invalid"):
            with (
                self.subTest(image_name=name),
                self.assertRaisesRegex(ValueError, "invalid image name"),
            ):
                Config.from_environment(ENV | {"IMAGE_NAME": name})
        for version in ("1.2.3", "v1.2.3-rc1", "v01.2.3"):
            with (
                self.subTest(release_tag=version),
                self.assertRaisesRegex(ValueError, "invalid stable release version"),
            ):
                Config.from_environment(ENV | {"RELEASE_TAG": version})


class PinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "dev.yml"
        self.original = f"environment_name: dev\n# preserve comment\n{OLD}\nservices:\n  postgres: true\n"
        self.path.write_text(self.original)

    def test_updates_only_target_line_and_preserves_content(self) -> None:
        self.assertTrue(update_pin(self.path, KEY, REPOSITORY, VERSION, REFERENCE))
        self.assertEqual(
            self.path.read_text(), self.original.replace(OLD, f"{KEY}: {REFERENCE}")
        )

    def test_identical_pin_is_noop_and_rerun_safe(self) -> None:
        self.assertTrue(update_pin(self.path, KEY, REPOSITORY, VERSION, REFERENCE))
        self.assertFalse(update_pin(self.path, KEY, REPOSITORY, VERSION, REFERENCE))

    def test_missing_key_fails(self) -> None:
        self.path.write_text("other: value\n")
        with self.assertRaisesRegex(ValueError, "expected exactly one"):
            update_pin(self.path, KEY, REPOSITORY, VERSION, REFERENCE)

    def test_duplicate_key_fails(self) -> None:
        self.path.write_text(self.original + OLD + "\n")
        with self.assertRaisesRegex(ValueError, "found 2"):
            update_pin(self.path, KEY, REPOSITORY, VERSION, REFERENCE)

    def test_older_release_cannot_replace_newer_pin(self) -> None:
        self.path.write_text(self.original.replace("v1.2.2", "v1.2.4"))
        with self.assertRaisesRegex(ValueError, "newer application"):
            update_pin(self.path, KEY, REPOSITORY, VERSION, REFERENCE)

    def test_same_version_different_digest_blocks(self) -> None:
        self.path.write_text(self.original.replace("v1.2.2", VERSION))
        with self.assertRaisesRegex(ValueError, "different artifact"):
            update_pin(self.path, KEY, REPOSITORY, VERSION, REFERENCE)

    def test_digest_and_publisher_outputs_validated(self) -> None:
        args = {
            "owner": "SpencerRWood",
            "image_name": "portfolio-website",
            "version": VERSION,
            "image_repository": REPOSITORY,
            "version_image": f"{REPOSITORY}:{VERSION}",
            "image_digest": DIGEST,
            "version_image_digest": REFERENCE,
        }
        self.assertEqual(validate_artifact(**args), REFERENCE)
        for change in (
            {"image_digest": "sha256:abc"},
            {"image_repository": "ghcr.io/other/portfolio-website"},
            {"version_image": f"{REPOSITORY}:latest"},
            {"version_image_digest": f"{REPOSITORY}:latest@{DIGEST}"},
            {"version": "v1.2.3-rc1"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_artifact(**(args | change))


class PullTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Config.from_environment(ENV)
        self.sha = "c" * 40
        self.pull = {
            "base": {"ref": "main", "repo": {"full_name": self.cfg.repository}},
            "head": {
                "ref": self.cfg.branch,
                "repo": {"full_name": self.cfg.repository},
                "sha": self.sha,
            },
            "title": self.cfg.title,
            "state": "open",
            "changed_files": 1,
        }
        self.files = [
            {
                "filename": self.cfg.file,
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": f"@@ -1 +1 @@\n-{OLD}\n+{KEY}: {REFERENCE}",
            }
        ]

    def test_exact_promotion_passes(self) -> None:
        self.assertEqual(validate_pull(self.pull, self.files, self.cfg), self.sha)

    def test_extra_file_or_line_fails(self) -> None:
        extra = copy.deepcopy(self.files[0])
        extra["filename"] = "README.md"
        with self.assertRaises(ValueError):
            validate_pull(self.pull, self.files + [extra], self.cfg)
        self.files[0]["patch"] += "\n+unrelated: value"
        with self.assertRaises(ValueError):
            validate_pull(self.pull, self.files, self.cfg)

    def test_wrong_key_version_digest_or_repository_fails(self) -> None:
        for replacement in (
            "other_image_ref: " + REFERENCE,
            f"{KEY}: {REPOSITORY}:v1.2.4@{DIGEST}",
            f"{KEY}: {REPOSITORY}:{VERSION}@{OLD_DIGEST}",
            f"{KEY}: ghcr.io/other/portfolio-website:{VERSION}@{DIGEST}",
        ):
            with self.subTest(replacement=replacement):
                self.files[0]["patch"] = f"@@ -1 +1 @@\n-{OLD}\n+{replacement}"
                with self.assertRaises(ValueError):
                    validate_pull(self.pull, self.files, self.cfg)

    def test_wrong_base_branch_promotion_branch_or_sha_fails(self) -> None:
        for location, key, value in (
            ("base", "ref", "production"),
            ("head", "ref", "chore/other"),
            ("head", "sha", "wrong"),
        ):
            with self.subTest(value=value):
                pull = copy.deepcopy(self.pull)
                pull[location][key] = value
                with self.assertRaises(ValueError):
                    validate_pull(pull, self.files, self.cfg)

    def test_wrong_promotion_title_fails(self) -> None:
        pull = copy.deepcopy(self.pull)
        pull["title"] = "chore(deps): update website portfolio to v1.2.3"
        with self.assertRaisesRegex(ValueError, "title"):
            validate_pull(pull, self.files, self.cfg)

    def test_status_missing_pending_success_failure_error_and_wrong_sha(self) -> None:
        status = {
            "id": 1,
            "context": self.cfg.context,
            "creator": {"login": "github-actions[bot]"},
            "url": f"https://api.github.com/repos/{self.cfg.repository}/statuses/{self.sha}",
            "target_url": f"https://github.com/{self.cfg.repository}/actions/runs/123",
            "state": "success",
        }
        self.assertEqual(validation_result([], self.cfg, self.sha), "pending")
        self.assertEqual(validation_result([status], self.cfg, "d" * 40), "pending")
        self.assertEqual(validation_result([status], self.cfg, self.sha), "success")
        for state in ("pending", "failure", "error"):
            with self.subTest(state=state):
                status["state"] = state
                if state == "pending":
                    self.assertEqual(
                        validation_result([status], self.cfg, self.sha), "pending"
                    )
                else:
                    with self.assertRaisesRegex(ValueError, state):
                        validation_result([status], self.cfg, self.sha)

    def test_timeout_and_changed_sha_block(self) -> None:
        with (
            patch(
                "verify_container_promotion_pr.pull_and_files",
                return_value=(self.pull, self.files),
            ),
            patch(
                "verify_container_promotion_pr.status_on_head", return_value="pending"
            ),
            self.assertRaises(TimeoutError),
        ):
            verify_and_merge(31, self.cfg, timeout=0)
        stale = copy.deepcopy(self.pull)
        stale["head"]["sha"] = "d" * 40
        with (
            patch(
                "verify_container_promotion_pr.pull_and_files",
                side_effect=[(self.pull, self.files), (stale, self.files)],
            ),
            patch(
                "verify_container_promotion_pr.status_on_head", return_value="success"
            ),
            self.assertRaisesRegex(ValueError, "head SHA changed"),
        ):
            verify_and_merge(31, self.cfg)

    def test_existing_pr_reused_without_push(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "verify_container_promotion_pr.Path", return_value=Path(tmp) / "dev.yml"
            ),
            patch("verify_container_promotion_pr.update_pin", return_value=True),
            patch("verify_container_promotion_pr.subprocess.run") as run,
            patch("verify_container_promotion_pr.find_open_pr", return_value=31),
            patch(
                "verify_container_promotion_pr.pull_and_files",
                return_value=(self.pull, self.files),
            ),
        ):
            run.return_value.returncode = 0
            self.assertEqual(prepare(self.cfg), 31)

    def test_already_pinned_skips_pr(self) -> None:
        with (
            patch("verify_container_promotion_pr.update_pin", return_value=False),
            patch("verify_container_promotion_pr.find_open_pr") as find,
        ):
            self.assertIsNone(prepare(self.cfg))
            find.assert_not_called()

    def test_already_merged_pr_does_not_create_duplicate(self) -> None:
        with (
            patch("verify_container_promotion_pr.update_pin", return_value=True),
            patch("verify_container_promotion_pr.subprocess.run") as run,
            patch(
                "verify_container_promotion_pr.find_previous_pr",
                return_value={"merged_at": "now"},
            ),
            patch(
                "verify_container_promotion_pr.main_pin",
                return_value=f"{KEY}: {REFERENCE}\n",
            ),
            patch("verify_container_promotion_pr.create_pr") as create,
        ):
            run.return_value.returncode = 2
            self.assertIsNone(prepare(self.cfg))
            create.assert_not_called()

    def test_newer_main_pin_blocks_merge_after_validation(self) -> None:
        newer = f"{KEY}: {REPOSITORY}:v1.2.4@{OLD_DIGEST}\n"
        with (
            patch(
                "verify_container_promotion_pr.pull_and_files",
                return_value=(self.pull, self.files),
            ),
            patch(
                "verify_container_promotion_pr.status_on_head", return_value="success"
            ),
            patch("verify_container_promotion_pr.main_pin", return_value=newer),
            patch("verify_container_promotion_pr.command") as command,
            self.assertRaisesRegex(ValueError, "newer application"),
        ):
            verify_and_merge(31, self.cfg)
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
