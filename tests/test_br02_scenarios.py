"""Bounded release/deployment sequences: real Git/PSR/shell, mocked service writes."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import yaml

from test_release_readiness import CandidateFixture, ROOT
import release_authorization as authorization
import release_publish as publication
import release_registry as registry
import candidate_provenance as provenance


class ReleaseLifecycleTests(CandidateFixture):
    def setUp(self):
        super().setUp()
        self.published, self.publishes, self.registry, self.writes = None, 0, {}, []
        self.fail_publication = False
        self.fail_image = ""
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def merge(self):
        self.record = self.collect()
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "development", "-m", "Merge approved release")
        self.revision = self.git("rev-parse", "HEAD")
        self.git("push", "-q", "origin", "main")
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.pr = {"number": 4, "merged_at": self.timestamp, "merge_commit_sha": self.revision,
                   "html_url": "https://github.com/owner/fixture/pull/4", "body": "",
                   "user": {"login": "maintainer"},
                   "head": {"sha": self.head, "ref": self.env["CANDIDATE_BRANCH"], "repo": {"full_name": "owner/fixture"}},
                   "base": {"ref": "main", "repo": {"full_name": "owner/fixture"}}}

    def api(self, path, **kwargs):
        if "rules/branches" in path:
            return [[{"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
                     {"type": "required_status_checks", "parameters": {"required_status_checks":
                         [{"context": "release-readiness", "integration_id": 15368}]}}]]
        if path.endswith("/pulls"):
            return [[self.pr]]
        if path.endswith("/reviews"):
            return [[]]
        if path.endswith("/comments"):
            return [[{"author_association": "OWNER", "user": {"login": "maintainer", "type": "User"},
                      "body": f"Release-Approval: {self.head}", "created_at": self.timestamp, "updated_at": self.timestamp,
                      "html_url": "https://github.com/owner/fixture/pull/4#issuecomment-1"}]]
        if "/check-runs" in path:
            return [{"check_runs": [{"id": 1, "name": "release-readiness", "head_sha": self.head,
                    "status": "completed", "conclusion": "success", "completed_at": self.timestamp,
                    "app": {"id": 15368, "slug": "github-actions"},
                    "html_url": "https://github.com/owner/fixture/actions/runs/123/job/456",
                    "details_url": "https://github.com/owner/fixture/actions/runs/123/job/456"}]}]
        if path.endswith("/actions/jobs/456"):
            return {"run_id": 123, "head_sha": self.head, "status": "completed", "conclusion": "success", "run_attempt": 1,
                    "check_run_url": "https://api.github.com/repos/owner/fixture/check-runs/1"}
        if path.endswith("/actions/runs/123/attempts/1"):
            return {"head_sha": self.head, "event": "pull_request", "status": "completed", "conclusion": "success",
                    "run_attempt": 1, "referenced_workflows": [{"sha": "d" * 40,
                    "path": "SpencerRWood/workflows/.github/workflows/release-readiness.yml@" + "d" * 40}]}
        if "/releases/tags/" in path:
            if self.published is None:
                raise ValueError("release not published")
            return self.published
        raise AssertionError(path)

    def authorize(self, *, deployment=False):
        real_run = subprocess.run
        def download(argv, **kwargs):
            if argv[:3] == ["gh", "run", "download"]:
                shutil.copytree(self.evidence, Path(argv[-1]), dirs_exist_ok=True)
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, **kwargs)
        with patch.object(authorization, "api", self.api), patch.object(subprocess, "run", download):
            return authorization.authorize("owner/fixture", self.revision, self.mapping, "release-readiness",
                workflow_revision="d" * 40, deployment_tag=self.record["next_tag"] if deployment else "",
                incident=self.env.get("HOTFIX_INCIDENT", ""), approval=self.env.get("HOTFIX_APPROVAL", ""))

    def publish(self):
        authority = self.authorize()
        config = Path(self.temporary.name) / "semantic.json"
        subprocess.run([sys.executable, str(ROOT / "scripts/semantic_release_config.py"),
                        "pyproject.toml", str(config)], check=True)
        def semantic(path, *args, **kwargs):
            if "--print-tag" in args:
                return subprocess.check_output([sys.executable, "-m", "semantic_release", "--config", str(config),
                                                "version", "--print-tag"], text=True).strip()
            if args[0] == "version":
                self.git("tag", authority["release_tag"])
                self.git("push", "-q", "origin", "refs/tags/" + authority["release_tag"])
                if self.fail_publication:
                    raise subprocess.CalledProcessError(1, "mock GitHub release creation")
            self.publishes += 1
            self.published = {"id": 1, "tag_name": authority["release_tag"], "published_at": self.timestamp,
                              "draft": False, "prerelease": False}
            return ""
        with patch.dict(os.environ, {"APPROVAL_REQUIRED": "true", "APPROVED_REVISION": self.revision,
                                     "EXPECTED_TAG": authority["release_tag"], "RELEASE_INTEGRATION": "true"}), \
             patch.object(publication, "semantic", semantic), patch.object(publication, "release", lambda tag: self.published):
            return publication.publish(config, "refs/heads/main", True)

    def images(self, names):
        real_run = subprocess.run
        def docker(argv, **kwargs):
            if argv[:4] == ["docker", "buildx", "imagetools", "create"]:
                tag, source = argv[5], argv[6]
                if self.fail_image and self.fail_image in tag:
                    raise subprocess.CalledProcessError(1, "mock interrupted image publication")
                digest = source.split("@", 1)[1]
                self.registry[tag] = digest
                self.writes.append(tag)
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, **kwargs)
        for index, name in enumerate(names):
            digest = "sha256:" + str(index + 1) * 64
            source = f"ghcr.io/owner/{name}@{digest}"
            tags = [f"ghcr.io/owner/{name}:{self.record['next_tag']}", f"ghcr.io/owner/{name}:sha-{self.revision}"]
            evidence = Path(self.temporary.name) / name
            evidence.mkdir(exist_ok=True)
            with patch.object(registry, "inspect_digest", side_effect=lambda tag: self.registry.get(tag)), \
                 patch.object(subprocess, "run", docker), patch.dict(os.environ, {
                     "APPROVAL_REQUIRED": "true", "APPROVED_REVISION": self.revision, "RELEASE_REF": "refs/heads/main"}):
                registry.promote(source, tags, digest, True, evidence)
        return dict(self.registry)

    def deploy(self, *, fail_health=False, attempt=1):
        self.git("checkout", "-q", "--detach", self.record["next_tag"])
        self.authorize(deployment=True)
        steps = yaml.safe_load((ROOT / ".github/workflows/deploy-ansible.yml").read_text())["jobs"]["deploy"]["steps"]
        selected = {s.get("id"): s for s in steps if s.get("id")}
        target = Path(self.temporary.name) / "runtime.json"
        previous = Path(self.temporary.name) / "prior-artifact.json"
        desired = Path(self.temporary.name) / "published-artifact.json"
        previous.write_text(json.dumps({"release": "v0.1.0", "digest": "sha256:" + "0" * 64}))
        desired.write_text(json.dumps({"release": self.record["next_tag"], "source": self.revision,
                                      "digests": self.registry}))
        if not target.exists():
            shutil.copyfile(previous, target)
        state = Path(self.temporary.name) / "deployment.json"
        if not state.exists():
            state.write_text(json.dumps({"current_release": "v0.1.0"}))
        env = {**os.environ, "SECRETS_FILE": "", "PRIVILEGED_DEPLOY_COMMAND": 'cp "$DESIRED" "$RUNTIME"',
               "PRIVILEGED_HEALTH_CHECK_COMMAND": "", "HEALTH_CHECK_COMMAND":
                   'test "$FAIL_HEALTH" != true && cmp "$DESIRED" "$RUNTIME"',
               "PRIVILEGED_ROLLBACK_COMMAND": 'cp "$PREVIOUS_ARTIFACT" "$RUNTIME"; cmp "$PREVIOUS_ARTIFACT" "$RUNTIME"',
               "AUTOMATIC": "true", "PREVIOUS_REF": "v0.1.0", "PREVIOUS": "v0.1.0", "FAIL_HEALTH": str(fail_health).lower(),
               "DESIRED": str(desired), "RUNTIME": str(target), "PREVIOUS_ARTIFACT": str(previous),
               "STATE_FILE": str(state), "RELEASE": self.record["next_tag"], "TARGET": "fixture-dev",
               "SOURCE_REVISION": self.revision, "WORKFLOW_REVISION": "d" * 40,
               "GITHUB_RUN_ID": "999", "GITHUB_RUN_ATTEMPT": str(attempt)}
        def execute(name):
            script = selected[name]["run"]
            if name == "rollback":
                script = script.split('git checkout --detach')[0]
            return subprocess.run(["bash", "-c", script], env=env, capture_output=True)
        apply = execute("apply")
        env["APPLY_OUTCOME"] = "success" if apply.returncode == 0 else "failure"
        health = execute("health")
        rollback = execute("rollback") if apply.returncode or health.returncode else None
        env.update(APPLY=env["APPLY_OUTCOME"], HEALTH="success" if health.returncode == 0 else "failure",
                   ROLLBACK="success" if rollback and rollback.returncode == 0 else "skipped")
        record = next(s for s in steps if s["name"] == "Record durable deployment metadata")
        subprocess.run(["bash", "-e", "-c", record["run"]], env=env, check=True, capture_output=True)
        return json.loads(state.read_text()), json.loads(target.read_text())

    def test_01_multiple_stories_integrate_into_development_without_publication(self):
        record = self.collect()
        self.assertEqual(record["next_tag"], "v0.2.0")
        self.assertEqual(self.git("ls-remote", "origin", "refs/tags/v0.2.0"), "")
        self.assertIsNone(self.published)

    def test_03_development_failure_prevents_release(self):
        Path("app.py").write_text("result = 0\n")
        with self.assertRaisesRegex(ValueError, "validation failed"):
            self.collect()
        self.assertEqual(self.publishes, 0)

    def test_04_approved_integration_runs_one_idempotent_cycle(self):
        self.merge()
        self.assertEqual(self.publish(), (True, "v0.2.0"))
        self.assertEqual(self.publish(), (True, "v0.2.0"))
        self.assertEqual(self.publishes, 1)

    def test_05_cli_library_github_release_has_exact_main_tag(self):
        self.merge()
        self.publish()
        self.assertEqual(self.git("rev-parse", "v0.2.0^{commit}"), self.revision)
        self.assertEqual(self.published["tag_name"], "v0.2.0")

    def test_06_single_image_digest_precedes_release(self):
        self.merge()
        digests = self.images(["api"])
        self.assertIsNone(self.published)
        self.publish()
        self.assertEqual(len(digests), 2)

    def test_07_multi_image_interruption_blocks_finalization_and_recovers_only_missing(self):
        self.merge()
        self.fail_image = "worker"
        with self.assertRaises(subprocess.CalledProcessError):
            self.images(["api", "worker"])
        self.assertIsNone(self.published)
        original = list(self.writes)
        self.fail_image = ""
        self.images(["api", "worker"])
        self.assertEqual(self.writes[:2], original)
        self.assertEqual(len(self.writes), 4)
        self.publish()
        self.assertEqual(len(set(self.registry.values())), 2)

    def test_08_configured_deployment_promotes_published_identity_and_verifies_runtime(self):
        self.merge()
        self.images(["api"])
        self.publish()
        state, runtime = self.deploy()
        self.assertEqual(state["last_attempt_state"], "success")
        self.assertEqual(runtime["source"], self.revision)
        self.assertEqual(runtime["digests"], self.registry)

    def test_09_publication_without_deployment_changes_no_runtime(self):
        self.merge()
        self.publish()
        self.assertFalse((Path(self.temporary.name) / "runtime.json").exists())

    def test_10_missing_explicit_release_approval_blocks_publication(self):
        self.merge()
        original = self.api
        with patch.object(self, "api", side_effect=lambda path, **kw:
                          [[]] if path.endswith("/comments") else original(path, **kw)):
            with self.assertRaisesRegex(ValueError, "explicit release approval"):
                self.publish()
        self.assertIsNone(self.published)

    def test_11_hotfix_release_blocks_next_normal_release_until_back_integration(self):
        self.git("checkout", "-q", "main")
        self.git("branch", "-f", "development", self.base)
        self.git("checkout", "-qb", "fix/op-1-incident", self.base)
        self.git("commit", "-q", "--allow-empty", "-m", "fix: incident")
        self.head = self.git("rev-parse", "HEAD")
        self.env.update(CANDIDATE_REVISION=self.head, CANDIDATE_BRANCH="fix/op-1-incident",
                        HOTFIX_INCIDENT="incident-1", HOTFIX_APPROVAL="approval-1")
        self.record = self.collect()
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "fix/op-1-incident", "-m", "Merge approved hotfix")
        self.revision = self.git("rev-parse", "HEAD")
        self.git("push", "-q", "origin", "main")
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.pr = {"number": 4, "merged_at": self.timestamp, "merge_commit_sha": self.revision,
                   "html_url": "https://github.com/owner/fixture/pull/4", "body":
                   "Hotfix-Incident: incident-1\nHotfix-Approval: approval-1", "user": {"login": "maintainer"},
                   "head": {"sha": self.head, "ref": "fix/op-1-incident", "repo": {"full_name": "owner/fixture"}},
                   "base": {"ref": "main", "repo": {"full_name": "owner/fixture"}}}
        self.assertEqual(self.publish(), (True, "v0.1.1"))
        self.git("checkout", "-q", "development")
        self.env.update(CANDIDATE_REVISION=self.git("rev-parse", "HEAD"), CANDIDATE_BRANCH="development",
                        TARGET_REVISION=self.revision, HOTFIX_INCIDENT="", HOTFIX_APPROVAL="")
        self.head = self.env["CANDIDATE_REVISION"]
        self.evidence = Path(self.temporary.name) / "unreconciled"
        with self.assertRaises(subprocess.CalledProcessError):
            self.collect()
        self.git("merge", "-q", "--no-ff", "main", "-m", "Reconcile approved hotfix")
        self.head = self.git("rev-parse", "HEAD")
        self.env["CANDIDATE_REVISION"] = self.head
        self.evidence = Path(self.temporary.name) / "reconciled"
        self.collect()

    def test_12_publication_and_deployment_failure_retries_preserve_identity(self):
        self.merge()
        self.images(["api"])
        self.fail_publication = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.fail_publication = False
        self.publish()
        identity = copy.deepcopy(self.published)
        state, runtime = self.deploy(fail_health=True)
        self.assertEqual(runtime["release"], "v0.1.0")
        self.assertEqual(state["rollback_state"], "success")
        # Deployment recovery remains valid after main integrates another Story.
        self.git("checkout", "-q", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "docs: later integration")
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", "--detach", self.record["next_tag"])
        with self.assertRaisesRegex(ValueError, "main advanced"):
            self.publish()
        guard = yaml.safe_load((ROOT / ".github/workflows/authorize-operation.yml").read_text())["jobs"]["authorize"]["steps"][0]["run"]
        for approved, allowed in [("true", True), ("false", False)]:
            result = subprocess.run(["bash", "-c", guard], capture_output=True,
                                    env={**os.environ, "RELEASE_INTEGRATION": "true", "AUTOMATIC": "false",
                                         "CALLER_EVENT": "workflow_dispatch", "CALLER_REF": "refs/heads/main", "APPROVED": approved})
            self.assertEqual(result.returncode == 0, allowed)
        state, runtime = self.deploy(attempt=2)
        self.assertEqual(state["current_release"], "v0.2.0")
        self.assertEqual(self.published, identity)
        self.assertEqual(self.publishes, 1)

    def test_13_successive_fix_dependency_breaking_and_no_impact_are_real_psr_calculations(self):
        record = self.collect()
        self.assertEqual(record["next_tag"], "v0.2.0")
        self.git("tag", "v0.2.0")
        for number, (message, tag) in enumerate([
            ("fix: correction", "v0.2.1"), ("chore(deps): update dependency", "v0.2.2"),
            ("feat!: remove interface\n\nBREAKING CHANGE: interface removed", "v1.0.0"),
            ("docs: no impact", "v1.0.0")]):
            self.git("commit", "-q", "--allow-empty", "-m", message)
            self.head = self.git("rev-parse", "HEAD")
            self.env["CANDIDATE_REVISION"] = self.head
            self.git("push", "-q", "origin", "development")
            self.evidence = Path(self.temporary.name) / f"successive-{number}"
            self.assertEqual(self.collect()["next_tag"], tag)
            if number < 3:
                self.git("tag", tag)
        self.assertEqual(self.publishes, 0)

    def test_14_legacy_consumers_keep_explicit_modes_and_supported_secrets(self):
        for filename in ("release.yml", "release-container.yml", "deploy-ansible.yml"):
            workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
            inputs = workflow.get("on", workflow.get(True))["workflow_call"]["inputs"]
            self.assertFalse(inputs["release_integration"]["default"])
        multi = yaml.safe_load((ROOT / ".github/workflows/release-containers.yml").read_text())
        for name in ("images", "finalize"):
            for secret in ("dbt_host", "dbt_user", "dbt_password", "dbt_port", "dbt_dbname", "dbt_schema"):
                self.assertEqual(multi["jobs"][name]["secrets"][secret], "${{ secrets." + secret + " }}")
