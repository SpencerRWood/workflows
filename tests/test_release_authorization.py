"""Fixture-only authorization: no repository writes, publication or deployments."""

import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_authorization as authorization
from release_images import image_matrix


class ReleaseAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.revision, self.head, self.base = "a" * 40, "b" * 40, "c" * 40
        self.mapping = {"strategy": "integration", "release_id": 26,
                        "integration_branch": "development", "scope_reference": "approved-scope"}
        self.rules = [{"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
                      {"type": "required_status_checks", "parameters": {"required_status_checks":
                          [{"context": "release-readiness", "integration_id": 15368},
                           {"context": "validation", "integration_id": 15368}]}}]
        self.pr = {"number": 4, "merged_at": "2026-10-14T17:00:00Z", "merge_commit_sha": self.revision,
                   "html_url": "https://example.invalid/pr/4", "body": "",
                   "user": {"login": "author"},
                   "head": {"sha": self.head, "ref": "development", "repo": {"full_name": "owner/repo"}},
                   "base": {"ref": "main", "repo": {"full_name": "owner/repo"}}}
        self.reviews = [{"state": "APPROVED", "commit_id": self.head, "user": {"login": "reviewer"},
                         "author_association": "MEMBER", "body": f"Release-Approval: {self.head}",
                         "submitted_at": "2026-10-14T16:59:00Z",
                         "html_url": "https://example.invalid/review/1"}]
        self.checks = [{"id": i, "name": name, "head_sha": self.head,
                        "status": "completed", "conclusion": "success",
                        "completed_at": "2026-10-14T16:58:00Z",
                        "app": {"id": 15368, "slug": "github-actions"},
                        "html_url": f"https://example.invalid/check/{i}"}
                       for i, name in enumerate(("release-readiness", "validation"), 1)]
        self.parents = f"{self.revision} {self.base} {self.head}"
        self.remote = self.revision + "\trefs/heads/main"

    def api(self, path, **kwargs):
        if path.endswith("/protection"):
            return {}
        if path.endswith("/comments"):
            return [[]]
        if "rules/branches/main" in path:
            return [self.rules]
        if path.endswith("/pulls"):
            return [[self.pr]]
        if path.endswith("/reviews"):
            return [self.reviews]
        if "/check-runs" in path:
            return [{"check_runs": self.checks}]
        self.fail(path)

    def git(self, *args):
        return {"rev-parse": self.revision, "ls-remote": self.remote,
                "rev-list": self.parents}[args[0]]

    def authorize(self, **kwargs):
        with patch.object(authorization, "api", self.api), patch.object(authorization, "git", self.git), \
                patch.object(authorization, "readiness_evidence", return_value={
                    "incident": kwargs.get("incident", ""), "hotfix_approval": kwargs.get("approval", ""),
                    "scope": {"deferrals": {}}, "next_tag": "v1.0.0"}), \
                patch.object(authorization.subprocess, "run") as ancestry:
            result = authorization.authorize("owner/repo", self.revision, self.mapping,
                                             "release-readiness", workflow_revision="d" * 40, **kwargs)
            return result, ancestry

    def test_approved_integration_has_revision_bound_evidence(self):
        record, ancestry = self.authorize()
        self.assertEqual(record["candidate_revision"], self.head)
        self.assertEqual(record["prior_main_revision"], self.base)
        self.assertEqual(len(record["checks"]), 2)
        ancestry.assert_any_call(["git", "merge-base", "--is-ancestor", self.base, self.head], check=True)
        ancestry.assert_any_call(["git", "diff", "--exit-code", self.head, self.revision], check=True)

    def test_arbitrary_push_fork_wrong_target_or_non_development_is_rejected(self):
        for key, value in [("merged_at", None), ("merge_commit_sha", self.base)]:
            original = copy.deepcopy(self.pr)
            self.pr[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.authorize()
            self.pr = original
        for section, key, value in [("base", "ref", "development"), ("head", "ref", "feature/op-1")]:
            original = copy.deepcopy(self.pr)
            self.pr[section][key] = value
            with self.subTest(section=section), self.assertRaises(ValueError):
                self.authorize()
            self.pr = original
        self.pr["head"]["repo"]["full_name"] = "fork/repo"
        with self.assertRaisesRegex(ValueError, "fork"):
            self.authorize()

    def test_absent_review_protection_or_required_release_gate_fails(self):
        for rules in [[], self.rules[:1], self.rules[1:]]:
            with self.subTest(rules=rules), patch.object(self, "rules", rules), self.assertRaises(ValueError):
                self.authorize()
        self.rules[1]["parameters"]["required_status_checks"] = [{"context": "validation"}]
        with self.assertRaisesRegex(ValueError, "required main check"):
            self.authorize()

    def test_stale_dismissed_self_or_unbound_reviews_fail(self):
        for changes in [{"state": "DISMISSED"}, {"commit_id": self.base},
                        {"user": {"login": "author"}}, {"author_association": "CONTRIBUTOR"},
                        {"submitted_at": "2026-10-14T17:01:00Z"}]:
            original = copy.deepcopy(self.reviews)
            self.reviews[0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.authorize()
            self.reviews = original
        self.reviews.append({**self.reviews[0], "state": "CHANGES_REQUESTED"})
        with self.assertRaises(ValueError):
            self.authorize()

    def test_failed_pending_skipped_spoofed_or_stale_ci_fails(self):
        for changes in [{"conclusion": "failure"}, {"conclusion": "skipped"},
                        {"status": "in_progress"}, {"head_sha": self.base},
                        {"app": {"id": 15368, "slug": "other-app"}},
                        {"app": {"id": 1, "slug": "github-actions"}},
                        {"completed_at": "2026-10-14T17:01:00Z"}]:
            original = copy.deepcopy(self.checks)
            self.checks[0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.authorize()
            self.checks = original

    def test_superseded_main_or_squash_history_fails(self):
        self.remote = self.base + "\trefs/heads/main"
        with self.assertRaisesRegex(ValueError, "main advanced"):
            self.authorize()
        self.remote = self.revision + "\trefs/heads/main"
        self.parents = f"{self.revision} {self.base} {self.head} {'e' * 40}"
        with self.assertRaisesRegex(ValueError, "unsupported ancestry"):
            self.authorize()

    def test_unreconciled_hotfix_blocks_normal_release(self):
        with patch.object(authorization.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "git")), \
                patch.object(authorization, "api", self.api), patch.object(authorization, "git", self.git):
            with self.assertRaises(subprocess.CalledProcessError):
                authorization.authorize("owner/repo", self.revision, self.mapping, "release-readiness", workflow_revision="d" * 40)

    def test_hotfix_requires_both_reviewed_references_and_exact_approval(self):
        self.pr["head"]["ref"] = "fix/op-1-incident"
        self.pr["body"] = "Hotfix-Incident: incident-1\nHotfix-Approval: approval-1"
        result, ancestry = self.authorize(incident="incident-1", approval="approval-1")
        self.assertEqual(result["incident"], "incident-1")
        ancestry.assert_called_once_with(["git", "diff", "--exit-code", self.head, self.revision], check=True)
        for refs in [{"incident": "incident-1"}, {"approval": "approval-1"},
                     {"incident": "other", "approval": "approval-1"}]:
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                self.authorize(**refs)


class WorkflowIntegrationTests(unittest.TestCase):
    def workflow(self, name):
        return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())

    def test_validation_and_readiness_have_no_publication_credentials(self):
        for name in ("release-readiness.yml",):
            document = self.workflow(name)
            self.assertEqual(document["permissions"]["contents"], "read")
            self.assertNotIn("packages", document["permissions"])
            self.assertNotIn("deployments", document["permissions"])
            text = str(document["jobs"])
            self.assertNotIn("packages: write", text)
            self.assertNotIn("--vcs-release", text)
            self.assertNotIn("release_app_private_key", text)
        for name in ("release.yml", "release-container.yml"):
            job = self.workflow(name)["jobs"]["validation"]
            self.assertEqual(job["permissions"], {"contents": "read"})
        validation = self.workflow("validate.yml")
        self.assertNotIn("release_app_private_key", str(validation))
        self.assertEqual(validation["jobs"]["contracts"]["permissions"], {"contents": "read"})

    def test_development_runs_tests_without_a_publisher_or_deployer(self):
        document = self.workflow("validate.yml")
        triggers = document.get("on", document.get(True))
        self.assertEqual(triggers["push"]["branches"], ["development"])
        self.assertEqual(triggers["pull_request"]["branches"], ["development", "main"])
        self.assertEqual(set(document["jobs"]), {"validation", "contracts"})
        self.assertEqual(document["jobs"]["contracts"]["if"], "inputs.workflow_revision == ''")
        self.assertEqual(document["jobs"]["validation"]["if"], "inputs.workflow_revision != ''")
        self.assertIn("pytest", str(document["jobs"]))

    def test_automatic_guard_rejects_other_events_and_development(self):
        guard = self.workflow("authorize-operation.yml")["jobs"]["authorize"]["steps"][0]["run"]
        for event in ("push", "pull_request", "workflow_dispatch"):
            for ref in ("refs/heads/main", "refs/heads/development", "refs/heads/feature/op-1"):
                result = subprocess.run(["bash", "-c", guard], capture_output=True,
                                        env={**os.environ, "RELEASE_INTEGRATION": "true", "AUTOMATIC": "false",
                                             "CALLER_EVENT": event, "CALLER_REF": ref})
                self.assertEqual(result.returncode == 0, event == "push" and ref == "refs/heads/main")

    def test_single_and_multi_image_publication_gate_release_and_deployment(self):
        multi = self.workflow("release-containers.yml")["jobs"]
        self.assertEqual(multi["finalize"]["needs"], ["plan", "images"])
        self.assertFalse(multi["images"]["with"]["finalize_release"])
        self.assertFalse(multi["images"]["strategy"]["fail-fast"])
        self.assertEqual(multi["images"]["with"]["expected_tag"], "${{ needs.plan.outputs.tag }}")
        self.assertEqual(multi["finalize"]["with"]["expected_tag"], "${{ needs.plan.outputs.tag }}")
        single = self.workflow("release-container.yml")["jobs"]["release"]
        finalize = next(s for s in single["steps"] if s.get("id") == "publish")
        self.assertIn("inputs.finalize_release", finalize["if"])
        self.assertIn("--no-commit --no-changelog --skip-build", finalize["run"])
        self.assertIn("inputs.release_integration", single["env"]["RECOVERY_APPROVED"])
        # Publication only: deployment requires an explicit downstream caller.
        self.assertFalse(any("deploy" in j or "promote-container-to-dev" in str(v) for j, v in multi.items()))

    def test_legacy_contracts_remain_opt_in(self):
        for name in ("release.yml", "release-container.yml", "deploy-ansible.yml"):
            document = self.workflow(name)
            inputs = document.get("on", document.get(True))["workflow_call"]["inputs"]
            self.assertFalse(inputs["release_integration"]["default"])
            self.assertFalse(inputs["require_approval"]["default"])
        deploy = self.workflow("deploy-ansible.yml")["jobs"]
        self.assertEqual(deploy["deploy"]["needs"], "authorization")
        rollback = next(s for s in deploy["deploy"]["steps"] if s.get("id") == "rollback")
        self.assertIn("inputs.release_integration", rollback["env"]["AUTOMATIC"])

    def test_representative_adopter_uses_supported_inputs_and_isolates_development(self):
        fixture = yaml.safe_load((ROOT / "tests/fixtures/br02/release.yml").read_text())
        for name, job in fixture["jobs"].items():
            filename, revision = job["uses"].split("@")
            schema = self.workflow(Path(filename).name)
            call = schema.get("on", schema.get(True))["workflow_call"]
            supplied = job.get("with", {})
            self.assertFalse(set(supplied) - set(call.get("inputs", {})))
            self.assertTrue(all(key in supplied for key, value in call.get("inputs", {}).items()
                                if value.get("required")))
            self.assertFalse(set(job.get("secrets", {})) - set(call.get("secrets", {})))
            if "workflow_revision" in supplied:
                self.assertEqual(supplied["workflow_revision"], revision)
            if name == "release":
                self.assertEqual(job["if"], "github.event_name == 'push' && github.ref == 'refs/heads/main'")
                self.assertTrue(supplied["release_integration"])
            else:
                self.assertNotIn("write", str(job.get("permissions", fixture["permissions"])))
        self.assertEqual(set(fixture["jobs"]), {"validation", "readiness", "release"})
        deploy = yaml.safe_load((ROOT / "tests/fixtures/br02/deployment.yml").read_text())["jobs"]["deploy"]
        schema = self.workflow("deploy-ansible.yml")
        call = schema.get("on", schema.get(True))["workflow_call"]
        self.assertFalse(set(deploy["with"]) - set(call["inputs"]))
        self.assertTrue(all(key in deploy["with"] for key, value in call["inputs"].items()
                            if value.get("required")))
        self.assertEqual(deploy["with"]["ref"], "${{ inputs.release_tag }}")
        self.assertTrue(deploy["with"]["release_integration"])
        self.assertTrue(deploy["with"]["privileged_rollback_command"])

    def test_configured_deployment_caller_requires_successful_publication_outputs(self):
        fixture = yaml.safe_load((ROOT / "tests/fixtures/br02/automatic-deployment.yml").read_text())
        triggers = fixture.get("on", fixture.get(True))
        self.assertEqual(triggers, {"push": {"branches": ["main"]}})
        jobs = fixture["jobs"]
        self.assertEqual(jobs["deploy"]["needs"], "release")
        self.assertEqual(jobs["deploy"]["if"], "needs.release.outputs.released == 'true'")
        self.assertEqual(jobs["deploy"]["with"]["ref"], "${{ needs.release.outputs.release_tag }}")
        for job in jobs.values():
            filename, revision = job["uses"].split("@")
            schema = self.workflow(Path(filename).name)
            call = schema.get("on", schema.get(True))["workflow_call"]
            supplied = job["with"]
            self.assertFalse(set(supplied) - set(call["inputs"]))
            self.assertTrue(all(key in supplied for key, value in call["inputs"].items()
                                if value.get("required")))
            self.assertFalse(set(job.get("secrets", {})) - set(call.get("secrets", {})))
            self.assertEqual(supplied["workflow_revision"], revision)
            self.assertTrue(supplied["release_integration"])

    def test_deployment_requires_same_repository_and_exact_approved_release(self):
        step = next(s for s in self.workflow("deploy-ansible.yml")["jobs"]["deploy"]["steps"]
                    if s.get("id") == "release_checkout")
        guard = step["run"].split('echo "repository=')[0]
        fake_git = 'git() { if [[ "$1" == describe ]]; then echo v1.0.0; else echo "$CHECKED_OUT_SHA"; fi; };\n'
        baseline = {"RELEASE_INTEGRATION": "true", "ALLOW_MAIN_COMMIT": "false",
                    "DEPLOY_REF": "v1.0.0", "DEPLOY_REPOSITORY": "owner/repo",
                    "GITHUB_REPOSITORY": "owner/repo", "APPROVED_REVISION": "a" * 40,
                    "CHECKED_OUT_SHA": "a" * 40}
        for changes, allowed in [({}, True), ({"DEPLOY_REPOSITORY": "other/repo"}, False),
                                 ({"CHECKED_OUT_SHA": "b" * 40}, False),
                                 ({"APPROVED_REVISION": ""}, False)]:
            with self.subTest(changes=changes):
                result = subprocess.run(["bash", "-c", fake_git + guard], capture_output=True,
                                        env={**os.environ, **baseline, **changes})
                self.assertEqual(result.returncode == 0, allowed)

    def test_failed_deployment_retry_preserves_release_identity_and_prior_good_state(self):
        steps = self.workflow("deploy-ansible.yml")["jobs"]["deploy"]["steps"]
        record = next(s for s in steps if s["name"] == "Record durable deployment metadata")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test",
                            "-c", "user.email=test@example.invalid", "commit", "-q",
                            "--allow-empty", "-m", "feat: fixture"], check=True)
            revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            state_file = root / "deployment.json"
            state_file.write_text(json.dumps({"current_release": "v0.9.0", "commit_sha": "b" * 40}))
            published_release = {"tag": "v1.0.0", "revision": revision, "digest": "sha256:" + "a" * 64}
            original = copy.deepcopy(published_release)
            for attempt, apply, health, rollback in [(1, "success", "failure", "success"),
                                                      (2, "success", "success", "skipped"),
                                                      (3, "success", "success", "skipped")]:
                result = subprocess.run(["bash", "-e", "-c", record["run"]], cwd=root,
                                        capture_output=True, text=True,
                                        env={**os.environ, "STATE_FILE": str(state_file),
                                             "RELEASE": original["tag"], "TARGET": "infrastructure-dev",
                                             "SOURCE_REVISION": revision, "WORKFLOW_REVISION": "c" * 40,
                                             "APPLY": apply, "HEALTH": health, "ROLLBACK": rollback,
                                             "PREVIOUS": "v0.9.0", "GITHUB_RUN_ID": "123",
                                             "GITHUB_RUN_ATTEMPT": str(attempt)})
                self.assertEqual(result.returncode, 0, result.stderr)
                state = json.loads(state_file.read_text())
                self.assertEqual(state["run_attempt"], str(attempt))
                self.assertEqual(state["last_attempt_commit_sha"], revision)
                self.assertEqual(published_release, original)
                if attempt == 1:
                    self.assertEqual(state["current_release"], "v0.9.0")
                    self.assertEqual(state["last_attempt_state"], "failure")
                    self.assertEqual(state["rollback_state"], "success")
                else:
                    self.assertEqual(state["current_release"], "v1.0.0")
                    self.assertEqual(state["previous_successful_release"], "v0.9.0")
                    self.assertEqual(state["commit_sha"], revision)
                self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)

    def test_matrix_rejects_duplicates_and_unsafe_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            previous = Path.cwd()
            os.chdir(temporary)
            self.addCleanup(os.chdir, previous)
            Path("Dockerfile").write_text("FROM scratch\n")
            import json
            image = {"image_name": "api", "dockerfile": "Dockerfile", "context": "."}
            self.assertEqual(len(image_matrix(json.dumps([image, {**image, "image_name": "worker"}]))), 2)
            for images in [[], [image, image], [{**image, "context": "../outside"}],
                           [{**image, "image_name": "other/repo"}], [{**image, "dockerfile": "missing"}]]:
                with self.subTest(images=images), self.assertRaises(ValueError):
                    image_matrix(json.dumps(images))

    def test_coordinated_plan_retries_exact_tag_but_skips_only_published_ancestors(self):
        plan = next(s for s in self.workflow("release-containers.yml")["jobs"]["plan"]["steps"]
                    if s.get("id") == "plan")
        script = plan["run"].replace("${{ steps.config.outputs.dependency_group }}", "dev")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = root / "workflow-contract/scripts"
            helper.mkdir(parents=True)
            shutil.copyfile(ROOT / "scripts/release_images.py", helper / "release_images.py")
            subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (root / "Dockerfile").write_text("FROM scratch\n")
            git("add", "Dockerfile")
            git("commit", "-qm", "feat: image")
            old = git("rev-parse", "HEAD")
            git("tag", "v0.9.0")
            git("commit", "-q", "--allow-empty", "-m", "feat: coordinated images")
            current = git("rev-parse", "HEAD")
            git("tag", "v1.0.0")
            prefix = ('uv() { if [[ "$*" == *--print-tag* ]]; then echo "$SEMANTIC_TAG"; fi; };\n'
                      'gh() { if [[ "$PUBLISHED_RELEASE" == true ]]; then echo 1; else return 1; fi; };\n'
                      'python() { if [[ "$1" == */semantic_release_config.py ]]; then return 0; '
                      f'else command {shlex.quote(sys.executable)} "$@"; fi; }};\n')
            images = [{"image_name": name, "dockerfile": "Dockerfile", "context": "."}
                      for name in ("api", "worker")]
            for tag, published, allowed, emits in [("v1.0.0", "false", True, True),
                                                    ("v1.0.0", "true", True, True),
                                                    ("v1.1.0", "false", True, True),
                                                    ("v0.9.0", "true", True, False),
                                                    ("v0.9.0", "false", False, False)]:
                output = root / "outputs"
                output.unlink(missing_ok=True)
                result = subprocess.run(["bash", "-c", prefix + script], cwd=root,
                                        capture_output=True, text=True,
                                        env={**os.environ, "RUNNER_TEMP": str(root),
                                             "GITHUB_OUTPUT": str(output), "GITHUB_REPOSITORY": "owner/repo",
                                             "APPROVED_REVISION": current, "SEMANTIC_TAG": tag,
                                             "PUBLISHED_RELEASE": published, "IMAGES": json.dumps(images)})
                with self.subTest(tag=tag, published=published):
                    self.assertEqual(result.returncode == 0, allowed, result.stderr)
                    self.assertEqual(output.exists(), emits)
                    if emits:
                        emitted = dict(line.split("=", 1) for line in output.read_text().splitlines())
                        self.assertEqual(emitted["tag"], tag)
                        self.assertEqual(json.loads(emitted["images"]), images)
            # A tag on a divergent branch is not a no-impact ancestor.
            git("checkout", "-q", "-b", "divergent", old)
            git("commit", "-q", "--allow-empty", "-m", "fix: divergent release")
            git("tag", "v0.9.1")
            git("checkout", "-q", "main")
            result = subprocess.run(["bash", "-c", prefix + script], cwd=root,
                                    capture_output=True,
                                    env={**os.environ, "RUNNER_TEMP": str(root),
                                         "GITHUB_OUTPUT": str(root / "outputs"), "GITHUB_REPOSITORY": "owner/repo",
                                         "APPROVED_REVISION": current, "SEMANTIC_TAG": "v0.9.1",
                                         "PUBLISHED_RELEASE": "true", "IMAGES": json.dumps(images)})
            self.assertNotEqual(result.returncode, 0)

    def test_readiness_rejects_wrong_event(self):
        steps = self.workflow("release-readiness.yml")["jobs"]["release-readiness"]["steps"]
        guard = steps[0]["run"]
        for event, base in [("push", "main"), ("pull_request", "development")]:
            result = subprocess.run(["bash", "-c", guard], capture_output=True,
                                    env={**os.environ, "EVENT": event, "BASE": base})
            self.assertNotEqual(result.returncode, 0)
