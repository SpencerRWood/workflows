"""Verify an approved release PR at the exact main revision, without write credentials."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib
import urllib.parse
from pathlib import Path

from release_readiness import KIND

SHA = re.compile(r"[0-9a-f]{40}\Z")


def api(path: str, *, pages: bool = False):
    command = ["gh", "api", path]
    if pages:
        command += ["--paginate", "--slurp"]
    return json.loads(subprocess.check_output(command, text=True))


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def before_merge(value: str | None, merged_at: str) -> bool:
    if not value:
        return False
    observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    merged = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
    require(observed.tzinfo is not None and merged.tzinfo is not None, "authority timestamps require timezones")
    return observed <= merged


def readiness_evidence(repository: str, check: dict, head: str, workflow: str,
                       mapping: dict, target: str, merged_at: str) -> dict:
    match = re.fullmatch(r"https://github\.com/" + re.escape(repository)
                         + r"/actions/runs/(\d+)/job/(\d+)", check.get("details_url", ""))
    require(match is not None, "readiness check lacks a workflow run authority")
    run_id = match.group(1)
    job = api(f"repos/{repository}/actions/jobs/{match.group(2)}")
    require(str(job["run_id"]) == run_id and job["head_sha"] == head
            and job["status"] == "completed" and job["conclusion"] == "success",
            "readiness job authority differs")
    require(job["check_run_url"] == f"https://api.github.com/repos/{repository}/check-runs/{check['id']}",
            "readiness check and job differ")
    attempt = job["run_attempt"]
    require(type(attempt) is int and attempt > 0, "invalid readiness attempt")
    run = api(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    require(run["head_sha"] == head and run["event"] == "pull_request"
            and run["status"] == "completed" and run["conclusion"] == "success",
            "readiness run is not successful at this candidate")
    require(any(w["sha"] == workflow and w["path"].startswith(
        "SpencerRWood/workflows/.github/workflows/release-readiness.yml@")
        for w in run.get("referenced_workflows", [])), "readiness did not use the pinned shared verifier")
    with tempfile.TemporaryDirectory(prefix="release-readiness-evidence-") as temporary:
        subprocess.run(["gh", "run", "download", run_id, "--repo", repository,
                        "--name", f"release-readiness-{head}-{run_id}-{attempt}", "--dir", temporary], check=True)
        root = Path(temporary)
        record = json.loads((root / "readiness.json").read_text())
        require(record.get("kind") == KIND and record.get("version") == 1
                and record.get("passed") is True, "invalid readiness record")
        require(record["repository"] == repository and record["candidate_revision"] == head
                and record["target_revision"] == target and record["branching"] == mapping
                and record["workflow_revision"] == workflow and str(record["run_id"]) == run_id
                and int(record["attempt"]) == run["run_attempt"]
                and before_merge(record["observed_at"], merged_at), "readiness binding differs")
        filenames = {"scope": "scope-release.log", "validation": "validation.log",
                     "integration": "integration.log", "security": "security.log",
                     "semantic-impact": "semantic-impact.log"}
        require(set(record["checks"]) == set(filenames), "mandatory readiness checks missing")
        require(set(record["files"]) == {str(p.relative_to(root)) for p in root.rglob("*")
                if p.is_file() and p != root / "readiness.json"}, "readiness records/logs are missing")
        for name, digest in record["files"].items():
            path = root / name
            require(not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
                    and hashlib.sha256(path.read_bytes()).hexdigest() == digest, "readiness record/log was tampered")
        for name, filename in filenames.items():
            require(record["checks"][name]["state"] == "passed"
                    and record["checks"][name]["log_sha256"] == hashlib.sha256((root / filename).read_bytes()).hexdigest(),
                    f"readiness check/log differs: {name}")
        require(record["scope"]["release_id"] == mapping["release_id"]
                and record["scope"]["scope_reference"] == mapping["scope_reference"], "readiness scope differs")
        require(bool(record["scope"]["stories"]) and all(s["status"] in {"Closed", "Rejected"}
                or str(s["id"]) in record["scope"]["deferrals"] for s in record["scope"]["stories"]),
                "unfinished release Stories")
        subprocess.run(["git", "check-ref-format", "refs/tags/" + record["next_tag"]], check=True)
        return record


def authorize(repository: str, revision: str, mapping: dict, readiness: str,
              incident: str = "", approval: str = "", *, workflow_revision: str = "",
              deployment_tag: str = "") -> dict:
    require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)),
            "invalid repository")
    require(bool(SHA.fullmatch(revision)), "revision must be a full SHA")
    require(mapping.get("strategy") == "integration", "integration adoption required")
    require(type(mapping.get("release_id")) is int and mapping["release_id"] > 0
            and bool(mapping.get("scope_reference")), "approved planning scope required")
    development = mapping.get("integration_branch")
    require(isinstance(development, str) and development not in {"", "main"},
            "development branch mapping required")
    require(bool(readiness), "release readiness check required")
    require(bool(SHA.fullmatch(workflow_revision)), "pinned verifier revision required")
    require(bool(incident) == bool(approval), "hotfix requires incident and approval references")
    require(git("rev-parse", "HEAD") == revision, "checkout differs from event revision")
    remote = git("ls-remote", "--exit-code", "origin", "refs/heads/main")
    if deployment_tag:
        subprocess.run(["git", "check-ref-format", "refs/tags/" + deployment_tag], check=True)
        require(bool(re.fullmatch(r"refs/heads/main", remote.split("\t")[-1]))
                and bool(SHA.fullmatch(remote.split("\t")[0])), "main authority unavailable")
        subprocess.run(["git", "merge-base", "--is-ancestor", revision, remote.split("\t")[0]], check=True)
        require(git("rev-parse", f"refs/tags/{deployment_tag}^{{commit}}") == revision, "deployment tag differs")
        published = api(f"repos/{repository}/releases/tags/{urllib.parse.quote(deployment_tag, safe='')}")
        require(published["tag_name"] == deployment_tag and published.get("published_at")
                and published["draft"] is False and published["prerelease"] is False,
                "deployment requires a completed published release")
    else:
        require(remote == revision + "\trefs/heads/main", "main advanced or is unavailable")

    # GitHub's effective branch rules are read with contents:read. Fail closed
    # when protection is absent, unreadable, or does not require PR review/checks.
    rules = [rule for page in api(f"repos/{repository}/rules/branches/main?per_page=100", pages=True)
             for rule in page]
    pr_rules = [r["parameters"] for r in rules if r["type"] == "pull_request"]
    check_rules = [r["parameters"] for r in rules if r["type"] == "required_status_checks"]
    if not rules:
        protection = api(f"repos/{repository}/branches/main/protection")
        reviews = protection.get("required_pull_request_reviews")
        checks = protection.get("required_status_checks")
        require(isinstance(reviews, dict) and isinstance(checks, dict), "protected main must require PRs and CI")
        pr_rules = [reviews]
        check_rules = [{"required_status_checks": [{"context": c["context"], "integration_id": c.get("app_id")}
                       for c in checks.get("checks", [])]}]
    require(bool(pr_rules) and bool(check_rules), "protected main must require PRs and CI")
    review_count = max(r.get("required_approving_review_count", 0) for r in pr_rules)
    contexts = {(c["context"], c.get("integration_id")) for r in check_rules
                for c in r["required_status_checks"]}
    require(any(name == readiness for name, _ in contexts), "release readiness must be a required main check")

    associated = api(f"repos/{repository}/commits/{revision}/pulls", pages=True)
    candidates = [p for page in associated for p in page
                  if p.get("merged_at") and p.get("merge_commit_sha") == revision
                  and p["base"]["ref"] == "main"
                  and p["base"]["repo"]["full_name"] == repository]
    require(len(candidates) == 1, "exact main revision must identify one merged release PR")
    pr = candidates[0]
    head = pr["head"]["sha"]
    require(bool(SHA.fullmatch(head)), "invalid candidate revision")
    require(pr["head"]["repo"]["full_name"] == repository, "fork release PR forbidden")
    if incident:
        require(pr["head"]["ref"].startswith(("fix/op-", "feat/op-", "feature/op-")),
                "hotfix must use a supported Story branch")
        require(f"Hotfix-Incident: {incident}" in (pr.get("body") or "")
                and f"Hotfix-Approval: {approval}" in (pr.get("body") or ""),
                "hotfix references must match the reviewed PR")
    else:
        require(pr["head"]["ref"] == development, "routine release must originate from development")
    parents = git("rev-list", "--parents", "-n", "1", revision).split()[1:]
    require(len(parents) in {1, 2} and (len(parents) == 1 or parents[1] == head),
            "release merge has unsupported ancestry")
    subprocess.run(["git", "diff", "--exit-code", head, revision], check=True)
    if not incident:
        subprocess.run(["git", "merge-base", "--is-ancestor", parents[0], head], check=True)

    reviews = api(f"repos/{repository}/pulls/{pr['number']}/reviews", pages=True)
    latest = {}
    for page in reviews:
        for review in page:
            if review["state"] != "COMMENTED":
                latest[review["user"]["login"]] = review
    valid_reviews = [r for r in latest.values() if r["state"] == "APPROVED"
                and r["commit_id"] == head and r["user"]["login"] != pr["user"]["login"]
                and r.get("author_association") in {"OWNER", "MEMBER", "COLLABORATOR"}
                and before_merge(r.get("submitted_at"), pr["merged_at"])]
    require(len(valid_reviews) >= review_count, "required reviews must bind the exact candidate")
    release_marker = f"Release-Approval: {head}"
    def approval_comments():
        comments = api(f"repos/{repository}/issues/{pr['number']}/comments", pages=True)
        return [c for page in comments for c in page
                    if c.get("author_association") in {"OWNER", "MEMBER", "COLLABORATOR"}
                    and c["user"].get("type") == "User" and before_merge(c.get("created_at"), pr["merged_at"])
                    and before_merge(c.get("updated_at"), pr["merged_at"])
                    and release_marker in (c.get("body") or "").splitlines()]
    # An approving review of this exact release PR is release approval. When
    # protection needs no independent reviewer, a trusted premerge comment also
    # supports a sole maintainer without weakening any required review rule.
    approved = valid_reviews or approval_comments()
    require(bool(approved), "explicit release approval must bind the exact candidate")
    require(not any(r["state"] == "CHANGES_REQUESTED" for r in latest.values()),
            "unresolved review changes")

    pages = api(f"repos/{repository}/commits/{head}/check-runs?filter=latest&per_page=100", pages=True)
    checks = [c for page in pages for c in page["check_runs"]]
    verified = []
    record = None
    for context, integration in sorted(contexts, key=lambda item: (item[0], str(item[1]))):
        matching = [c for c in checks if c["name"] == context
                    and c["head_sha"] == head
                    and (integration in {None, -1} or c["app"]["id"] == integration)]
        # Require Actions provenance for the release gate, rather than trusting
        # a caller boolean or an arbitrary legacy commit status of the same name.
        if context == readiness:
            matching = [c for c in matching if c["app"]["slug"] == "github-actions"]
        require(bool(matching) and all(c["status"] == "completed"
                                      and c["conclusion"] == "success"
                                      and before_merge(c.get("completed_at"), pr["merged_at"])
                                      for c in matching),
                f"required candidate check unavailable or unsuccessful: {context}")
        verified.extend({"id": c["id"], "name": c["name"], "url": c["html_url"]} for c in matching)
        if context == readiness:
            require(len(matching) == 1, "ambiguous readiness authority")
            record = readiness_evidence(repository, matching[0], head, workflow_revision, mapping,
                                        parents[0], pr["merged_at"])
    require(record is not None and record["incident"] == incident and record["hotfix_approval"] == approval,
            "readiness hotfix authority differs")
    require(not deployment_tag or deployment_tag == record["next_tag"], "deployment release identity differs from approved plan")
    deferred = record["scope"]["deferrals"]
    if deferred:
        marker = "Release-Deferrals: " + ",".join(sorted(deferred, key=int))
        require(any(marker in c.get("body", "").splitlines() for c in approval_comments()),
                "release deferrals require explicit premerge approval")
    return {"repository": repository, "revision": revision, "candidate_revision": head,
            "prior_main_revision": parents[0], "pr": pr["number"], "pr_url": pr["html_url"],
            "branching": mapping, "reviews": [r["html_url"] for r in approved],
            "checks": verified, "readiness": record, "release_tag": deployment_tag or record["next_tag"],
            "incident": incident or None, "hotfix_approval": approval or None,
            "run_id": os.environ.get("GITHUB_RUN_ID"), "attempt": os.environ.get("GITHUB_RUN_ATTEMPT")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        event = os.environ["GITHUB_EVENT_NAME"]
        require(os.environ["GITHUB_REF"] == "refs/heads/main" and (event == "push"
                or event == "workflow_dispatch" and os.environ.get("RECOVERY_APPROVED") == "true"),
                "release requires a main push or explicitly approved recovery")
        with Path("pyproject.toml").open("rb") as stream:
            mapping = tomllib.load(stream)["tool"]["wood"]["branching"]
        deployment_tag = os.environ.get("DEPLOYMENT_RELEASE", "")
        revision = git("rev-parse", "HEAD") if deployment_tag else os.environ["GITHUB_SHA"]
        result = authorize(os.environ["GITHUB_REPOSITORY"], revision, mapping,
                           os.environ["READINESS_CHECK"], os.environ.get("HOTFIX_INCIDENT", ""),
                           os.environ.get("HOTFIX_APPROVAL", ""),
                           workflow_revision=os.environ["WORKFLOW_REVISION"], deployment_tag=deployment_tag)
        args.evidence.write_text(json.dumps(result, indent=2) + "\n")
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            output.write(f"approved_revision={result['revision']}\n")
            output.write(f"release_tag={result['release_tag']}\n")
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"release authorization failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
