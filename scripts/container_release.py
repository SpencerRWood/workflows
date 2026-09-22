"""Validate a caller's published release before publishing its container."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


RELEASE_TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
IMAGE_NAME = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
PLATFORM = re.compile(r"linux/[a-z0-9_]+(?:/[a-z0-9_]+)?\Z")


def command(*args: str) -> str:
    """Run a required Git or GitHub command and return its output."""
    return subprocess.run(
        args, check=True, capture_output=True, text=True
    ).stdout.strip()


def local_path(root: Path, value: str, *, directory: bool) -> Path:
    """Resolve a repository-relative build path without leaving the checkout."""
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"invalid repository-relative build path: {value!r}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root) or not (
        resolved.is_dir() if directory else resolved.is_file()
    ):
        raise ValueError(
            f"build path does not exist within the release checkout: {value!r}"
        )
    return resolved


def prepare(environment: dict[str, str]) -> dict[str, str]:
    """Check the release and derive image references from its exact commit."""
    tag = environment.get("RELEASE_TAG", "")
    if not RELEASE_TAG.fullmatch(tag):
        raise ValueError(
            "release_tag must be a stable semantic version tag such as v1.2.3"
        )

    repository = environment["GITHUB_REPOSITORY"]
    owner, repository_name = repository.split("/", 1)
    image_name = (environment.get("IMAGE_NAME") or repository_name).lower()
    if not IMAGE_NAME.fullmatch(image_name):
        raise ValueError("image_name must be a single valid GHCR image name")

    platforms = environment.get("PLATFORMS", "")
    if not platforms or any(
        not PLATFORM.fullmatch(item.strip()) for item in platforms.split(",")
    ):
        raise ValueError("platforms must be comma-separated linux platform names")

    root = Path(environment["GITHUB_WORKSPACE"]).resolve()
    local_path(root, environment.get("BUILD_CONTEXT", "."), directory=True)
    local_path(root, environment.get("DOCKERFILE", "Dockerfile"), directory=False)

    sha = command("git", "-C", str(root), "rev-parse", "HEAD")
    tag_sha = command(
        "git", "-C", str(root), "rev-parse", f"refs/tags/{tag}^{{commit}}"
    )
    if sha != tag_sha:
        raise ValueError(f"checked-out commit {sha} does not match release tag {tag}")

    release = json.loads(
        command("gh", "api", f"repos/{repository}/releases/tags/{tag}")
    )
    if (
        release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise ValueError(f"{tag} must be a published, non-prerelease GitHub Release")

    image_repository = f"ghcr.io/{owner.lower()}/{image_name}"
    return {
        "release_tag": tag,
        "commit_sha": sha,
        "image_repository": image_repository,
        "version_image": f"{image_repository}:{tag}",
        "sha_image": f"{image_repository}:sha-{sha}",
    }


def main() -> int:
    try:
        outputs = prepare(dict(os.environ))
    except (KeyError, ValueError, subprocess.CalledProcessError) as error:
        print(f"container release validation failed: {error}", file=sys.stderr)
        return 1
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
