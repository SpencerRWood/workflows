"""No registry/GitHub mutations: exercise source, signed receipt and partial-state boundaries."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import candidate_provenance as provenance
import release_head
import release_registry as registry


class SourceTests(unittest.TestCase):
    def test_approved_revision_mismatch_rejected_before_remote_access(self):
        with patch.object(
            release_head.subprocess, "check_output", return_value="a" * 40
        ) as git:
            with self.assertRaisesRegex(ValueError, "explicitly approved"):
                release_head.is_current("refs/heads/main", "b" * 40)
            self.assertEqual(git.call_count, 1)

    def test_matching_and_superseded_remote(self):
        for remote, expected in [("a", True), ("b", False)]:
            with (
                self.subTest(remote=remote),
                patch.object(
                    release_head.subprocess,
                    "check_output",
                    side_effect=["a" * 40, remote * 40 + "\trefs/heads/main"],
                ),
            ):
                self.assertEqual(
                    release_head.is_current("refs/heads/main", "a" * 40), expected
                )

    def test_required_approval_missing_or_superseded_is_nonzero_without_output(self):
        for revision, current in [("", True), ("a" * 40, False)]:
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "outputs"
                with (
                    patch.dict(
                        os.environ,
                        {
                            "APPROVAL_REQUIRED": "true",
                            "APPROVED_REVISION": revision,
                            "RELEASE_REF": "refs/heads/main",
                            "GITHUB_OUTPUT": str(output),
                        },
                    ),
                    patch.object(sys, "argv", ["release_head.py", "--require-current"]),
                    patch.object(release_head, "is_current", return_value=current),
                ):
                    self.assertEqual(release_head.main(), 1)
                    self.assertFalse(output.exists())


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "source"
        self.root.mkdir()
        (self.root / ".github").mkdir()
        (self.root / ".github/release.toml").write_text(
            'version=1\n[python]\nversion="3.14"\n'
        )
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        for name, value in {
            "Dockerfile": "FROM scratch\n",
            "uv.lock": 'version=1\n[[package]]\nname="example-app"\nversion="0.5.1"\nsource={editable="."}\n',
            "pyproject.toml": '[project]\nname="example-app"\nversion="0.5.1"\n[tool.semantic_release]\nversion_toml=["pyproject.toml:project.version"]\n',
            "app.py": "print('app')\n",
            ".gitignore": "secret.env\n",
        }.items():
            (self.root / name).write_text(value)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "feat: app",
            ],
            check=True,
        )
        self.source = provenance.command(
            "git", "-C", str(self.root), "rev-parse", "HEAD"
        )
        (self.root / "pyproject.toml").write_text(
            '[project]\nname="example-app"\nversion="0.6.0"\n[tool.semantic_release]\nversion_toml=["pyproject.toml:project.version"]\n'
        )
        (self.root / "uv.lock").write_text(
            (self.root / "uv.lock")
            .read_text()
            .replace('version="0.5.1"', 'version="0.6.0"')
        )
        (self.root / "secret.env").write_text("never-copy-this\n")
        self.environment = {
            "SOURCE_REVISION": self.source,
            "WORKFLOW_REVISION": "b" * 40,
            "RELEASE_TAG": "v0.6.0",
            "GITHUB_REPOSITORY": "owner/app",
            "DOCKERFILE": "Dockerfile",
            "BUILD_CONTEXT": ".",
            "PLATFORMS": "linux/amd64",
        }
        self.snapshot = Path(self.temporary.name) / "snapshot"
        self.identity = provenance.prepare(self.root, self.snapshot, self.environment)
        self.digest = "sha256:" + "c" * 64
        self.repository = "ghcr.io/owner/app"
        self.predicate = {
            "identity": self.identity,
            "digest": self.digest,
            "build_metadata": {"containerimage.digest": self.digest},
            "run_id": "12",
        }
        self.results = [
            {
                "verificationResult": {
                    "statement": {
                        "predicateType": provenance.PREDICATE,
                        "subject": [
                            {
                                "name": self.repository,
                                "digest": {"sha256": self.digest[7:]},
                            }
                        ],
                        "predicate": self.predicate,
                    }
                }
            }
        ]

    def test_prepared_lockfile_and_inputs_identity_excludes_ignored_runtime_files(self):
        self.assertFalse((self.snapshot / "secret.env").exists())
        self.assertEqual(
            (self.snapshot / "pyproject.toml").read_bytes(),
            (self.root / "pyproject.toml").read_bytes(),
        )
        self.assertEqual(
            self.identity["lockfile"]["sha256"],
            hashlib.sha256((self.root / "uv.lock").read_bytes()).hexdigest(),
        )
        (self.root / "app.py").write_text("changed\n")
        self.assertNotEqual(
            provenance.fingerprint(provenance.inputs(self.root)),
            self.identity["inputs_sha256"],
        )

    def test_rejects_wrong_source_version_workflow_and_snapshot_overwrite(self):
        for override in (
            {"SOURCE_REVISION": "a" * 40},
            {"WORKFLOW_REVISION": "main"},
            {"RELEASE_TAG": "v0.7.0"},
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                provenance.prepare(
                    self.root,
                    Path(self.temporary.name) / "other",
                    {**self.environment, **override},
                )
        with self.assertRaisesRegex(ValueError, "preserve"):
            provenance.prepare(self.root, self.snapshot, self.environment)

    def test_signed_receipt_identity_and_digest_must_match(self):
        self.assertEqual(
            provenance.verified_receipt(
                self.results, self.identity, self.repository, self.digest
            ),
            self.predicate,
        )
        for key in [
            "source_sha",
            "workflow_sha",
            "version",
            "consumer_type",
            "platforms",
            "dockerfile",
            "lockfile",
            "inputs_sha256",
        ]:
            bad = copy.deepcopy(self.results)
            bad[0]["verificationResult"]["statement"]["predicate"]["identity"][key] = (
                "conflict"
            )
            with (
                self.subTest(field=key),
                self.assertRaisesRegex(ValueError, "provenance"),
            ):
                provenance.verified_receipt(
                    bad, self.identity, self.repository, self.digest
                )
        with self.assertRaises(ValueError):
            provenance.verified_receipt(
                self.results, self.identity, self.repository, "sha256:" + "d" * 64
            )
        with self.assertRaises(ValueError):
            provenance.verified_receipt([], self.identity, self.repository, self.digest)

    def test_final_metadata_commit_must_match_source_parent_inputs_and_remote(self):
        with (
            patch.object(
                provenance,
                "command",
                side_effect=["d" * 40, self.source, "d" * 40 + "\trefs/tags/v0.6.0"],
            ),
            patch.object(provenance, "is_current", return_value=True),
            patch.object(provenance, "inputs", return_value=self.identity["files"]),
        ):
            provenance.verify_finalized(self.root, self.identity)
        for changed in ["parent", "inputs", "remote", "tag"]:
            files = copy.deepcopy(self.identity["files"])
            if changed == "inputs":
                files[0]["sha256"] = "e" * 64
            with (
                self.subTest(changed=changed),
                patch.object(
                    provenance,
                    "command",
                    side_effect=[
                        "d" * 40,
                        "e" * 40 if changed == "parent" else self.source,
                        ("e" if changed == "tag" else "d") * 40 + "\trefs/tags/v0.6.0",
                    ],
                ),
                patch.object(
                    provenance, "is_current", return_value=changed != "remote"
                ),
                patch.object(provenance, "inputs", return_value=files),
            ):
                with self.assertRaises(ValueError):
                    provenance.verify_finalized(self.root, self.identity)

    def test_exact_source_tag_retains_prepared_image_identity_without_main_metadata_commit(self):
        with (patch.object(provenance, "command", side_effect=[self.source, "b" * 40,
                    self.source + "\trefs/tags/v0.6.0"]),
              patch.object(provenance, "is_current", return_value=True),
              patch.object(provenance, "inputs") as files):
            provenance.verify_finalized(self.root, self.identity)
            files.assert_not_called()

    def test_recovered_receipt_requires_exact_source_workflow_version_and_manifest(self):
        environment = {"GITHUB_REPOSITORY": self.identity["repository"],
                       "SOURCE_REVISION": self.identity["source_sha"],
                       "WORKFLOW_REVISION": self.identity["workflow_sha"],
                       "RELEASE_TAG": self.identity["version"], "IMAGE_REPOSITORY": self.repository,
                       "DOCKERFILE": self.identity["dockerfile"], "BUILD_CONTEXT": self.identity["build_context"],
                       "PLATFORMS": ",".join(self.identity["platforms"])}
        with patch.object(provenance, "command", return_value=json.dumps(self.results)):
            with tempfile.TemporaryDirectory() as temporary:
                recovered = provenance.recover_identity(environment, self.digest, Path(temporary))
                self.assertEqual(recovered, self.identity)
                self.assertTrue((Path(temporary) / "identity.json").exists())
            for field in ("SOURCE_REVISION", "WORKFLOW_REVISION", "RELEASE_TAG", "DOCKERFILE", "BUILD_CONTEXT", "PLATFORMS"):
                changed = {**environment, field: "e" * 40 if "REVISION" in field else "different"}
                with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                    provenance.recover_identity(changed, self.digest, Path(temporary))


    def test_preparation_cannot_introduce_unreviewed_code_or_dependency_changes(self):
        for name, value in [
            ("app.py", "changed code\n"),
            ("extra.py", "new code\n"),
            (
                "pyproject.toml",
                '[project]\nname="other-app"\nversion="0.6.0"\n[tool.semantic_release]\nversion_toml=["pyproject.toml:project.version"]\n',
            ),
        ]:
            path = self.root / name
            old = path.read_bytes() if path.exists() else None
            path.write_text(value)
            with self.subTest(path=name), self.assertRaises(ValueError):
                provenance.prepare(
                    self.root,
                    Path(self.temporary.name) / "unapproved",
                    self.environment,
                )
            if old is None:
                path.unlink()
            else:
                path.write_bytes(old)

    def test_preparation_rejects_lock_dependency_changes_and_wrong_package_version(
        self,
    ):
        path = self.root / "uv.lock"
        original = path.read_text()
        for changed in [
            original + '[[package]]\nname="unapproved-dependency"\nversion="1.0.0"\n',
            original.replace('editable="."', 'editable="other"'),
            original.replace('version="0.6.0"', 'version="0.5.1"'),
        ]:
            with self.subTest(lock=changed), self.assertRaises(ValueError):
                path.write_text(changed)
                provenance.prepare(
                    self.root,
                    Path(self.temporary.name) / "unapproved-lock",
                    self.environment,
                )
        path.write_text(original)

    def test_receipt_cannot_rebind_different_build_digest(self):
        bad = copy.deepcopy(self.results)
        bad[0]["verificationResult"]["statement"]["predicate"]["build_metadata"][
            "containerimage.digest"
        ] = "sha256:" + "e" * 64
        with self.assertRaises(ValueError):
            provenance.verified_receipt(
                bad, self.identity, self.repository, self.digest
            )

    def test_signature_policy_is_checked_before_docker_and_actual_metadata_is_verified(
        self,
    ):
        labels = {
            "org.opencontainers.image.source": "https://github.com/owner/app",
            "org.opencontainers.image.revision": self.source,
            "org.opencontainers.image.version": "v0.6.0",
            "io.wood.release.inputs": self.identity["inputs_sha256"],
        }
        config = {"Os": "linux", "Architecture": "amd64", "Config": {"Labels": labels}}
        for kind in ["valid", "signature", "platform", "label", "package"]:
            calls = []

            def command(*args):
                calls.append(args)
                if args[0] == "gh":
                    if kind == "signature":
                        raise subprocess.CalledProcessError(1, "gh attestation verify")
                    return json.dumps(self.results)
                if args[1:3] == ("image", "inspect"):
                    value = copy.deepcopy(config)
                    if kind == "platform":
                        value["Architecture"] = "arm64"
                    if kind == "label":
                        value["Config"]["Labels"][
                            "org.opencontainers.image.revision"
                        ] = "bad"
                    return json.dumps([value])
                if args[1] == "run":
                    return "0.5.1" if kind == "package" else "0.6.0"
                return ""

            with (
                self.subTest(kind=kind),
                patch.object(provenance, "command", side_effect=command),
            ):
                if kind == "valid":
                    provenance.verify(
                        self.identity,
                        self.repository,
                        self.digest,
                        Path(self.temporary.name),
                    )
                else:
                    with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                        provenance.verify(
                            self.identity,
                            self.repository,
                            self.digest,
                            Path(self.temporary.name),
                        )
            self.assertIn("--signer-digest", calls[0])
            self.assertIn("--source-digest", calls[0])
            self.assertIn("--deny-self-hosted-runners", calls[0])
            if kind == "signature":
                self.assertEqual(len(calls), 1)


class RegistryTests(unittest.TestCase):
    def test_multi_image_interruption_recovers_each_missing_digest_independently(self):
        digest = "sha256:" + "a" * 64
        state, writes = {}, []

        def publish(command, **kwargs):
            tag = command[5]
            writes.append(tag)
            if "worker:sha-" in tag and len(writes) == 4:
                raise subprocess.CalledProcessError(1, command)
            state[tag] = digest

        with (tempfile.TemporaryDirectory() as temporary,
              patch.object(registry, "inspect_digest", side_effect=lambda tag: state.get(tag)),
              patch.object(registry, "require_current"),
              patch.object(registry.subprocess, "run", side_effect=publish)):
            for name in ("api", "worker"):
                evidence = Path(temporary) / name
                evidence.mkdir()
                tags = [f"ghcr.io/owner/{name}:v1.0.0", f"ghcr.io/owner/{name}:sha-source"]
                if name == "worker":
                    with self.assertRaises(subprocess.CalledProcessError):
                        registry.promote(f"ghcr.io/owner/{name}@{digest}", tags, digest, True, evidence)
                else:
                    registry.promote(f"ghcr.io/owner/{name}@{digest}", tags, digest, True, evidence)
            self.assertEqual(len(state), 3)
            before = len(writes)
            for name in ("api", "worker"):
                tags = [f"ghcr.io/owner/{name}:v1.0.0", f"ghcr.io/owner/{name}:sha-source"]
                registry.promote(f"ghcr.io/owner/{name}@{digest}", tags, digest, True, Path(temporary) / name)
            self.assertEqual(len(writes), before + 1)
            self.assertEqual(len(state), 4)
            self.assertEqual(set(state.values()), {digest})

    def test_unknown_authentication_never_means_missing_candidate(self):
        with patch.object(
            registry.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, b"", b"401 Unauthorized"),
        ):
            with self.assertRaisesRegex(ValueError, "absence"):
                registry.inspect_digest("ghcr.io/owner/app:candidate")

    def test_recovery_requires_explicit_permission(self):
        with self.assertRaisesRegex(ValueError, "recovery_approved"):
            registry.require_recovery(False)
        registry.require_recovery(True)

    def test_superseded_main_prevents_any_registry_action(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "RELEASE_REF": "refs/heads/main",
                    "APPROVAL_REQUIRED": "true",
                    "APPROVED_REVISION": "a" * 40,
                },
            ),
            patch.object(registry, "is_current", return_value=False),
            patch.object(registry, "inspect_digest") as inspect,
            patch.object(
                sys, "argv", ["release_registry.py", "find", "--evidence", temporary]
            ),
        ):
            self.assertEqual(registry.main(), 1)
            inspect.assert_not_called()

    def test_partial_and_conflicting_release_tags_cannot_be_overwritten(self):
        digest = "sha256:" + "a" * 64
        for existing, recovery in [(digest, False), ("sha256:" + "b" * 64, True)]:
            with (
                tempfile.TemporaryDirectory() as temporary,
                patch.object(registry, "inspect_digest", side_effect=[None, existing]),
                patch.object(registry.subprocess, "run") as mutate,
            ):
                with self.assertRaises(ValueError):
                    registry.promote(
                        "ghcr.io/owner/app@" + digest,
                        ["version", "sha"],
                        digest,
                        recovery,
                        Path(temporary),
                    )
                mutate.assert_not_called()

    def test_authorized_partial_promotion_completes_only_missing_same_digest_tag(self):
        digest = "sha256:" + "a" * 64
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(
                registry,
                "inspect_digest",
                side_effect=[digest, None, digest, None, digest],
            ),
            patch.object(registry, "require_current") as guard,
            patch.object(registry.subprocess, "run") as mutate,
        ):
            registry.promote(
                "ghcr.io/owner/app@" + digest,
                ["version", "sha"],
                digest,
                True,
                Path(temporary),
            )
            self.assertEqual(mutate.call_count, 1)
            self.assertEqual(
                mutate.call_args.args[0][-3:],
                ["--tag", "sha", "ghcr.io/owner/app@" + digest],
            )
            guard.assert_called_once()
            self.assertTrue((Path(temporary) / "promotion.jsonl").exists())

    def test_interrupted_promotion_retains_completed_tag_evidence_without_retry(self):
        digest = "sha256:" + "a" * 64
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(
                registry, "inspect_digest", side_effect=[None, None, None, digest, None]
            ),
            patch.object(registry, "require_current"),
            patch.object(
                registry.subprocess,
                "run",
                side_effect=[None, subprocess.CalledProcessError(1, "docker")],
            ),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                registry.promote(
                    "ghcr.io/owner/app@" + digest,
                    ["version", "sha"],
                    digest,
                    False,
                    Path(temporary),
                )
            rows = (Path(temporary) / "promotion.jsonl").read_text().splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0])["tag"], "version")


class WorkflowTests(unittest.TestCase):
    def test_failure_boundaries_and_digest_smoke_order(self):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/release-container.yml").read_text()
        )
        steps = workflow["jobs"]["release"]["steps"]
        by_name = {s["name"]: s for s in steps}
        names = list(by_name)
        ordered = [
            "Find reusable commit-specific candidate",
            "Build candidate image once",
            "Sign prepared candidate identity",
            "Verify signed candidate identity and actual package",
            "Validate exact Dagster candidate digest",
            "Publish the validated digest under release image tags",
            "Finalize semantic-release and GitHub Release",
        ]
        self.assertEqual(
            [names.index(n) for n in ordered], sorted(names.index(n) for n in ordered)
        )
        for name in ordered:
            self.assertNotIn("always()", by_name[name].get("if", ""))
            self.assertNotIn("continue-on-error", by_name[name])
        self.assertIn(
            "@${{ steps.digest.outputs.digest }}", by_name[ordered[4]]["env"]["IMAGE"]
        )
        for name in [
            ordered[0],
            "Require current approved main before candidate publication",
            ordered[-2],
            ordered[-1],
        ]:
            self.assertIn("--require-current", by_name[name]["run"])
        self.assertEqual(
            by_name["Preserve publication evidence after success or interruption"][
                "if"
            ],
            "always()",
        )

    def test_existing_git_tag_without_completed_release_and_finalization_fail_closed(
        self,
    ):
        text = (ROOT / ".github/workflows/release-container.yml").read_text()
        workflow = yaml.safe_load(text)
        steps = {s["name"]: s for s in workflow["jobs"]["release"]["steps"]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "release-evidence").mkdir()
            for name, script in {
                "python": "exit 0",
                "git": "exit 0",
                "gh": "exit 1",
                "uv": 'if [[ "$*" == *--print-tag* ]]; then echo v0.6.0; else exit 1; fi',
            }.items():
                p = root / name
                p.write_text("#!/bin/bash\n" + script + "\n")
                p.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(root) + ":" + os.environ["PATH"],
                "RUNNER_TEMP": str(root),
                "GITHUB_REPOSITORY": "owner/app",
                "GITHUB_OUTPUT": str(root / "output"),
                "EXPECTED_TAG": "v0.6.0",
            }
            for name in [
                "Determine next version with semantic-release",
                "Finalize semantic-release and GitHub Release",
            ]:
                with self.subTest(stage=name):
                    result = subprocess.run(
                        ["bash", "-e", "-c", steps[name]["run"]],
                        env=env,
                        capture_output=True,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
