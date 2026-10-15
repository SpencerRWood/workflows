"""Real Git snapshots and signed-receipt policy for a static Node image."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import candidate_provenance as provenance


class NodeProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "consumer"
        shutil.copytree(ROOT / "tests/fixtures/node-only", self.root)
        (self.root / "Dockerfile").write_text("FROM scratch\n")
        (self.root / ".gitignore").write_text("secret.env\n")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "feat: static Node application")
        self.environment = {
            "SOURCE_REVISION": self.git("rev-parse", "HEAD"),
            "WORKFLOW_REVISION": "b" * 40,
            "RELEASE_TAG": "v0.1.0",
            "GITHUB_REPOSITORY": "owner/node-fixture",
            "DOCKERFILE": "Dockerfile",
            "BUILD_CONTEXT": ".",
            "PLATFORMS": "linux/amd64",
        }
        self.snapshot = Path(self.temporary.name) / "snapshot"

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True,
                                       stderr=subprocess.PIPE).strip()

    def prepare(self):
        return provenance.prepare(self.root, self.snapshot, self.environment)

    def test_snapshot_preserves_node_source_without_python_project_or_version_rewrite(self):
        (self.root / "secret.env").write_text("ignored runtime secret")
        identity = self.prepare()
        self.assertEqual(identity["consumer_type"], "node")
        self.assertEqual(identity["version"], "v0.1.0")
        self.assertEqual(identity["package_version"], "0.0.0")
        self.assertEqual(identity["lockfile"]["path"], "frontend/package-lock.json")
        self.assertEqual(identity["lockfile"]["sha256"], hashlib.sha256(
            (self.root / "frontend/package-lock.json").read_bytes()).hexdigest())
        self.assertFalse((self.snapshot / "uv.lock").exists())
        self.assertNotIn("project", tomllib.loads((self.snapshot / "pyproject.toml").read_text()))
        self.assertFalse((self.snapshot / "secret.env").exists())
        self.assertEqual(self.git("status", "--porcelain"), "")
        for item in identity["files"]:
            self.assertEqual((self.snapshot / item["path"]).read_bytes(),
                             (self.root / item["path"]).read_bytes())

    def test_node_version_rewrites_and_untracked_source_are_rejected(self):
        for filename in ("frontend/package.json", "frontend/unapproved.js"):
            path = self.root / filename
            original = path.read_bytes() if path.exists() else None
            path.write_text('{"name":"node-only-release-fixture","version":"0.1.0"}')
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self.prepare()
            if original is None:
                path.unlink()
            else:
                path.write_bytes(original)

    def test_even_coordinated_manifest_and_lock_rewrites_are_rejected(self):
        for filename in ("package.json", "package-lock.json"):
            path = self.root / "frontend" / filename
            document = json.loads(path.read_text())
            document["version"] = "0.1.0"
            path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "unapproved source"):
            self.prepare()

    def test_static_image_verification_requires_receipt_labels_and_healthcheck_not_python(self):
        identity = self.prepare()
        repository, digest = "ghcr.io/owner/node-fixture", "sha256:" + "c" * 64
        receipt = {"identity": identity, "digest": digest, "run_id": "12",
                   "build_metadata": {"containerimage.digest": digest}}
        results = [{"verificationResult": {"statement": {
            "predicateType": provenance.PREDICATE,
            "subject": [{"name": repository, "digest": {"sha256": digest[7:]}}],
            "predicate": receipt}}}]
        config = {"Os": "linux", "Architecture": "amd64", "Config": {
            "Healthcheck": {"Test": ["CMD", "check"]}, "Labels": {
                "org.opencontainers.image.source": "https://github.com/owner/node-fixture",
                "org.opencontainers.image.revision": identity["source_sha"],
                "org.opencontainers.image.version": "v0.1.0",
                "io.wood.release.inputs": identity["inputs_sha256"]}}}
        evidence = Path(self.temporary.name) / "evidence"
        evidence.mkdir()
        for variation in ("valid", "wrong-source", "no-healthcheck", "wrong-receipt"):
            observed = copy.deepcopy(config)
            signed = copy.deepcopy(results)
            if variation == "wrong-source":
                observed["Config"]["Labels"]["org.opencontainers.image.revision"] = "d" * 40
            if variation == "no-healthcheck":
                del observed["Config"]["Healthcheck"]
            if variation == "wrong-receipt":
                signed[0]["verificationResult"]["statement"]["predicate"]["identity"]["consumer_type"] = "python"
            with self.subTest(variation=variation), patch.object(provenance, "command",
                    side_effect=[json.dumps(signed), "", json.dumps([observed])]) as calls:
                if variation == "valid":
                    provenance.verify(identity, repository, digest, evidence)
                else:
                    with self.assertRaises(ValueError):
                        provenance.verify(identity, repository, digest, evidence)
                self.assertFalse(any(call.args[:2] == ("docker", "run") for call in calls.call_args_list))
