"""Skip a release whose validated checkout was superseded on the remote branch."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")


def is_current(ref: str, approved_revision: str | None = None) -> bool:
    """Compare the checked-out commit with the authoritative remote branch."""
    if ref != "refs/heads/main":
        raise ValueError(f"release publication requires refs/heads/main: {ref!r}")
    local = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if approved_revision is not None:
        if not SHA.fullmatch(approved_revision):
            raise ValueError("approved_revision must be a full commit SHA")
        if local != approved_revision:
            raise ValueError("checkout differs from the explicitly approved revision")
    remote_line = subprocess.check_output(
        ["git", "ls-remote", "--exit-code", "origin", ref], text=True
    ).strip()
    remote, _, remote_ref = remote_line.partition("\t")
    if not SHA.fullmatch(local) or not SHA.fullmatch(remote) or remote_ref != ref:
        raise ValueError("could not identify the release checkout and remote branch")
    if local != remote:
        print(f"Skipping superseded release: checkout {local}, {ref} {remote}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-current", action="store_true")
    args = parser.parse_args()
    try:
        required = os.environ.get("APPROVAL_REQUIRED", "false")
        if required not in {"true", "false"}:
            raise ValueError("APPROVAL_REQUIRED must be true or false")
        approved = os.environ.get("APPROVED_REVISION", "")
        if required == "true" and not approved:
            raise ValueError("approval-gated publication requires approved_revision")
        current = is_current(os.environ["RELEASE_REF"], approved or None)
        if args.require_current and not current:
            print("release head changed before publication completed", file=sys.stderr)
            return 1
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            stream.write(f"current={str(current).lower()}\n")
    except (KeyError, ValueError, subprocess.CalledProcessError) as error:
        print(f"release head check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
