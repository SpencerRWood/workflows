"""Validate an explicit Git authentication mode without emitting credentials."""

import json
import os
import sys


def validate(environment: dict[str, str]) -> str:
    mode = environment.get("RELEASE_GIT_AUTH", "")
    if mode not in {"github-token", "github-app"}:
        raise ValueError("release_git_auth must be github-token or github-app")
    if mode == "github-app" and not all(
        environment.get(name, "").strip()
        for name in (
            "RELEASE_APP_CLIENT_ID",
            "RELEASE_APP_SLUG",
            "RELEASE_APP_PRIVATE_KEY",
        )
    ):
        raise ValueError("github-app requires client ID, reviewed slug and private key")
    return mode


def main() -> int:
    try:
        mode = validate(dict(os.environ))
    except ValueError as error:
        sys.stdout.write(json.dumps({"status": "blocked", "reason": str(error)}) + "\n")
        return 1
    sys.stdout.write(json.dumps({"status": "configured", "git_auth": mode}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
