"""Local Git/PSR technical release checks without a planning authority."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_readiness as readiness
import release_authorization as authorization


class CandidateFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "checkout"
        self.remote = Path(self.temporary.name) / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("remote", "add", "origin", str(self.remote))
        (self.root / ".github").mkdir()
        (self.root / ".gitignore").write_text(".venv/\n__pycache__/\ndist/\n")
        (self.root / "app.py").write_text("result = 42\n")
        (self.root / "uv.lock").write_text('version=1\n[[package]]\nname="fixture"\nversion="0.1.0"\nsource={editable="."}\n')
        (self.root / "pyproject.toml").write_text(
            '[project]\nname="fixture"\nversion="0.1.0"\n'
            '[tool.semantic_release]\nversion_toml=["pyproject.toml:project.version"]\n'
            'allow_zero_version=true\n[tool.semantic_release.remote]\nurl="https://github.com/owner/fixture.git"\n'
            '[tool.wood.branching]\nstrategy="integration"\n'
            'integration_branch="development"\n')
        (self.root / ".github/release.toml").write_text(
            'version=1\n[python]\n[validation]\nchecks=["pytest"]\n[release]\nsemantic_release=true\n')
        (self.root / ".venv").symlink_to(Path(sys.executable).parent.parent, target_is_directory=True)
        self.git("add", ".")
        self.git("commit", "-qm", "chore: base")
        self.git("tag", "v0.1.0")
        self.base = self.git("rev-parse", "HEAD")
        self.git("push", "-q", "origin", "main", "--tags")
        self.git("checkout", "-qb", "development")
        self.git("commit", "-q", "--allow-empty", "-m", "feat: Story one")
        self.git("commit", "-q", "--allow-empty", "-m", "fix: Story two")
        self.head = self.git("rev-parse", "HEAD")
        self.git("push", "-q", "origin", "development")
        self.mapping = {"strategy": "integration", "integration_branch": "development"}
        self.env = {"CANDIDATE_REVISION": self.head, "TARGET_REVISION": self.base,
                    "CANDIDATE_BRANCH": "development", "GITHUB_REPOSITORY": "owner/fixture",
                    "WORKFLOW_REVISION": "d" * 40, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                    "OPENPROJECT_API_TOKEN": "fixture-only-secret", "GH_TOKEN": "fixture-only-read-token",
                    "GITHUB_TOKEN": "fixture-only-write-token"}
        self.evidence = Path(self.temporary.name) / "evidence"
        self.integration = 'test -z "${OPENPROJECT_API_TOKEN:-}${GH_TOKEN:-}${GITHUB_TOKEN:-}" && python3 -c "import app; assert app.result == 42"'
        self.security = "git diff --exit-code && git diff --cached --exit-code"

    def git(self, *args):
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.PIPE).strip()

    def wood(self, args, log, **kwargs):
        if args == ["contract"]:
            data = {"branching": {"version": 1, "strategies": ["trunk", "release", "integration"],
                                  "branch_prefixes": ["feature", "feat", "fix"]}}
        elif args == ["repo", "validate"]:
            check = subprocess.run([sys.executable, "-c", "import app; assert app.result == 42"],
                                   env=kwargs.get("env"), capture_output=True)
            if check.returncode:
                raise ValueError("accumulated validation failed")
            data = {"passed": True, "checks": [{"name": "application", "state": "passed"}]}
        else:
            raise AssertionError(args)
        log.write_text(json.dumps({"status": "success", "data": data}))
        return data

    def collect(self, **kwargs):
        with patch.dict(os.environ, self.env), patch.object(readiness, "wood", self.wood):
            return readiness.collect(self.evidence, kwargs.pop("integration", self.integration),
                                     kwargs.pop("security", self.security), **kwargs)


class ReleaseReadinessTests(CandidateFixture):
    def test_candidate_without_planning_metadata_runs_technical_checks_without_publication(self):
        record = self.collect()
        self.assertEqual(record["next_tag"], "v0.2.0")
        self.assertEqual(set(record["checks"]), {"validation", "integration", "security", "semantic-impact"})
        self.assertNotIn("scope", record)
        self.assertEqual(record["version"], 2)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        self.assertEqual(self.git("ls-remote", "origin", "refs/tags/v0.2.0"), "")
        for path in self.evidence.iterdir():
            self.assertNotIn("fixture-only-secret", path.read_text())

    def test_no_planning_token_or_file_is_needed_and_no_planning_command_is_called(self):
        self.env.pop("OPENPROJECT_API_TOKEN")
        self.collect()
        self.assertFalse((self.evidence / "scope-release.log").exists())

    def test_advanced_remote_candidate_fails_before_checks(self):
        self.git("commit", "-q", "--allow-empty", "-m", "fix: next candidate")
        self.git("push", "-q", "origin", "development")
        self.git("checkout", "-q", "--detach", self.head)
        with self.assertRaisesRegex(ValueError, "candidate advanced"):
            self.collect()

    def test_failed_security_blocks_record_before_release(self):
        with self.assertRaisesRegex(ValueError, "security.log"):
            self.collect(security="exit 42")
        self.assertFalse((self.evidence / "readiness.json").exists())

    def test_modified_candidate_is_rejected_after_repository_checks(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.collect(integration='printf "result = 43\\n" > app.py', security="true")
        self.assertFalse((self.evidence / "readiness.json").exists())

    def test_tag_at_merge_uses_supported_generator_interface_without_publication(self):
        capability = self.root / ".github/release.toml"
        capability.write_text(capability.read_text() + "tag_merged_commit=true\n")
        self.git("add", ".github/release.toml")
        self.git("commit", "-qm", "fix: use tag at merge")
        self.head = self.git("rev-parse", "HEAD")
        self.env["CANDIDATE_REVISION"] = self.head
        self.git("push", "-q", "origin", "development")
        self.assertEqual(self.collect()["next_tag"], "v0.2.0")
        self.assertEqual(self.git("ls-remote", "origin", "refs/tags/v0.2.0"), "")

    def test_hotfix_preserves_main_ancestry_and_records_incident(self):
        self.git("checkout", "-qb", "fix/op-1-incident", self.base)
        self.git("commit", "-q", "--allow-empty", "-m", "fix: incident")
        self.head = self.git("rev-parse", "HEAD")
        self.env.update(CANDIDATE_REVISION=self.head, CANDIDATE_BRANCH="fix/op-1-incident",
                        HOTFIX_INCIDENT="incident-1", HOTFIX_APPROVAL="approval-1")
        result = self.collect()
        self.assertEqual(result["mode"], "hotfix")
        self.assertEqual(result["next_tag"], "v0.1.1")

    def test_saved_evidence_is_verified_against_pinned_run_and_logs(self):
        # Wood retains its coverage database alongside logs. Artifact upload must
        # include hidden files so the independently verified inventory is exact.
        original = self.wood
        def with_coverage(args, log, **kwargs):
            data = original(args, log, **kwargs)
            if args == ["repo", "validate"]:
                directory = log.parent / "wood-validate"
                directory.mkdir()
                (directory / ".coverage").write_bytes(b"disposable coverage fixture")
            return data
        with patch.object(self, "wood", with_coverage):
            record = self.collect()
        self.assertIn("wood-validate/.coverage", record["files"])
        stamp = datetime.now(timezone.utc).isoformat()
        check = {"id": 1, "details_url": "https://github.com/owner/fixture/actions/runs/123/job/456"}
        run = {"head_sha": self.head, "event": "pull_request", "status": "completed", "conclusion": "success",
               "run_attempt": 1, "referenced_workflows": [{"sha": "d" * 40,
               "path": "SpencerRWood/workflows/.github/workflows/release-readiness.yml@" + "d" * 40}]}
        real_run = subprocess.run
        def download(argv, **kwargs):
            if argv[:3] == ["gh", "run", "download"]:
                shutil.copytree(self.evidence, Path(argv[-1]), dirs_exist_ok=True)
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, **kwargs)
        job = {"run_id": 123, "run_attempt": 1, "head_sha": self.head, "status": "completed", "conclusion": "success",
               "check_run_url": "https://api.github.com/repos/owner/fixture/check-runs/1"}
        with patch.object(authorization, "api", side_effect=lambda path:
                          job if "/actions/jobs/" in path else run), patch.object(subprocess, "run", download):
            result = authorization.readiness_evidence("owner/fixture", check, self.head, "d" * 40,
                                                      self.mapping, self.base, stamp)
            self.assertEqual(result["next_tag"], record["next_tag"])
            (self.evidence / "security.log").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "tampered|check/log differs"):
                authorization.readiness_evidence("owner/fixture", check, self.head, "d" * 40,
                                                self.mapping, self.base, stamp)

    def test_wrong_shared_workflow_does_not_authorize_a_named_green_check(self):
        run = {"head_sha": self.head, "event": "pull_request", "status": "completed", "conclusion": "success",
               "referenced_workflows": []}
        job = {"run_id": 123, "run_attempt": 1, "head_sha": self.head, "status": "completed", "conclusion": "success",
               "check_run_url": "https://api.github.com/repos/owner/fixture/check-runs/1"}
        with patch.object(authorization, "api", side_effect=lambda path:
                          job if "/actions/jobs/" in path else run):
            with self.assertRaisesRegex(ValueError, "pinned shared verifier"):
                authorization.readiness_evidence("owner/fixture", {"id": 1, "details_url":
                    "https://github.com/owner/fixture/actions/runs/123/job/456"}, self.head,
                    "d" * 40, self.mapping, self.base, datetime.now(timezone.utc).isoformat())
