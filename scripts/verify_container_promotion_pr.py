"""Create and merge only a validated, exact infrastructure dev image promotion."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from base64 import b64decode
from dataclasses import dataclass
from pathlib import Path

from update_environment_image_pin import (
    check_promotion_order,
    update_pin,
    validate_artifact,
)

POLL_SECONDS = 15
TIMEOUT_SECONDS = 20 * 60
SHA = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
BRANCH = re.compile(r"[A-Za-z0-9._/-]+\Z")


def command(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(
        args, cwd=cwd, text=True, capture_output=True, check=True
    ).stdout.strip()


def api(path: str) -> dict | list[dict]:
    return json.loads(command("gh", "api", path))


@dataclass(frozen=True)
class Config:
    repository: str
    base: str
    file: str
    key: str
    image_name: str
    version: str
    image_repository: str
    reference: str
    branch: str
    title: str
    context: str

    @classmethod
    def from_environment(cls, env: dict[str, str]) -> Config:
        repository = env["INFRASTRUCTURE_REPOSITORY"]
        base = env["INFRASTRUCTURE_BASE_BRANCH"]
        file = env["ENVIRONMENT_FILE"]
        image_name = env["IMAGE_NAME"]
        version = env["RELEASE_TAG"]
        context = env["STATUS_CONTEXT"]
        if not REPOSITORY.fullmatch(repository) or not BRANCH.fullmatch(base):
            raise ValueError("invalid infrastructure repository or base branch")
        if file != "environments/dev.yml":
            raise ValueError("dev promotion supports only environments/dev.yml")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", image_name):
            raise ValueError("invalid image name")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", context):
            raise ValueError("invalid status context")
        reference = validate_artifact(
            owner=env["APPLICATION_REPOSITORY"].split("/", 1)[0],
            image_name=image_name,
            version=version,
            image_repository=env["IMAGE_REPOSITORY"],
            version_image=env["VERSION_IMAGE"],
            image_digest=env["IMAGE_DIGEST"],
            version_image_digest=env["VERSION_IMAGE_DIGEST"],
        )
        return cls(
            repository,
            base,
            file,
            env["IMAGE_KEY"],
            image_name,
            version,
            env["IMAGE_REPOSITORY"],
            reference,
            f"chore/{image_name}-{version}",
            f"chore(deps): update {image_name} to {version}",
            context,
        )


def validate_pull(pull: dict, files: list[dict], cfg: Config) -> str:
    head = pull.get("head") or {}
    base = pull.get("base") or {}
    if (
        pull.get("state") != "open"
        or pull.get("title") != cfg.title
        or base.get("ref") != cfg.base
        or (base.get("repo") or {}).get("full_name") != cfg.repository
        or head.get("ref") != cfg.branch
        or (head.get("repo") or {}).get("full_name") != cfg.repository
    ):
        raise ValueError("promotion PR base, head, title, or state changed")
    sha = head.get("sha", "")
    if not SHA.fullmatch(sha):
        raise ValueError("promotion PR has no valid head SHA")
    if pull.get("changed_files") != 1 or len(files) != 1:
        raise ValueError("promotion PR changes more than one file")
    file = files[0]
    if (
        file.get("filename") != cfg.file
        or file.get("status") != "modified"
        or file.get("additions") != 1
        or file.get("deletions") != 1
    ):
        raise ValueError(
            "promotion PR must replace exactly one line in the configured dev file"
        )
    patch = file.get("patch") or ""
    added = [
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    removed = [
        line[1:]
        for line in patch.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    if len(added) != 1 or len(removed) != 1:
        raise ValueError("promotion PR has an unexpected diff")
    if added[0] != f"{cfg.key}: {cfg.reference}":
        raise ValueError("promotion PR does not add the exact published image pin")
    if not re.fullmatch(
        rf"{re.escape(cfg.key)}: {re.escape(cfg.image_repository)}:v[0-9]+\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{{64}}",
        removed[0],
    ):
        raise ValueError("promotion PR removes an unexpected image pin")
    return sha


def validation_result(statuses: list[dict], cfg: Config, sha: str) -> str:
    matching = [
        status
        for status in statuses
        if status.get("context") == cfg.context
        and (status.get("creator") or {}).get("login") == "github-actions[bot]"
        and (status.get("url") or "").endswith(f"/statuses/{sha}")
        and (status.get("target_url") or "").startswith(
            f"https://github.com/{cfg.repository}/actions/runs/"
        )
    ]
    if not matching:
        return "pending"
    state = max(matching, key=lambda item: item.get("id", 0)).get("state")
    if state == "pending":
        return "pending"
    if state != "success":
        raise ValueError(f"infrastructure {cfg.context} finished with {state!r}")
    return "success"


def pull_and_files(number: int, cfg: Config) -> tuple[dict, list[dict]]:
    pull = api(f"repos/{cfg.repository}/pulls/{number}")
    files = api(f"repos/{cfg.repository}/pulls/{number}/files?per_page=100")
    after = api(f"repos/{cfg.repository}/pulls/{number}")
    if (
        not isinstance(pull, dict)
        or not isinstance(files, list)
        or not isinstance(after, dict)
    ):
        raise TypeError("invalid infrastructure PR response")
    if (pull.get("head") or {}).get("sha") != (after.get("head") or {}).get("sha"):
        raise ValueError("promotion PR head SHA changed while reading its diff")
    return pull, files


def main_pin(cfg: Config) -> str:
    response = api(f"repos/{cfg.repository}/contents/{cfg.file}?ref={cfg.base}")
    if not isinstance(response, dict) or response.get("encoding") != "base64":
        raise ValueError("could not read current infrastructure dev pin")
    return b64decode(response["content"]).decode("utf-8")


def status_on_head(cfg: Config, sha: str) -> str:
    try:
        statuses = api(f"repos/{cfg.repository}/commits/{sha}/statuses?per_page=100")
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "infrastructure token needs Commit statuses: read"
        ) from error
    if not isinstance(statuses, list):
        raise TypeError("invalid infrastructure commit statuses response")
    return validation_result(statuses, cfg, sha)


def release_is_published(cfg: Config, application_repository: str) -> None:
    if not REPOSITORY.fullmatch(application_repository):
        raise ValueError("invalid application repository")
    sha = command("git", "-C", "application", "rev-parse", "HEAD")
    tag_sha = command(
        "git", "-C", "application", "rev-parse", f"refs/tags/{cfg.version}^{{commit}}"
    )
    if sha != tag_sha:
        raise ValueError("application checkout does not match the release tag")
    release = api(f"repos/{application_repository}/releases/tags/{cfg.version}")
    if (
        not isinstance(release, dict)
        or release.get("tag_name") != cfg.version
        or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise ValueError("application release is missing, draft, or prerelease")


def find_open_pr(cfg: Config) -> int | None:
    owner = cfg.repository.split("/", 1)[0]
    pulls = api(
        f"repos/{cfg.repository}/pulls?state=open&head={owner}:{cfg.branch}&base={cfg.base}&per_page=100"
    )
    if not isinstance(pulls, list):
        raise TypeError("invalid infrastructure PR list")
    matches = [
        pull["number"]
        for pull in pulls
        if (pull.get("head") or {}).get("ref") == cfg.branch
    ]
    if len(matches) > 1:
        raise ValueError("duplicate promotion PRs exist")
    return matches[0] if matches else None


def find_previous_pr(cfg: Config) -> dict | None:
    owner = cfg.repository.split("/", 1)[0]
    pulls = api(
        f"repos/{cfg.repository}/pulls?state=all&head={owner}:{cfg.branch}&per_page=100"
    )
    if not isinstance(pulls, list):
        raise TypeError("invalid infrastructure PR history")
    return pulls[0] if pulls else None


def create_pr(cfg: Config) -> int:
    body = (
        f"Application release: {cfg.version}\nImmutable image: {cfg.reference}\n"
        f"Source release: https://github.com/{os.environ['APPLICATION_REPOSITORY']}/releases/tag/{cfg.version}\n\n"
        "Infrastructure validation and exact-diff verification must pass before automatic dev merge.\n"
    )
    body_file = Path(os.environ["RUNNER_TEMP"]) / "container-dev-promotion-pr.md"
    body_file.write_text(body, encoding="utf-8")
    command(
        "gh",
        "pr",
        "create",
        "--repo",
        cfg.repository,
        "--base",
        cfg.base,
        "--head",
        cfg.branch,
        "--title",
        cfg.title,
        "--body-file",
        str(body_file),
    )
    number = find_open_pr(cfg)
    if number is None:
        raise RuntimeError("created promotion PR cannot be found")
    print(f"Created infrastructure PR #{number}")
    return number


def prepare(cfg: Config) -> int | None:
    """Create one PR or reuse the exact existing PR; return None for a no-op."""
    path = Path("infrastructure") / cfg.file
    changed = update_pin(
        path, cfg.key, cfg.image_repository, cfg.version, cfg.reference
    )
    if not changed:
        print("Exact artifact already pinned on infrastructure main")
        return None
    branch_exists = subprocess.run(
        [
            "git",
            "-C",
            "infrastructure",
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            cfg.branch,
        ],
        capture_output=True,
        check=False,
    ).returncode
    if branch_exists == 0:
        number = find_open_pr(cfg)
        if number is not None:
            pull, files = pull_and_files(number, cfg)
            validate_pull(pull, files, cfg)
            print(f"Reusing infrastructure PR #{number}")
            return number
        command(
            "git", "-C", "infrastructure", "fetch", "origin", f"refs/heads/{cfg.branch}"
        )
        branch_file = subprocess.run(
            ["git", "-C", "infrastructure", "show", f"FETCH_HEAD:{cfg.file}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        if command(
            "git",
            "-C",
            "infrastructure",
            "diff",
            "--name-only",
            f"origin/{cfg.base}...FETCH_HEAD",
        ) != cfg.file or branch_file != path.read_text(encoding="utf-8"):
            raise ValueError(
                "existing version branch differs from the exact requested dev pin"
            )
        previous = find_previous_pr(cfg)
        if previous is not None:
            raise ValueError("version branch already has a closed PR; review manually")
        return create_pr(cfg)
    if branch_exists != 2:
        raise RuntimeError("could not inspect existing promotion branch")
    previous = find_previous_pr(cfg)
    if previous is not None:
        if previous.get("merged_at"):
            if check_promotion_order(
                main_pin(cfg), cfg.key, cfg.image_repository, cfg.version, cfg.reference
            ):
                print("Promotion PR already merged and exact artifact is on main")
                return None
            raise ValueError(
                "promotion PR was merged but infrastructure main has not caught up"
            )
        raise ValueError("version branch has a closed unmerged PR; review manually")
    command("git", "-C", "infrastructure", "switch", "-c", cfg.branch)
    command(
        "git",
        "-C",
        "infrastructure",
        "config",
        "user.name",
        "container dev promotion bot",
    )
    command(
        "git",
        "-C",
        "infrastructure",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    command("git", "-C", "infrastructure", "add", "--", cfg.file)
    command("git", "-C", "infrastructure", "diff", "--cached", "--check")
    if (
        command("git", "-C", "infrastructure", "diff", "--cached", "--name-only")
        != cfg.file
    ):
        raise ValueError("promotion stages files outside the dev image pin")
    command("git", "-C", "infrastructure", "commit", "-m", cfg.title)
    command(
        "git", "-C", "infrastructure", "push", "origin", f"HEAD:refs/heads/{cfg.branch}"
    )
    return create_pr(cfg)


def verify_and_merge(
    number: int, cfg: Config, *, timeout: int = TIMEOUT_SECONDS
) -> None:
    pull, files = pull_and_files(number, cfg)
    sha = validate_pull(pull, files, cfg)
    deadline = time.monotonic() + timeout
    while True:
        current, _ = pull_and_files(number, cfg)
        if (
            current.get("state") != "open"
            or (current.get("head") or {}).get("sha") != sha
        ):
            raise ValueError(
                "promotion PR head SHA changed while waiting for validation"
            )
        if status_on_head(cfg, sha) == "success":
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out waiting for {cfg.context} on {sha}")
        print(f"Infrastructure {cfg.context}: pending", flush=True)
        time.sleep(POLL_SECONDS)
    pull, files = pull_and_files(number, cfg)
    if validate_pull(pull, files, cfg) != sha:
        raise ValueError("promotion PR head SHA changed after validation")
    if status_on_head(cfg, sha) != "success":
        raise ValueError("infrastructure validation no longer succeeds")
    if check_promotion_order(
        main_pin(cfg), cfg.key, cfg.image_repository, cfg.version, cfg.reference
    ):
        print("Exact artifact already on infrastructure main")
        return
    command(
        "gh",
        "pr",
        "merge",
        str(number),
        "--repo",
        cfg.repository,
        "--squash",
        "--delete-branch",
        "--match-head-commit",
        sha,
        "--subject",
        cfg.title,
    )
    merged = api(f"repos/{cfg.repository}/pulls/{number}")
    if not isinstance(merged, dict) or merged.get("merged") is not True:
        raise RuntimeError("infrastructure promotion merge could not be confirmed")
    print(f"Merged infrastructure PR #{number}")


def main() -> int:
    try:
        cfg = Config.from_environment(dict(os.environ))
        operation = sys.argv[1]
        if operation == "artifact":
            release_is_published(cfg, os.environ["APPLICATION_REPOSITORY"])
        elif operation == "prepare":
            number = prepare(cfg)
            with Path(os.environ["GITHUB_OUTPUT"]).open(
                "a", encoding="utf-8"
            ) as output:
                output.write(f"changed={'true' if number is not None else 'false'}\n")
                if number is not None:
                    output.write(f"number={number}\n")
        elif operation == "merge":
            verify_and_merge(int(os.environ["PR_NUMBER"]), cfg)
        else:
            raise ValueError("expected artifact, prepare, or merge")
    except (
        KeyError,
        ValueError,
        TypeError,
        RuntimeError,
        TimeoutError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"container dev promotion failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
