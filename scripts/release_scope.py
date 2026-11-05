"""Verify explicitly approved repository scope without a live planning service."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def repository_scope(root: Path, revision: str, filename: str, mapping: dict, context: dict) -> dict:
    """Bind scope approval and actual implementation ancestry to the candidate."""
    path = Path(filename)
    require(bool(filename) and not path.is_absolute() and ".." not in path.parts
            and path.as_posix() == filename, "repository scope path must be canonical and relative")
    source = root / path
    require(source.is_file() and not source.is_symlink()
            and source.resolve().is_relative_to(root.resolve()), "repository scope must be a regular repository file")
    require(bool(re.fullmatch(r"[a-f0-9]{40}", revision)), "full scope revision required")
    size = int(subprocess.check_output(["git", "cat-file", "-s", f"{revision}:{filename}"],
                                       cwd=root, timeout=30))
    require(size <= 65536, "repository scope exceeds bound")
    raw = subprocess.check_output(["git", "show", f"{revision}:{filename}"], cwd=root, timeout=30)
    require(len(raw) <= 65536 and source.read_bytes() == raw,
            "repository scope differs from the committed candidate or exceeds bound")
    manifest = json.loads(raw)
    fields = {"version", "project_id", "initiative_id", "release_id", "release_name",
              "scope_reference", "approval_reference", "stories"}
    require(isinstance(manifest, dict) and set(manifest) == fields
            and type(manifest["version"]) is int and manifest["version"] == 1,
            "invalid repository scope schema")
    for name, expected in (("project_id", context["project_id"]),
                           ("initiative_id", context["initiative_id"]),
                           ("release_id", mapping["release_id"])):
        require(type(manifest[name]) is int and manifest[name] == expected,
                f"repository scope {name} differs from approved mapping")
    require(manifest["scope_reference"] == mapping["scope_reference"], "repository scope reference differs")
    require(all(isinstance(manifest[name], str) and 0 < len(manifest[name].strip()) <= 2000
                for name in ("approval_reference", "release_name")), "explicit repository scope approval required")
    entries = manifest["stories"]
    require(isinstance(entries, list) and 0 < len(entries) <= 100, "repository scope needs bounded Stories")
    seen, stories, deferrals = set(), [], {}
    for entry in entries:
        require(isinstance(entry, dict) and set(entry) == {"id", "merge_commits", "defer_reason"},
                "invalid repository Story schema")
        identifier, commits, reason = entry["id"], entry["merge_commits"], entry["defer_reason"]
        require(type(identifier) is int and identifier > 0 and identifier not in seen,
                "repository Story IDs must be unique positive integers")
        seen.add(identifier)
        require(isinstance(reason, str) and len(reason) <= 2000
                and (reason == "" or bool(reason.strip())), "invalid repository deferral reason")
        require(isinstance(commits, list) and len(commits) <= 100 and (commits or reason),
                "implemented Story needs committed implementation evidence")
        require(all(isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{40}", commit) for commit in commits)
                and len(set(commits)) == len(commits), "implementation revisions must be unique full SHAs")
        for commit in commits:
            subprocess.run(["git", "merge-base", "--is-ancestor", commit, revision],
                           cwd=root, check=True, capture_output=True, timeout=30)
        if reason:
            deferrals[str(identifier)] = reason
        stories.append({"id": identifier, "status": "Deferred" if reason else "Integrated",
                        "merge_commits": commits})
    return {"release_id": manifest["release_id"], "release_name": manifest["release_name"],
            "project_id": manifest["project_id"], "initiative_id": manifest["initiative_id"],
            "scope_reference": manifest["scope_reference"], "stories": stories, "deferrals": deferrals,
            "source": {"type": "repository", "path": filename,
                       "sha256": hashlib.sha256(raw).hexdigest(),
                       "approval_reference": manifest["approval_reference"],
                       "limitation": "Repository-approved scope and Git ancestry; no live OpenProject status attested."}}
