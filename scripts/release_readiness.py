"""Run accumulated technical checks and nonpublishing semantic inspection."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib

SHA = re.compile(r"[0-9a-f]{40}\Z")
KIND = "wood-shared-release-readiness"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def command(argv: list[str], log: Path, *, cwd: Path | None = None, env: dict | None = None) -> str:
    result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=300)
    log.write_text(result.stdout + result.stderr)
    require(result.returncode == 0, f"required check failed: {log.name}")
    return result.stdout.strip()


def wood(args: list[str], log: Path, *, env: dict | None = None) -> dict:
    process = subprocess.run(["wood", *args, "--json"], env=env, text=True, capture_output=True, timeout=300)
    log.write_text(process.stdout + process.stderr)
    result = json.loads(process.stdout)
    # Wood owns these local records/logs. Retain them even on failure so the
    # ephemeral runner's diagnostic paths remain useful after the job ends.
    if args[:2] in (["repo", "validate"], ["repo", "verify"]):
        data = result.get("data", {})
        if data.get("log_dir"):
            shutil.copytree(data["log_dir"], log.parent / ("wood-" + args[1]))
    require(result.get("status") == "success", f"Wood check unavailable: {' '.join(args)}")
    return result["data"]


def predict(root: Path, candidate: str, evidence: Path) -> str:
    capability = tomllib.loads((root / ".github/release.toml").read_text())
    require(capability["release"]["semantic_release"] is True, "release capability required")
    directory = capability.get("project", {}).get("working_directory", ".")
    require(not Path(directory).is_absolute() and ".." not in Path(directory).parts, "unsafe project directory")
    with tempfile.TemporaryDirectory(prefix="release-impact-") as temporary:
        clone, config = Path(temporary) / "repository", Path(temporary) / "semantic.json"
        subprocess.run(["git", "clone", "--quiet", "--shared", str(root), str(clone)], check=True)
        git("checkout", "-B", "main", candidate, cwd=clone)
        git("remote", "set-url", "origin", git("remote", "get-url", "origin"), cwd=clone)
        command(["python3", str(Path(__file__).with_name("semantic_release_config.py")),
                 str(clone / directory / "pyproject.toml"), str(config)], evidence / "semantic-config.log")
        executable = Path(os.environ["SEMANTIC_RELEASE_EXECUTABLE"]) if "SEMANTIC_RELEASE_EXECUTABLE" in os.environ else root / directory / ".venv/bin/semantic-release"
        require(executable.is_file(), "locked semantic-release executable unavailable")
        env = {k: v for k, v in os.environ.items()
               if k not in {"GH_TOKEN", "GITHUB_TOKEN", "GITHUB_OUTPUT", "OPENPROJECT_API_TOKEN"}}
        tag = command([str(executable), "--config", str(config), "version", "--print-tag"],
                      evidence / "semantic-impact.log", cwd=clone / directory, env=env)
        subprocess.run(["git", "check-ref-format", "refs/tags/" + tag], check=True)
        return tag


def collect(evidence: Path, integration: str, security: str) -> dict:
    evidence.mkdir(parents=True, exist_ok=False)
    root = Path(git("rev-parse", "--show-toplevel"))
    candidate, target = os.environ["CANDIDATE_REVISION"], os.environ["TARGET_REVISION"]
    require(bool(SHA.fullmatch(candidate)) and bool(SHA.fullmatch(target)), "full candidate and target SHAs required")
    require(git("rev-parse", "HEAD") == candidate, "checkout differs from candidate")
    require(git("rev-parse", "--is-shallow-repository") == "false", "complete history required")
    document = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["wood"]
    mapping = {key: document["branching"][key] for key in ("strategy", "integration_branch")}
    require(mapping["strategy"] == "integration" and isinstance(mapping["integration_branch"], str)
            and mapping["integration_branch"] not in {"", "main"}, "integration branch mapping required")
    contract = wood(["contract"], evidence / "wood-contract.log")
    require(contract["branching"]["version"] == 1
            and set(contract["branching"]["strategies"]) >= {"trunk", "release", "integration"}
            and set(contract["branching"]["branch_prefixes"]) >= {"feature", "feat", "fix"},
            "complete Wood branching contract version 1 required")
    incident, approval = os.environ.get("HOTFIX_INCIDENT", ""), os.environ.get("HOTFIX_APPROVAL", "")
    require(bool(incident) == bool(approval), "hotfix requires incident and approval")
    if incident:
        require(os.environ["CANDIDATE_BRANCH"].startswith(("fix/op-", "feat/op-", "feature/op-")), "hotfix Story branch required")
        subprocess.run(["git", "merge-base", "--is-ancestor", target, candidate], check=True)
        require(git("ls-remote", "origin", "refs/heads/main") == target + "\trefs/heads/main", "target main advanced")
    else:
        require(os.environ["CANDIDATE_BRANCH"] == mapping["integration_branch"], "candidate must be development")
        # Git ancestry is a technical gate, independent of Wood planning metadata.
        subprocess.run(["git", "check-ref-format", "refs/heads/" + mapping["integration_branch"]], check=True)
        require(git("ls-remote", "--exit-code", "origin", "refs/heads/main") == target + "\trefs/heads/main",
                "target main advanced")
        require(git("ls-remote", "--exit-code", "origin", "refs/heads/" + mapping["integration_branch"])
                == candidate + "\trefs/heads/" + mapping["integration_branch"], "candidate advanced")
        command(["git", "merge-base", "--is-ancestor", target, candidate], evidence / "branch-readiness.log")
    env = {k: v for k, v in os.environ.items() if k not in {"GITHUB_TOKEN", "GH_TOKEN", "OPENPROJECT_API_TOKEN"}}
    capability = tomllib.loads((root / ".github/release.toml").read_text())
    with tempfile.TemporaryDirectory(prefix="release-check-profile-") as temporary:
        if "dbt-parse" in capability["validation"]["checks"]:
            require(all(env.get(key) for key in ("DBT_HOST", "DBT_USER", "DBT_PASSWORD")),
                    "required dbt validation credentials missing")
            directory = capability.get("project", {}).get("working_directory", ".")
            example = Path(capability.get("dbt", {}).get("profiles_example", "profiles.yml.example"))
            require(not example.is_absolute() and ".." not in example.parts, "unsafe dbt profile path")
            shutil.copyfile(root / directory / example, Path(temporary) / "profiles.yml")
            env["DBT_PROFILES_DIR"] = temporary
        validation = wood(["repo", "validate"], evidence / "validation.log", env=env)
        require(validation["passed"] is True and bool(validation["checks"]), "accumulated validation did not pass")
        require(bool(integration.strip()) and bool(security.strip()), "integration and security checks mandatory")
        command(["bash", "-euo", "pipefail", "-c", integration], evidence / "integration.log", env=env)
        command(["bash", "-euo", "pipefail", "-c", security], evidence / "security.log", env=env)
    tag = predict(root, candidate, evidence)
    require(git("rev-parse", "HEAD") == candidate, "checks changed candidate")
    subprocess.run(["git", "diff", "--exit-code"], check=True)
    subprocess.run(["git", "diff", "--cached", "--exit-code"], check=True)
    require(not git("status", "--porcelain"), "checks introduced untracked or modified candidate contents")
    result = {"kind": KIND, "version": 2, "repository": os.environ["GITHUB_REPOSITORY"],
              "candidate_revision": candidate, "target_revision": target, "branching": mapping,
              "next_tag": tag, "passed": True, "mode": "hotfix" if incident else "release",
              "incident": incident, "hotfix_approval": approval, "workflow_revision": os.environ["WORKFLOW_REVISION"],
              "observed_at": datetime.now(timezone.utc).isoformat(),
              "run_id": os.environ["GITHUB_RUN_ID"], "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
              "checks": {name: {"state": "passed", "log_sha256": hashlib.sha256((evidence / filename).read_bytes()).hexdigest()}
                         for name, filename in {"validation": "validation.log",
                                                "integration": "integration.log", "security": "security.log",
                                                "semantic-impact": "semantic-impact.log"}.items()}}
    result["files"] = {str(path.relative_to(evidence)): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in evidence.rglob("*") if path.is_file()}
    (evidence / "readiness.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--integration-command", required=True)
    parser.add_argument("--security-command", required=True)
    args = parser.parse_args()
    try:
        collect(args.evidence, args.integration_command, args.security_command)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"release readiness failed: {error}; retain check logs")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
