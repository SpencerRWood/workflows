"""Dagster candidate-image gate contract tests."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import dagster_runtime  # noqa: E402


class DagsterRuntimeTests(unittest.TestCase):
    def test_cleanup_restores_nested_ownership_without_following_symlinks(self) -> None:
        image = "ghcr.io/example/app@sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            logs = home / "storage" / "run" / "compute_logs"
            logs.mkdir(parents=True)
            (logs / "result.out").write_text("success")
            (home / "external").symlink_to(home.parent, target_is_directory=True)
            with patch.object(dagster_runtime, "command") as docker:
                dagster_runtime.restore_home_ownership(image, home)
            args = docker.call_args.args
            self.assertIn(f"type=bind,src={home},dst=/dagster-home", args)
            self.assertEqual(args[args.index("--user") + 1], "0:0")
            self.assertEqual(args[args.index("--network") + 1], "none")
            self.assertEqual(args[-3:-1], (image, "-c"))
            with patch("os.chown") as chown:
                exec(args[-1].replace("/dagster-home", str(home)))
            paths = {call.args[0] for call in chown.call_args_list}
            self.assertEqual(paths, {
                str(home), str(home / "storage"), str(home / "storage" / "run"),
                str(logs), str(logs / "result.out"), str(home / "external"),
            })
            for call in chown.call_args_list[1:]:
                self.assertFalse(call.kwargs["follow_symlinks"])

    def test_cleanup_failure_is_reported(self) -> None:
        with patch.object(dagster_runtime, "command", side_effect=
                          subprocess.CalledProcessError(1, ["docker"])):
            with self.assertRaises(subprocess.CalledProcessError):
                dagster_runtime.restore_home_ownership("image", Path("/tmp/home"))

    def test_instance_uses_postgres_for_all_dagster_storage(self) -> None:
        config = dagster_runtime.instance_yaml()
        self.assertIn("storage:\n  postgres:\n    postgres_db:", config)
        self.assertIn("db_name: dagster_ci", config)
        self.assertNotIn("sqlite", config)

    def test_client_uses_exact_image_and_shared_instance(self) -> None:
        image = "ghcr.io/example/app@sha256:" + "a" * 64
        args = dagster_runtime.client_args(image, "isolated", Path("/tmp/home"))
        self.assertEqual(args[-1], image)
        self.assertIn("DAGSTER_HOME=/dagster-home", args)
        self.assertIn("isolated", args)

    def test_mutable_image_reference_is_rejected_before_docker(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/dagster_runtime.py"),
                "ghcr.io/example/app:latest",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("immutable GHCR digest", result.stderr)

    def test_stopped_code_server_has_image_startup_diagnostic(self) -> None:
        stopped = subprocess.CompletedProcess(["docker"], 0, "false\n", "")
        with patch.object(dagster_runtime, "command", return_value=stopped):
            with self.assertRaisesRegex(dagster_runtime.PhaseError, "image startup"):
                dagster_runtime.wait_until(
                    "gRPC readiness", ["probe"], 1, startup_container="code"
                )

    def test_workflow_gates_promotion_on_runtime_validation(self) -> None:
        workflow = (ROOT / ".github/workflows/container-release.yml").read_text()
        self.assertLess(
            workflow.index("Build Dagster candidate once"),
            workflow.index("Validate exact Dagster candidate digest"),
        )
        self.assertLess(
            workflow.index("Validate exact Dagster candidate digest"),
            workflow.index("Promote validated digest to release tags"),
        )
        self.assertIn("steps.candidate.outputs.digest", workflow)
        self.assertIn('test "$actual_digest" = "$EXPECTED_DIGEST"', workflow)

    def test_runtime_checks_run_and_event_rows(self) -> None:
        source = (ROOT / "scripts/dagster_runtime.py").read_text()
        self.assertIn("dagster job launch --grpc-host dagster-code", source)
        self.assertIn("--run-id {run_id}", source)
        self.assertIn("from runs where run_id", source)
        self.assertIn("from event_logs where run_id", source)
        for phase in (
            "image startup",
            "gRPC readiness",
            "execution",
            "event-log persistence",
        ):
            self.assertIn(phase, source)


if __name__ == "__main__":
    unittest.main()
