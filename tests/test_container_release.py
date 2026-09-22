"""Container release contract and source-integrity tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import container_release  # noqa: E402


class ContainerReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "Dockerfile"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "feat: release",
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.root), "tag", "v1.2.3"], check=True)
        self.environment = {
            "RELEASE_TAG": "v1.2.3",
            "GITHUB_REPOSITORY": "SpencerRWood/Website-Portfolio",
            "GITHUB_WORKSPACE": str(self.root),
            "BUILD_CONTEXT": ".",
            "DOCKERFILE": "Dockerfile",
            "PLATFORMS": "linux/amd64",
        }

    def prepare(
        self, environment: dict[str, str] | None = None, *, release: dict | None = None
    ) -> dict[str, str]:
        real_command = container_release.command

        def command(*args: str) -> str:
            if args[0] == "gh":
                return json.dumps(
                    release
                    or {"tag_name": "v1.2.3", "draft": False, "prerelease": False}
                )
            return real_command(*args)

        with patch.object(container_release, "command", side_effect=command):
            return container_release.prepare(environment or self.environment)

    def test_generates_version_and_full_sha_references(self) -> None:
        result = self.prepare()
        sha = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(
            result["image_repository"], "ghcr.io/spencerrwood/website-portfolio"
        )
        self.assertEqual(
            result["version_image"], "ghcr.io/spencerrwood/website-portfolio:v1.2.3"
        )
        self.assertEqual(
            result["sha_image"], f"ghcr.io/spencerrwood/website-portfolio:sha-{sha}"
        )

    def test_rejects_missing_or_invalid_release_tag(self) -> None:
        for tag in ("", "main", "v1.2", "v01.2.3", "v1.2.3; echo unsafe"):
            with (
                self.subTest(tag=tag),
                self.assertRaisesRegex(ValueError, "release_tag"),
            ):
                self.prepare({**self.environment, "RELEASE_TAG": tag})

    def test_rejects_checkout_at_other_commit(self) -> None:
        (self.root / "other.txt").write_text("later\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "other.txt"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "fix: later",
            ],
            check=True,
        )
        with self.assertRaisesRegex(ValueError, "does not match release tag"):
            self.prepare()

    def test_rejects_draft_or_prerelease(self) -> None:
        for field in ("draft", "prerelease"):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "published"),
            ):
                self.prepare(
                    release={
                        "tag_name": "v1.2.3",
                        "draft": False,
                        "prerelease": False,
                        field: True,
                    }
                )

    def test_rejects_paths_outside_release_and_invalid_image_names(self) -> None:
        for override in (
            {"DOCKERFILE": "../Dockerfile"},
            {"BUILD_CONTEXT": "../"},
            {"IMAGE_NAME": "other/image"},
            {"PLATFORMS": "linux/amd64,invalid"},
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.prepare({**self.environment, **override})

    def test_workflow_exposes_digest_and_uses_exact_release_source(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "container-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ref: ${{ inputs.release_tag }}", workflow)
        self.assertIn(
            "version_image_digest: ${{ steps.prepare.outputs.version_image }}@${{ steps.build.outputs.digest }}",
            workflow,
        )
        self.assertIn("packages: write", workflow)
        self.assertIn("flavor: latest=false", workflow)
        self.assertIn(
            "type=raw,value=${{ steps.prepare.outputs.release_tag }}", workflow
        )
        self.assertIn(
            "type=raw,value=sha-${{ steps.prepare.outputs.commit_sha }}", workflow
        )
        self.assertIn('docker buildx imagetools inspect "$image"', workflow)
        self.assertIn("Verify pushed digest", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertNotIn("push:\n", workflow)


if __name__ == "__main__":
    unittest.main()
