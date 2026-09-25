"""Skip a release whose validated checkout was superseded on the remote branch."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


SHA = re.compile(r"[0-9a-f]{40}\Z")


def is_current(ref: str) -> bool:
    """Compare the checked-out commit with the authoritative remote branch."""
    if not ref.startswith("refs/heads/"):
        raise ValueError(f"release ref must be a branch: {ref!r}")
    local = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
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
    try:
        current = is_current(os.environ["RELEASE_REF"])
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            stream.write(f"current={str(current).lower()}\n")
    except (KeyError, ValueError, subprocess.CalledProcessError) as error:
        print(f"release head check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
