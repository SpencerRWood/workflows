"""Integrated release ordering contracts."""

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import release_registry

ROOT = Path(__file__).parents[1]
INTEGRATED = (ROOT / ".github/workflows/release-container.yml").read_text()
NON_CONTAINER = (ROOT / ".github/workflows/release.yml").read_text()


class ReleaseOrderTests(unittest.TestCase):
    def test_all_publishing_jobs_are_main_only_and_validation_is_branch_agnostic(
        self,
    ) -> None:
        guard = "if: github.ref == 'refs/heads/main' && (github.event_name == 'push' || github.event_name == 'workflow_dispatch')"
        for name in ("release.yml", "release-container.yml", "container-release.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github/workflows" / name).read_text()
                self.assertIn(guard, workflow)
                self.assertLess(workflow.index(guard), workflow.index("runs-on:"))
        validation = (ROOT / ".github/workflows/validate.yml").read_text()
        self.assertIn("workflow_call:", validation)
        self.assertNotIn("github.ref == 'refs/heads/main'", validation)
        self.assertNotIn("docker/build-push-action", validation)
        self.assertNotIn("version --vcs-release", validation)

    def test_release_tagging_shell_uses_the_validated_digest(self) -> None:
        manifest = b'{"schemaVersion":2,"manifests":[]}'
        digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "from pathlib import Path\n"
                "args = sys.argv[1:]\n"
                'root = Path(os.environ["FAKE_DOCKER_STATE"])\n'
                'tag = args[3] if len(args) > 3 else ""\n'
                'if args[:3] == ["buildx", "imagetools", "inspect"]:\n'
                '    if not (root / tag.replace("/", "_")).exists():\n'
                '        print(tag + ": not found", file=sys.stderr); sys.exit(1)\n'
                '    if "--raw" in args: sys.stdout.buffer.write(b\'{"schemaVersion":2,"manifests":[]}\')\n'
                'elif args[:3] == ["buildx", "imagetools", "create"]:\n'
                '    (root / args[4].replace("/", "_")).write_text(args[5])\n'
                "else: sys.exit(2)\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{root}:{os.environ['PATH']}",
                "FAKE_DOCKER_STATE": directory,
                "SOURCE_IMAGE": f"ghcr.io/example/app@{digest}",
                "VERSION_IMAGE": "ghcr.io/example/app:v1.2.3",
                "SHA_IMAGE": "ghcr.io/example/app:sha-abc",
                "EXPECTED_DIGEST": digest,
            }
            # Execute the supported promotion helper with a fake registry and
            # a validated source guard; no live registry or source mutations.
            with (
                patch.dict(os.environ, env),
                patch.object(release_registry, "require_current"),
            ):
                release_registry.promote(
                    env["SOURCE_IMAGE"],
                    [env["VERSION_IMAGE"], env["SHA_IMAGE"]],
                    digest,
                    False,
                    root,
                )
            for tag in ("v1.2.3", "sha-abc"):
                self.assertEqual(
                    (root / f"ghcr.io_example_app:{tag}").read_text(),
                    env["SOURCE_IMAGE"],
                )

    def test_version_and_required_gates_precede_vcs_publication(self) -> None:
        self.assertIn("uses: ./.github/workflows/validate.yml", INTEGRATED)
        self.assertIn("workflow_revision: ${{ inputs.workflow_revision }}", INTEGRATED)
        stages = (
            "Determine next version with semantic-release",
            "Prepare semantic release metadata without a commit or tag",
            "Lock prepared version before building the candidate",
            "Build candidate image once",
            "Verify exact candidate digest",
            "Validate exact Dagster candidate digest",
            "Publish the validated digest under release image tags",
            "Finalize semantic-release and GitHub Release",
        )
        self.assertEqual(
            list(map(INTEGRATED.index, stages)), sorted(map(INTEGRATED.index, stages))
        )
        self.assertIn("version --print-tag", INTEGRATED)
        self.assertIn("uv lock\n", INTEGRATED)
        self.assertNotIn("uv lock --offline", INTEGRATED)
        self.assertIn("git add uv.lock", INTEGRATED)
        self.assertIn("version --vcs-release", INTEGRATED)
        self.assertEqual(INTEGRATED.count("docker/build-push-action@"), 1)
        self.assertIn("needs: [authorization, validation]", INTEGRATED)

    def test_failures_block_publication_and_promotion(self) -> None:
        self.assertIn(
            "if: steps.version.outputs.release_tag != '' && steps.existing.outputs.digest == ''",
            INTEGRATED,
        )
        self.assertIn(
            "if: steps.version.outputs.release_tag != '' && steps.config.outputs.dagster_runtime_validation == 'true'",
            INTEGRATED,
        )
        self.assertIn("if: steps.version.outputs.release_tag != ''", INTEGRATED)
        self.assertNotIn("continue-on-error", INTEGRATED)
        self.assertIn(
            "released: ${{ steps.publish.outputs.released || 'false' }}", INTEGRATED
        )
        self.assertIn("cancel-in-progress: false", INTEGRATED)
        self.assertIn("format('release-{0}-{1}', github.repository, github.ref)", INTEGRATED)

    def test_tested_digest_is_tagged_and_exported(self) -> None:
        self.assertIn(
            "IMAGE: ${{ steps.image.outputs.image_repository }}@${{ steps.digest.outputs.digest }}",
            INTEGRATED,
        )
        self.assertIn(
            "SOURCE_IMAGE: ${{ steps.image.outputs.image_repository }}@${{ steps.digest.outputs.digest }}",
            INTEGRATED,
        )
        self.assertIn("EXPECTED_DIGEST: ${{ steps.digest.outputs.digest }}", INTEGRATED)
        self.assertIn("image_digest: ${{ steps.digest.outputs.digest }}", INTEGRATED)
        self.assertIn(
            "version_image_digest: ${{ steps.image.outputs.version_image }}@${{ steps.digest.outputs.digest }}",
            INTEGRATED,
        )
        self.assertIn('test "$actual" = "$EXPECTED_DIGEST"', INTEGRATED)

    def test_non_dagster_and_non_container_paths(self) -> None:
        self.assertIn("container_publish", INTEGRATED)
        self.assertIn("dagster_runtime_validation == 'true'", INTEGRATED)
        self.assertNotIn("docker/build-push-action@", NON_CONTAINER)
        self.assertNotIn("packages: write", NON_CONTAINER)

    def test_publication_rechecks_version_and_branch(self) -> None:
        final_stage = INTEGRATED[
            INTEGRATED.index("Finalize semantic-release and GitHub Release") :
        ]
        self.assertIn("scripts/release_head.py", final_stage)
        self.assertIn('test "$actual_tag" = "$EXPECTED_TAG"', final_stage)
        self.assertIn("gh api", final_stage)


if __name__ == "__main__":
    unittest.main()
