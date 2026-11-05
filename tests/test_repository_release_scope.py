"""Repository scope executes full gates and never pretends to verify live planning."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from test_release_readiness import CandidateFixture, authorization, readiness


class RepositoryScopeTests(CandidateFixture):
    def setUp(self):
        super().setUp()
        self.manifest = {"version": 1, "project_id": 3, "initiative_id": 552,
                         "release_id": 26, "release_name": "R1", "scope_reference": "approved-scope",
                         "approval_reference": "Explicit repository release approval",
                         "stories": [{"id": 1, "merge_commits": [self.head], "defer_reason": ""}]}
        self.filename = ".github/release-scope.json"
        self.commit_scope(self.manifest)

    def commit_scope(self, manifest):
        (self.root / self.filename).write_text(json.dumps(manifest))
        self.git("add", self.filename)
        self.git("commit", "-qm", "docs: approve repository release scope")
        self.head = self.git("rev-parse", "HEAD")
        self.env["CANDIDATE_REVISION"] = self.head
        self.git("push", "-q", "origin", "development")

    def test_no_openproject_token_or_lookup_and_all_checks_remain_mandatory(self):
        self.env.pop("OPENPROJECT_API_TOKEN")
        original = self.wood
        def local_only(args, log, **kwargs):
            self.assertNotIn(args[0], {"release", "story"})
            return original(args, log, **kwargs)
        with patch.dict(os.environ, self.env), patch.object(readiness, "wood", local_only):
            record = readiness.collect(self.evidence, self.integration, self.security,
                                       scope_file=self.filename)
        self.assertEqual(record["next_tag"], "v0.2.0")
        self.assertEqual(record["scope"]["source"]["type"], "repository")
        self.assertEqual(record["scope"]["stories"][0]["status"], "Integrated")
        self.assertIn("no live OpenProject", record["scope"]["source"]["limitation"])
        self.assertEqual(set(record["checks"]), {"scope", "validation", "integration", "security", "semantic-impact"})

    def test_scope_file_cannot_disable_security(self):
        with self.assertRaisesRegex(ValueError, "security.log"):
            self.collect(scope_file=self.filename, security="exit 42")
        self.assertFalse((self.evidence / "readiness.json").exists())

    def test_tag_at_merge_uses_supported_generator_interface_without_publication(self):
        capability = self.root / ".github/release.toml"
        capability.write_text(capability.read_text() + "tag_merged_commit=true\n")
        self.git("add", ".github/release.toml")
        self.commit_scope(self.manifest)
        record = self.collect(scope_file=self.filename)
        self.assertEqual(record["next_tag"], "v0.2.0")
        self.assertEqual(self.git("ls-remote", "origin", "refs/tags/v0.2.0"), "")

    def test_deferrals_must_be_in_approved_file(self):
        with self.assertRaisesRegex(ValueError, "committed scope"):
            self.collect(scope_file=self.filename, deferrals={"1": "caller bypass"})

    def test_unsafe_or_missing_scope_never_falls_back_to_live_planning(self):
        for index, filename in enumerate(["../outside.json", "/tmp/outside.json", ".github/missing.json"]):
            with self.subTest(filename=filename):
                self.evidence = Path(self.temporary.name) / f"path-{index}"
                with patch.object(readiness, "verify_scope", side_effect=AssertionError("unexpected planning lookup")):
                    with self.assertRaises(ValueError):
                        self.collect(scope_file=filename)

    def test_scope_symlink_is_rejected(self):
        (self.root / ".github/symlink.json").symlink_to(self.root / self.filename)
        with self.assertRaisesRegex(ValueError, "regular repository file"):
            self.collect(scope_file=".github/symlink.json")

    def test_invalid_scope_mapping_approval_and_duplicates_fail(self):
        for index, change in enumerate([
            {"project_id": 999}, {"release_id": 999}, {"initiative_id": 999},
            {"approval_reference": ""}, {"scope_reference": "unapproved"},
            {"stories": []}, {"stories": self.manifest["stories"] * 2},
            {"stories": [{"id": 1, "merge_commits": [], "defer_reason": ""}]},
            {"unexpected": True},
        ]):
            with self.subTest(change=change):
                self.commit_scope({**self.manifest, **change})
                self.evidence = Path(self.temporary.name) / f"invalid-{index}"
                with self.assertRaises(ValueError):
                    self.collect(scope_file=self.filename)

    def test_uncommitted_manifest_or_unintegrated_code_fails(self):
        (self.root / self.filename).write_text("{}")
        with self.assertRaisesRegex(ValueError, "committed candidate"):
            self.collect(scope_file=self.filename)
        self.git("checkout", "--", self.filename)
        self.git("checkout", "-qb", "unintegrated", self.base)
        self.git("commit", "-q", "--allow-empty", "-m", "feat: not integrated")
        missing = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "development")
        manifest = copy.deepcopy(self.manifest)
        manifest["stories"][0]["merge_commits"] = [missing]
        self.commit_scope(manifest)
        self.evidence = Path(self.temporary.name) / "unintegrated-evidence"
        with self.assertRaises(subprocess.CalledProcessError):
            self.collect(scope_file=self.filename)

    def test_saved_receipt_is_independently_bound_to_scope_file(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["stories"][0]["defer_reason"] = "Deployment separately approved later"
        self.commit_scope(manifest)
        record = self.collect(scope_file=self.filename)
        self.assertEqual(record["scope"]["deferrals"], {"1": manifest["stories"][0]["defer_reason"]})
        stamp = datetime.now(timezone.utc).isoformat()
        check = {"id": 1, "details_url": "https://github.com/owner/fixture/actions/runs/123/job/456"}
        run = {"head_sha": self.head, "event": "pull_request", "status": "completed", "conclusion": "success",
               "run_attempt": 1, "referenced_workflows": [{"sha": "d" * 40,
               "path": "SpencerRWood/workflows/.github/workflows/release-readiness.yml@" + "d" * 40}]}
        job = {"run_id": 123, "run_attempt": 1, "head_sha": self.head, "status": "completed", "conclusion": "success",
               "check_run_url": "https://api.github.com/repos/owner/fixture/check-runs/1"}
        real_run = subprocess.run
        def download(argv, **kwargs):
            if argv[:3] == ["gh", "run", "download"]:
                shutil.copytree(self.evidence, Path(argv[-1]), dirs_exist_ok=True)
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, **kwargs)
        with patch.object(authorization, "api", side_effect=lambda path: job if "/actions/jobs/" in path else run), \
                patch.object(subprocess, "run", download):
            verified = authorization.readiness_evidence("owner/fixture", check, self.head, "d" * 40,
                                                       self.mapping, self.base, stamp)
            self.assertEqual(verified["scope"], record["scope"])
            record["scope"]["source"]["approval_reference"] = "forged approval"
            # Even a syntactically valid receipt with intact log hashes cannot
            # replace the exact scope authority in the candidate's committed file.
            (self.evidence / "readiness.json").write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "committed authority"):
                authorization.readiness_evidence("owner/fixture", check, self.head, "d" * 40,
                                                self.mapping, self.base, stamp)
