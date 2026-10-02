#!/usr/bin/env python3
"""Run repository-owned candidate checks against disposable PostgreSQL."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


class RuntimeFailure(RuntimeError):
    """A bounded runtime phase failed."""


def validate(image: str, check_module: str, timeout: int) -> None:
    token = uuid.uuid4().hex[:12]
    network = f"application-ci-{token}"
    postgres = f"application-postgres-{token}"
    candidate = f"application-check-{token}"
    password = secrets.token_hex(24)
    deadline = time.monotonic() + timeout
    phase = "initialization"

    def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeFailure("runtime deadline exceeded")
        result = subprocess.run(args, capture_output=True, text=True, timeout=remaining)
        if check and result.returncode:
            raise RuntimeFailure((result.stdout + result.stderr)[-4000:])
        return result

    with tempfile.TemporaryDirectory(prefix="application-runtime-") as temporary:
        environment = Path(temporary) / "runtime.env"
        environment.write_text(
            f"RUNTIME_DATABASE_URL=postgresql+psycopg://runtime:{password}@postgres/runtime\n",
            encoding="utf-8",
        )
        environment.chmod(0o600)
        try:
            command("docker", "network", "create", "--internal", network)
            phase = "PostgreSQL startup"
            command("docker", "run", "-d", "--name", postgres, "--network", network,
                    "--network-alias", "postgres", "-e", "POSTGRES_USER=runtime",
                    "-e", f"POSTGRES_PASSWORD={password}", "-e", "POSTGRES_DB=runtime",
                    "postgres:16-alpine")
            while command("docker", "exec", postgres, "pg_isready", "-h", "127.0.0.1", "-U", "runtime",
                          "-d", "runtime", check=False).returncode:
                if time.monotonic() >= deadline:
                    raise RuntimeFailure("PostgreSQL readiness timed out")
                time.sleep(1)
            phase = "candidate repository checks"
            command("docker", "run", "--name", candidate, "--network", network,
                    "--env-file", str(environment), "--entrypoint", "python", image,
                    "-m", check_module)
        except (RuntimeFailure, subprocess.TimeoutExpired) as error:
            diagnostic = str(error).replace(password, "[REDACTED]")
            # Full candidate logs are retained in CI; never include ephemeral secrets.
            try:
                logs = subprocess.run(["docker", "logs", candidate], capture_output=True,
                                      text=True, timeout=10)
                diagnostic += "\n" + (logs.stdout + logs.stderr).replace(password, "[REDACTED]")[-8000:]
            except subprocess.TimeoutExpired:
                pass
            raise RuntimeFailure(f"{phase}: {diagnostic}") from None
        finally:
            for name in (candidate, postgres):
                try:
                    subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                                   text=True, timeout=10, check=False)
                except subprocess.TimeoutExpired:
                    pass
            try:
                subprocess.run(["docker", "network", "rm", network], capture_output=True,
                               text=True, timeout=10, check=False)
            except subprocess.TimeoutExpired:
                pass
    print(json.dumps({"state": "passed", "image": image, "check_module": check_module}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Immutable GHCR digest-qualified candidate")
    parser.add_argument("--check-module", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--allow-local-image", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.allow_local_image and not re.fullmatch(
        r"ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}", args.image
    ):
        parser.error("image must be an immutable GHCR digest reference")
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", args.check_module):
        parser.error("check-module must be a dotted Python module name")
    if not 10 <= args.timeout <= 300:
        parser.error("timeout must be from 10 to 300 seconds")
    try:
        validate(args.image, args.check_module, args.timeout)
    except RuntimeFailure as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
