"""Update one top-level dev image pin while preserving every other byte."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
IMAGE = re.compile(r"ghcr\.io/[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*\Z")
KEY = re.compile(r"[a-z][a-z0-9_]*_image_ref\Z")


def version_parts(value: str) -> tuple[int, int, int]:
    match = VERSION.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid stable release version: {value!r}")
    return tuple(int(part) for part in match.groups())


def validate_artifact(
    *,
    owner: str,
    image_name: str,
    version: str,
    image_repository: str,
    version_image: str,
    image_digest: str,
    version_image_digest: str,
) -> str:
    version_parts(version)
    if not DIGEST.fullmatch(image_digest):
        raise ValueError("invalid sha256 image digest")
    expected_repository = f"ghcr.io/{owner.lower()}/{image_name.lower()}"
    if (
        not IMAGE.fullmatch(expected_repository)
        or image_repository != expected_repository
    ):
        raise ValueError("unexpected image repository")
    expected_image = f"{expected_repository}:{version}"
    expected_reference = f"{expected_image}@{image_digest}"
    if version_image != expected_image or version_image_digest != expected_reference:
        raise ValueError("container publisher outputs disagree with release artifact")
    return expected_reference


def pin_line(content: str, key: str) -> tuple[int, int, str]:
    """Find exactly one unindented scalar key and return its span and value."""
    if not KEY.fullmatch(key):
        raise ValueError("invalid dev image key")
    matches = list(
        re.finditer(
            rf"^{re.escape(key)}:[ \t]*([^\r\n#]+?)[ \t]*$", content, re.MULTILINE
        )
    )
    occurrences = list(re.finditer(rf"^{re.escape(key)}:", content, re.MULTILINE))
    if len(occurrences) != 1 or len(matches) != 1:
        raise ValueError(f"expected exactly one {key}; found {len(occurrences)}")
    match = matches[0]
    return match.start(), match.end(), match.group(1).strip()


def check_promotion_order(
    content: str, key: str, repository: str, version: str, reference: str
) -> bool:
    _, _, current = pin_line(content, key)
    pattern = re.compile(
        rf"{re.escape(repository)}:(v[0-9]+\.[0-9]+\.[0-9]+)@sha256:[0-9a-f]{{64}}\Z"
    )
    match = pattern.fullmatch(current)
    if match is None:
        raise ValueError(
            "current dev image pin is not a digest-qualified image from the expected repository"
        )
    current_version = match.group(1)
    if version_parts(current_version) > version_parts(version):
        raise ValueError(
            f"dev already has newer application {current_version}; refusing {version}"
        )
    if current_version == version:
        if current != reference:
            raise ValueError(f"dev already pins {version} to a different artifact")
        return True
    return False


def update_pin(
    path: Path, key: str, repository: str, version: str, reference: str
) -> bool:
    """Return True when exactly one configured line was replaced."""
    content = path.read_text(encoding="utf-8")
    if check_promotion_order(content, key, repository, version, reference):
        return False
    start, end, _ = pin_line(content, key)
    updated = content[:start] + f"{key}: {reference}" + content[end:]
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--key", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--image-name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--image-repository", required=True)
    parser.add_argument("--version-image", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--version-image-digest", required=True)
    args = parser.parse_args()
    reference = validate_artifact(
        owner=args.owner,
        image_name=args.image_name,
        version=args.version,
        image_repository=args.image_repository,
        version_image=args.version_image,
        image_digest=args.image_digest,
        version_image_digest=args.version_image_digest,
    )
    changed = update_pin(
        args.path, args.key, args.image_repository, args.version, reference
    )
    print(f"changed={'true' if changed else 'false'}")


if __name__ == "__main__":
    main()
