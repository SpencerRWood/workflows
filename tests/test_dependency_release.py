"""Release policy tests against Python Semantic Release's actual parser."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from semantic_release.enums import LevelBump


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dependency_release_parser import DependencyReleaseParser  # noqa: E402


class DependencyReleaseTests(unittest.TestCase):
    def test_release_levels(self) -> None:
        parser = DependencyReleaseParser()
        cases = {
            "feat: something": LevelBump.MINOR,
            "fix: something": LevelBump.PATCH,
            "chore(deps): something": LevelBump.PATCH,
            "chore(deps): update caddy docker tag to v2.11.4": LevelBump.PATCH,
            "chore(deps): pin dependencies": LevelBump.PATCH,
            "chore: something": LevelBump.NO_RELEASE,
            "chore: update docs": LevelBump.NO_RELEASE,
            "chore: cleanup config": LevelBump.NO_RELEASE,
            "chore: reorganize tooling": LevelBump.NO_RELEASE,
            "docs: something": LevelBump.NO_RELEASE,
            "chore(tooling): something": LevelBump.NO_RELEASE,
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                result = parser.parse_message(message)
                self.assertIsNotNone(result)
                self.assertEqual(result.bump, expected)

    def test_consumer_config_is_preserved_except_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pyproject = root / "pyproject.toml"
            pyproject.write_text(
                '[tool.semantic_release]\ncommit_parser = "conventional"\n'
                'tag_format = "v{version}"\nversion_toml = ["pyproject.toml:project.version"]\n'
                '[tool.semantic_release.commit_parser_options]\n'
                'minor_tags = ["feat"]\npatch_tags = ["fix", "perf"]\n',
                encoding="utf-8",
            )
            output = root / "release.json"
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "semantic_release_config.py"), str(pyproject), str(output)],
                check=True,
            )
            config = json.loads(output.read_text(encoding="utf-8"))["semantic_release"]
            self.assertEqual(config["tag_format"], "v{version}")
            self.assertEqual(config["version_toml"], ["pyproject.toml:project.version"])
            self.assertEqual(config["commit_parser_options"]["patch_tags"], ["fix", "perf"])
            self.assertTrue(config["commit_parser"].endswith("dependency_release_parser.py:DependencyReleaseParser"))

    def test_shared_workflow_uses_generated_config(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/semantic_release_config.py", workflow)
        self.assertIn('semantic-release --config "$RUNNER_TEMP/semantic-release-config.json" version --vcs-release', workflow)
        self.assertNotIn("uv lock --offline", workflow)
        stages = (
            "version --no-commit --no-tag --no-push --no-vcs-release --skip-build",
            "              uv lock\n",
            "git add uv.lock",
            'version --vcs-release "${release_options[@]}"',
        )
        self.assertEqual(list(map(workflow.index, stages)), sorted(map(workflow.index, stages)))
        self.assertIn("if [[ \"${{ steps.config.outputs.tag_merged_commit }}\" != 'true' ]]; then", workflow)

    def test_tag_only_release_tags_validated_head_without_source_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pyproject = root / "pyproject.toml"
            pyproject.write_text(
                '[project]\nname = "release-test"\ndynamic = ["version"]\n'
                '[tool.semantic_release]\ncommit_parser = "conventional"\n'
                'tag_format = "v{version}"\n'
                '[tool.semantic_release.commit_parser_options]\n'
                'minor_tags = ["feat"]\npatch_tags = ["fix", "perf"]\n',
                encoding="utf-8",
            )
            remote = root / "remote.git"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=root, check=True)
            subprocess.run(["git", "add", "pyproject.toml"], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "feat: initial release"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "tag", "v1.2.3"], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "chore(deps): update image"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "push", "origin", "main", "--tags"], cwd=root, check=True, capture_output=True)
            output = root / "release.json"
            subprocess.run([sys.executable, str(ROOT / "scripts" / "semantic_release_config.py"), str(pyproject), str(output), "--tag-merged-commit"], check=True)
            head_before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            result = subprocess.run(
                [sys.executable, "-m", "semantic_release", "--config", str(output), "version", "--no-commit", "--no-changelog", "--no-push", "--no-vcs-release", "--skip-build"],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            head_after = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            tag_target = subprocess.check_output(["git", "rev-parse", "v1.2.4^{}"], cwd=root, text=True).strip()
            self.assertEqual(head_before, head_after)
            self.assertEqual(head_after, tag_target)

    def test_dependency_commit_increments_patch_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pyproject = root / "pyproject.toml"
            pyproject.write_text(
                '[project]\nname = "release-test"\nversion = "1.2.3"\n'
                '[tool.semantic_release]\ncommit_parser = "conventional"\n'
                'tag_format = "v{version}"\nversion_toml = ["pyproject.toml:project.version"]\n'
                '[tool.semantic_release.commit_parser_options]\n'
                'minor_tags = ["feat"]\npatch_tags = ["fix", "perf"]\n',
                encoding="utf-8",
            )
            subprocess.run(["uv", "lock", "--offline"], cwd=root, check=True, capture_output=True)
            config = root / "release.json"
            subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/release-test.git"],
                cwd=root, check=True, capture_output=True,
            )
            subprocess.run(["git", "add", "pyproject.toml", "uv.lock"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "feat: initial release"],
                cwd=root, check=True, capture_output=True,
            )
            subprocess.run(["git", "tag", "v1.2.3"], cwd=root, check=True, capture_output=True)
            (root / "dependency.txt").write_text("v2.11.4\n", encoding="utf-8")
            subprocess.run(["git", "add", "dependency.txt"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "chore(deps): update caddy docker tag to v2.11.4"],
                cwd=root, check=True, capture_output=True,
            )
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "semantic_release_config.py"), str(pyproject), str(config)],
                check=True,
            )
            result = subprocess.run(
                [sys.executable, "-m", "semantic_release", "--config", str(config), "version", "--print"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "1.2.4")
            again = subprocess.run(
                [sys.executable, "-m", "semantic_release", "--config", str(config), "version", "--print-tag"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(again.stdout.strip(), "v1.2.4")
            self.assertFalse((root / "CHANGELOG.md").exists())
            prepare = subprocess.run(
                [sys.executable, "-m", "semantic_release", "--config", str(config), "version", "--no-commit", "--no-tag", "--no-push", "--no-vcs-release", "--skip-build"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(prepare.returncode, 0, prepare.stderr)
            self.assertFalse(subprocess.run(["git", "show-ref", "--verify", "--quiet", "refs/tags/v1.2.4"], cwd=root).returncode == 0)
            prepared_version = subprocess.run(
                [sys.executable, "-m", "semantic_release", "--config", str(config), "version", "--print-tag"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(prepared_version.stdout.strip(), "v1.2.4")
            prepared_files = {
                name: (root / name).read_text()
                for name in ("pyproject.toml", "CHANGELOG.md")
            }
            subprocess.run(["uv", "lock", "--offline"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "add", "uv.lock"], cwd=root, check=True)
            publish = subprocess.run(
                [sys.executable, "-m", "semantic_release", "--config", str(config), "version", "--no-push", "--no-vcs-release"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(publish.returncode, 0, publish.stderr)
            tag_sha = subprocess.check_output(["git", "rev-parse", "v1.2.4^{commit}"], cwd=root, text=True).strip()
            head_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            self.assertEqual(tag_sha, head_sha)
            locked = subprocess.check_output(["git", "show", "v1.2.4:uv.lock"], cwd=root, text=True)
            self.assertIn('name = "release-test"\nversion = "1.2.4"', locked)
            self.assertEqual(
                prepared_files,
                {name: (root / name).read_text() for name in prepared_files},
            )


if __name__ == "__main__":
    unittest.main()
