"""Real Node-only consumer and existing central semantic-release regression fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).parents[1]


class NodeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "consumer"
        shutil.copytree(ROOT / "tests/fixtures/node-only", self.root)

    def test_node_consumer_needs_no_python_project_or_uv_lock(self):
        output = self.root / "outputs"
        result = subprocess.run([sys.executable, str(ROOT / "scripts/release_config.py"), str(self.root / ".github/release.toml")], env={**os.environ, "GITHUB_OUTPUT": str(output)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        self.assertEqual(values["python_enabled"], "false")
        self.assertEqual(values["node_check_format"], "true")
        self.assertFalse((self.root / "uv.lock").exists())
        if shutil.which("node"):
            subprocess.run(["npm", "run", "build"], cwd=self.root / "frontend", check=True, capture_output=True)

    def test_actual_central_engine_calculates_without_consumer_dependencies_or_mutation(self):
        def git(*args):
            return subprocess.check_output(["git", *args], cwd=self.root, text=True, stderr=subprocess.PIPE).strip()
        git("init", "-q", "-b", "main")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        git("remote", "add", "origin", "https://github.com/owner/node-fixture.git")
        git("add", ".")
        git("commit", "-qm", "chore: baseline")
        git("commit", "-qm", "feat: first Node application", "--allow-empty")
        config = Path(self.temp.name) / "semantic.json"
        subprocess.run([sys.executable, str(ROOT / "scripts/semantic_release_config.py"), str(self.root / "pyproject.toml"), str(config), "--tag-merged-commit"], check=True)
        self.assertFalse(json.loads(config.read_text())["semantic_release"].get("version_toml"))
        executable = Path(sys.executable).parent / "semantic-release"
        self.assertTrue(executable.is_file())
        before = git("rev-parse", "HEAD")
        env = {key:value for key,value in os.environ.items() if key not in {"GH_TOKEN", "GITHUB_TOKEN", "OPENPROJECT_API_TOKEN", "GITHUB_OUTPUT"}}
        result = subprocess.run([str(executable), "--config", str(config), "version", "--print-tag"], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "v0.1.0")
        self.assertEqual(git("rev-parse", "HEAD"), before)
        self.assertEqual(git("tag", "--list"), "")
        self.assertEqual(git("status", "--porcelain"), "")

    def test_node_paths_do_not_install_consumer_python_dependencies(self):
        for name in ("validate", "release", "release-container"):
            document = yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())
            steps = [step for job in document["jobs"].values() for step in job.get("steps", [])]
            for step in steps:
                if step.get("name") in {"Set up uv", "Sync Python dependencies", "Lock prepared version before building the candidate", "Prepare semantic release metadata without a commit or tag"}:
                    self.assertIn("python_enabled == 'true'", step.get("if", ""), step)
            if name != "validate":
                install = next(step for step in steps if step.get("name") == "Install locked central release engine for Node-only consumers")
                self.assertIn("--no-deps --require-hashes", install["run"])
                self.assertNotIn("uv ", install["run"])

    def test_metadata_only_policy_cannot_update_version_files(self):
        path = self.root / "pyproject.toml"
        path.write_text('[tool.semantic_release]\nversion_variables=["frontend/package.json:version"]\n')
        result = subprocess.run([sys.executable, str(ROOT / "scripts/semantic_release_config.py"), str(path), str(self.root / "semantic.json"), "--tag-merged-commit"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot update version files", result.stderr)

    def test_health_gate_rejects_mutable_identity_before_docker(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import container_health
        with patch.object(container_health.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "exact GHCR digest"):
                container_health.verify("ghcr.io/owner/app:latest")
            run.assert_not_called()

    def test_health_gate_cleans_up_an_unhealthy_candidate(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import container_health
        image = "ghcr.io/owner/app@sha256:" + "a" * 64
        responses = ['{"Healthcheck":{"Test":["CMD","check"]}}', "fixture-container", '{"Running":true,"Health":{"Status":"unhealthy"}}']
        with patch.object(container_health.subprocess, "run") as run, patch.object(container_health.subprocess, "check_output", side_effect=responses):
            with self.assertRaisesRegex(ValueError, "failed its health check"):
                container_health.verify(image)
            self.assertEqual(run.call_args.args[0], ["docker", "rm", "--force", "fixture-container"])


if __name__ == "__main__":
    unittest.main()
