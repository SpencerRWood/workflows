#!/usr/bin/env python3
"""Exercise a candidate Dagster image against isolated PostgreSQL storage."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

POSTGRES_IMAGE = "postgres:16-alpine"
DB_USER = "dagster_ci"
DB_PASSWORD = "dagster_ci_only"
DB_NAME = "dagster_ci"


class PhaseError(RuntimeError):
    """A failed runtime boundary with its diagnostic phase."""


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check)


def instance_yaml() -> str:
    """Use Dagster's standard combined PostgreSQL run/event/schedule storage."""
    return f"""storage:
  postgres:
    postgres_db:
      hostname: postgres
      username: {DB_USER}
      password: {DB_PASSWORD}
      db_name: {DB_NAME}
      port: 5432
run_coordinator:
  module: dagster._core.run_coordinator.default_run_coordinator
  class: DefaultRunCoordinator
telemetry:
  enabled: false
"""


def client_args(image: str, network: str, home: Path) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "--mount",
        f"type=bind,src={home},dst=/dagster-home",
        "-e",
        "DAGSTER_HOME=/dagster-home",
        "--entrypoint",
        "dagster",
        image,
    ]


def run_status(image: str, network: str, home: Path, run_id: str) -> str:
    """Read the authoritative status from PostgreSQL-backed Dagster run storage."""
    script = (
        "from dagster import DagsterInstance; "
        f"run = DagsterInstance.get().get_run_by_id('{run_id}'); "
        "print(run.status.value if run else 'MISSING')"
    )
    args = client_args(image, network, home)
    args[-3:] = ["--entrypoint", "python", image]
    result = command(*args, "-c", script, check=False)
    if result.returncode:
        raise PhaseError(
            f"Dagster instance/PostgreSQL initialization: {result.stderr[-2000:]}"
        )
    return result.stdout.strip()


def wait_until(
    phase: str, probe: list[str], timeout: int, *, startup_container: str | None = None
) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        if startup_container:
            alive = command(
                "docker",
                "inspect",
                "--format",
                "{{.State.Running}}",
                startup_container,
                check=False,
            )
            if alive.stdout.strip() != "true":
                raise PhaseError("image startup: code-server container exited")
        result = command(*probe, check=False)
        if result.returncode == 0:
            return
        last = (result.stdout + result.stderr)[-2000:]
        time.sleep(2)
    raise PhaseError(f"{phase}: readiness timed out. {last}")


def validate(image: str, smoke_job: str, grpc_port: int, timeout: int) -> None:
    token = uuid.uuid4().hex[:12]
    network = f"dagster-ci-{token}"
    postgres = f"dagster-postgres-{token}"
    server = f"dagster-code-{token}"
    run_id = str(uuid.uuid4())
    phase = "PostgreSQL initialization"
    with tempfile.TemporaryDirectory(prefix="dagster-runtime-") as temporary:
        home = Path(temporary)
        os.chmod(home, 0o777)
        (home / "dagster.yaml").write_text(instance_yaml(), encoding="utf-8")
        try:
            command("docker", "network", "create", network)
            command(
                "docker",
                "run",
                "-d",
                "--name",
                postgres,
                "--network",
                network,
                "--network-alias",
                "postgres",
                "-e",
                f"POSTGRES_USER={DB_USER}",
                "-e",
                f"POSTGRES_PASSWORD={DB_PASSWORD}",
                "-e",
                f"POSTGRES_DB={DB_NAME}",
                POSTGRES_IMAGE,
            )
            wait_until(
                phase,
                [
                    "docker",
                    "exec",
                    postgres,
                    "pg_isready",
                    "-U",
                    DB_USER,
                    "-d",
                    DB_NAME,
                ],
                timeout,
            )
            phase = "image startup"
            started = command(
                "docker",
                "run",
                "-d",
                "--name",
                server,
                "--network",
                network,
                "--network-alias",
                "dagster-code",
                "--mount",
                f"type=bind,src={home},dst=/dagster-home",
                "-e",
                "DAGSTER_HOME=/dagster-home",
                "-e",
                f"DAGSTER_GRPC_PORT={grpc_port}",
                image,
                check=False,
            )
            if started.returncode:
                raise PhaseError(f"{phase}: {started.stderr.strip()}")
            phase = "gRPC readiness"
            wait_until(
                phase,
                [
                    *client_args(image, network, home),
                    "api",
                    "grpc-health-check",
                    "-h",
                    "dagster-code",
                    "-p",
                    str(grpc_port),
                ],
                timeout,
                startup_container=server,
            )
            phase = "Dagster instance/PostgreSQL initialization"
            check = command(
                *client_args(image, network, home), "instance", "info", check=False
            )
            if check.returncode:
                raise PhaseError(f"{phase}: {(check.stdout + check.stderr)[-2000:]}")
            phase = "run launch"
            # Keep PID 1 alive so DefaultRunLauncher's child process can finish.
            launch_command = (
                "dagster job launch --grpc-host dagster-code "
                f"--grpc-port {grpc_port} -j {shlex.quote(smoke_job)} "
                f"--run-id {run_id} && sleep {timeout}"
            )
            launched = command(
                "docker",
                "run",
                "-d",
                "--name",
                f"dagster-launch-{token}",
                "--network",
                network,
                "--mount",
                f"type=bind,src={home},dst=/dagster-home",
                "-e",
                "DAGSTER_HOME=/dagster-home",
                "--entrypoint",
                "sh",
                image,
                "-c",
                launch_command,
                check=False,
            )
            if launched.returncode:
                raise PhaseError(f"{phase}: {launched.stderr.strip()}")
            phase = "execution"
            deadline = time.monotonic() + timeout
            last_status = ""
            while time.monotonic() < deadline:
                alive = command(
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Running}}",
                    f"dagster-launch-{token}",
                    check=False,
                )
                if alive.stdout.strip() != "true":
                    phase = "run launch"
                    raise PhaseError("launcher container exited before run completion")
                last_status = run_status(image, network, home, run_id)
                if last_status == "SUCCESS":
                    break
                if last_status in {"FAILURE", "CANCELED"}:
                    raise PhaseError(f"{phase}: run reached {last_status}")
                time.sleep(2)
            else:
                raise PhaseError(
                    f"{phase}: run {run_id} did not reach SUCCESS. "
                    f"Last status: {last_status}"
                )
            phase = "PostgreSQL run persistence"
            run_count = command(
                "docker",
                "exec",
                postgres,
                "psql",
                "-U",
                DB_USER,
                "-d",
                DB_NAME,
                "-tAc",
                f"select count(*) from runs where run_id = '{run_id}'",
            ).stdout.strip()
            if run_count != "1":
                raise PhaseError(f"{phase}: expected one run row, got {run_count!r}")
            phase = "event-log persistence"
            event_count = command(
                "docker",
                "exec",
                postgres,
                "psql",
                "-U",
                DB_USER,
                "-d",
                DB_NAME,
                "-tAc",
                f"select count(*) from event_logs where run_id = '{run_id}'",
            ).stdout.strip()
            if not event_count.isdigit() or int(event_count) == 0:
                raise PhaseError(f"{phase}: no PostgreSQL event rows for {run_id}")
            print(
                f"Dagster runtime PASS: image={image} run={run_id} events={event_count}"
            )
        except (PhaseError, subprocess.CalledProcessError) as error:
            if isinstance(error, PhaseError) and str(error).startswith(
                "image startup:"
            ):
                phase = "image startup"
            detail = (
                f"{error}: {(error.stdout or '')[-2000:]} "
                f"{(error.stderr or '')[-2000:]}"
                if isinstance(error, subprocess.CalledProcessError)
                else str(error)
            )
            print(f"Dagster runtime FAIL [{phase}]: {detail}", file=sys.stderr)
            for container in (server, f"dagster-launch-{token}"):
                logs = command(
                    "docker", "logs", "--tail", "100", container, check=False
                )
                if logs.returncode == 0:
                    print(
                        f"{container} logs:\n{(logs.stdout + logs.stderr)[-12000:]}",
                        file=sys.stderr,
                    )
            raise SystemExit(1) from error
        finally:
            for container in (f"dagster-launch-{token}", server, postgres):
                command("docker", "rm", "-f", container, check=False)
            command("docker", "network", "rm", network, check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Immutable digest-qualified candidate image")
    parser.add_argument("--smoke-job", default="runtime_smoke_job")
    parser.add_argument("--grpc-port", type=int, default=4000)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--allow-local-tag", action="store_true", help=argparse.SUPPRESS
    )
    arguments = parser.parse_args()
    if not arguments.allow_local_tag and not re.fullmatch(
        r"ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}", arguments.image
    ):
        parser.error("image must be an immutable GHCR digest reference")
    validate(
        arguments.image, arguments.smoke_job, arguments.grpc_port, arguments.timeout
    )
