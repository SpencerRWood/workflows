"""Fail-closed registry discovery/promotion; recovery never receives implicit approval."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from release_head import is_current


def inspect_digest(image: str) -> str | None:
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", image, "--raw"],
        capture_output=True,
    )
    if result.returncode:
        message = result.stderr.decode(errors="replace").lower()
        if (
            "manifest unknown" in message
            or "manifest_unknown" in message
            or (image.lower() + ": not found") in message
        ):
            return None
        raise ValueError("registry lookup unavailable; absence was not established")
    document = json.loads(result.stdout)
    if document.get("schemaVersion") != 2:
        raise ValueError("invalid registry manifest")
    return "sha256:" + hashlib.sha256(result.stdout).hexdigest()


def require_current() -> None:
    approved = os.environ.get("APPROVED_REVISION") or None
    if os.environ.get("APPROVAL_REQUIRED") == "true" and approved is None:
        raise ValueError("approved_revision required")
    if not is_current(os.environ["RELEASE_REF"], approved):
        raise ValueError("main advanced; mutation forbidden")


def require_recovery(approved: bool) -> None:
    if not approved:
        raise ValueError(
            "partial/existing publication requires explicit recovery_approved authorization"
        )


def promote(
    source: str, tags: list[str], digest: str, recovery: bool, evidence: Path
) -> None:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not source.endswith(
        "@" + digest
    ):
        raise ValueError("promotion requires exact verified digest")
    observations = {tag: inspect_digest(tag) for tag in tags}
    (evidence / "promotion-before.json").write_text(
        json.dumps(observations, sort_keys=True)
    )
    if any(value is not None and value != digest for value in observations.values()):
        raise ValueError("conflicting release digest; never overwrite tags")
    if any(value is not None for value in observations.values()):
        require_recovery(recovery)
    for tag, observed in observations.items():
        if observed is None:
            require_current()
            # Recheck immediately before writing; publication requires a sole
            # registry writer (see runbook), because GHCR tags have no CAS API.
            if inspect_digest(tag) is not None:
                raise ValueError("tag appeared concurrently; preserve it and stop")
            subprocess.run(
                ["docker", "buildx", "imagetools", "create", "--tag", tag, source],
                check=True,
            )
        actual = inspect_digest(tag)
        with (evidence / "promotion.jsonl").open("a") as stream:
            stream.write(json.dumps({"tag": tag, "digest": actual}) + "\n")
        if actual != digest:
            raise ValueError("promoted digest differs; preserve partial state")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["preflight", "find", "promote"])
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    recovery = os.environ.get("RECOVERY_APPROVED", "false") == "true"
    try:
        require_current()
        if args.phase == "preflight":
            if int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")) > 1:
                require_recovery(recovery)
        elif args.phase == "find":
            digest = inspect_digest(os.environ["CANDIDATE_IMAGE"])
            (args.evidence / "candidate-discovery.json").write_text(
                json.dumps({"image": os.environ["CANDIDATE_IMAGE"], "digest": digest})
            )
            if digest:
                require_recovery(recovery)
                with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
                    stream.write("digest=" + digest + "\n")
        else:
            promote(
                os.environ["SOURCE_IMAGE"],
                [os.environ["VERSION_IMAGE"], os.environ["SHA_IMAGE"]],
                os.environ["EXPECTED_DIGEST"],
                recovery,
                args.evidence,
            )
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(
            f"publication stopped: {error}; retain evidence and obtain explicit recovery authorization",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
