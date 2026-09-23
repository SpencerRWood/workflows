"""Mirror a reusable PR validation job into one commit-status context."""

from __future__ import annotations

import os
import re
import subprocess
import sys

SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
CONTEXT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}\Z")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
FINAL_STATES = {
    "success": ("success", "Centralized validation passed"),
    "failure": ("failure", "Centralized validation failed"),
    "cancelled": ("error", "Centralized validation was cancelled"),
}


def status_for_phase(phase: str, job_status: str = "") -> tuple[str, str]:
    if phase == "pending":
        return "pending", "Centralized validation is running"
    if phase != "final":
        raise ValueError(f"invalid validation status phase: {phase!r}")
    return FINAL_STATES.get(
        job_status, ("error", f"Centralized validation ended with {job_status or 'unknown'}")
    )


def status_request(phase: str, environment: dict[str, str]) -> list[str]:
    if environment.get("GITHUB_EVENT_NAME") != "pull_request":
        raise ValueError("commit status publishing requires a pull_request event")
    sha = environment.get("PR_HEAD_SHA", "")
    if not SHA_PATTERN.fullmatch(sha):
        raise ValueError("pull request head SHA is missing or invalid")
    context = environment.get("STATUS_CONTEXT", "")
    if not CONTEXT_PATTERN.fullmatch(context):
        raise ValueError("commit status context is missing or invalid")
    repository = environment.get("GITHUB_REPOSITORY", "")
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("GitHub repository is missing or invalid")
    run_id = environment.get("GITHUB_RUN_ID", "")
    if not run_id.isdecimal():
        raise ValueError("GitHub run ID is missing or invalid")
    server = environment.get("GITHUB_SERVER_URL", "")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+", server):
        raise ValueError("GitHub server URL is missing or invalid")
    state, description = status_for_phase(
        phase, environment.get("VALIDATION_JOB_STATUS", "")
    )
    return [
        "gh",
        "api",
        "-X",
        "POST",
        f"repos/{repository}/statuses/{sha}",
        "-f",
        f"state={state}",
        "-f",
        f"context={context}",
        "-f",
        f"description={description}",
        "-f",
        f"target_url={server}/{repository}/actions/runs/{run_id}",
    ]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: validation_status.py pending|final")
    command = status_request(sys.argv[1], dict(os.environ))
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    print(f"Published {command[6].split('=', 1)[1]} validation status for {os.environ['PR_HEAD_SHA']}")


if __name__ == "__main__":
    main()
