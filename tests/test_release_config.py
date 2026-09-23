"""Black-box tests for the release configuration loader."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "release_config.py"
WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "release.yml"
VALIDATION_WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "validate.yml"


class ReleaseConfigTests(unittest.TestCase):
    """Exercise supported and invalid configuration combinations."""

    def run_config(self, config: str, files: tuple[str, ...] = ()) -> tuple[subprocess.CompletedProcess[str], str]:
        """Run the loader against a small representative consumer checkout."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = root / ".github" / "release.toml"
            config_path.parent.mkdir()
            config_path.write_text(config, encoding="utf-8")
            (root / "pyproject.toml").touch()
            (root / "uv.lock").touch()
            for file_name in files:
                file_path = root / file_name
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.touch()
            output_path = root / "outputs"
            environment = {**os.environ, "GITHUB_OUTPUT": str(output_path)}
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(config_path)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            outputs = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
            return result, outputs

    def test_python_package_configuration(self) -> None:
        result, outputs = self.run_config(
            """version = 1
[python]
[validation]
checks = ["ruff", "ruff-format", "mypy", "pytest", "pre-commit"]
[build]
python_package = true
[release]
semantic_release = true
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check_ruff=true", outputs)
        self.assertIn("build_python_package=true", outputs)

    def test_node_configuration(self) -> None:
        result, outputs = self.run_config(
            """version = 1
[python]
[validation]
checks = ["mypy", "pytest"]
[node]
directory = "frontend"
checks = ["lint", "typecheck", "test", "build"]
[release]
semantic_release = true
""",
            ("frontend/package-lock.json",),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("node_enabled=true", outputs)
        self.assertIn("node_check_typecheck=true", outputs)

    def test_monorepo_python_project_directory_is_emitted(self) -> None:
        result, outputs = self.run_config(
            """version = 1
[project]
working_directory = "backend"
[python]
[validation]
checks = ["mypy", "pytest", "pre-commit"]
[release]
semantic_release = true
""",
            ("backend/pyproject.toml", "backend/uv.lock"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("working_directory=backend", outputs)

    def test_pre_commit_resolves_from_configured_python_project(self) -> None:
        workflow = VALIDATION_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            'uv run --directory "${{ steps.config.outputs.working_directory }}" pre-commit run --files',
            workflow,
        )
        self.assertIn('git -C "$GITHUB_WORKSPACE" ls-files -z', workflow)

    def test_helper_checkout_is_outside_consumer_workspace(self) -> None:
        workflow = VALIDATION_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("WORKFLOW_SHA: ${{ github.workflow_sha }}", workflow)
        self.assertIn('git -C "$RUNNER_TEMP/workflow-contract" fetch --depth 1 origin "$WORKFLOW_SHA"', workflow)
        self.assertIn(
            'python "$RUNNER_TEMP/workflow-contract/scripts/release_config.py" .github/release.toml',
            workflow,
        )
        self.assertNotIn(".workflow-contract", workflow)

    def test_consumer_validation_stays_in_configured_working_directory(self) -> None:
        workflow = VALIDATION_WORKFLOW.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            workflow.count("working-directory: ${{ steps.config.outputs.working_directory }}"),
            10,
        )
        self.assertIn("working-directory: ${{ steps.config.outputs.node_directory }}", workflow)

    def test_public_workflow_contract_has_no_inputs(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("on:\n  workflow_call:", workflow)
        self.assertNotIn("workflow_call:\n    inputs:", workflow)

    def test_release_calls_the_same_validation_contract_as_pull_requests(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("uses: SpencerRWood/workflows/.github/workflows/validate.yml@v1", workflow)
        self.assertIn("needs: validation", workflow)

    def test_compose_capability_is_exposed(self) -> None:
        result, outputs = self.run_config(
            """version = 1
[python]
[validation]
checks = ["mypy", "pytest", "docker-compose", "pre-commit"]
[release]
semantic_release = true
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check_docker_compose=true", outputs)

    def test_coverage_requires_a_target_and_pytest(self) -> None:
        result, _ = self.run_config(
            """version = 1
[python]
[validation]
checks = ["pytest", "pytest-coverage"]
[release]
semantic_release = true
"""
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("coverage.target", result.stderr)

    def test_coverage_configuration(self) -> None:
        result, outputs = self.run_config(
            """version = 1
[python]
[validation]
checks = ["pytest", "pytest-coverage"]
[coverage]
target = "example_package"
[release]
semantic_release = true
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coverage_target=example_package", outputs)

    def test_dbt_configuration(self) -> None:
        result, outputs = self.run_config(
            """version = 1
[python]
[validation]
checks = ["sqlfluff", "dbt-deps", "dbt-parse", "pre-commit"]
[dbt]
profiles_example = "profiles.example.yml"
[release]
semantic_release = true
""",
            ("profiles.example.yml",),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check_dbt_deps=true", outputs)
        self.assertIn("check_dbt_parse=true", outputs)

    def test_dbt_parse_requires_dependency_capability(self) -> None:
        result, _ = self.run_config(
            """version = 1
[python]
[validation]
checks = ["dbt-parse"]
[release]
semantic_release = true
""",
            ("profiles.example.yml",),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dbt-deps", result.stderr)

    def test_unknown_capability_fails_clearly(self) -> None:
        result, _ = self.run_config(
            """version = 1
[python]
[validation]
checks = ["skip-quality"]
[release]
semantic_release = true
"""
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported capability", result.stderr)

    def test_invalid_toml_fails_clearly(self) -> None:
        result, _ = self.run_config("version = [\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not valid TOML", result.stderr)


if __name__ == "__main__":
    unittest.main()
