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
            config = root / "release.json"
            subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/example/release-test.git"],
                cwd=root, check=True, capture_output=True,
            )
            subprocess.run(["git", "add", "pyproject.toml"], cwd=root, check=True, capture_output=True)
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


if __name__ == "__main__":
    unittest.main()
