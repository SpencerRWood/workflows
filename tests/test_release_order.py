"""Integrated release ordering contracts."""

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
INTEGRATED = (ROOT / ".github/workflows/release-container.yml").read_text()
NON_CONTAINER = (ROOT / ".github/workflows/release.yml").read_text()


class ReleaseOrderTests(unittest.TestCase):
    def test_release_tagging_shell_uses_the_validated_digest(self) -> None:
        marker = "      - name: Publish the validated digest under release image tags"
        stage = INTEGRATED.split(marker, 1)[1]
        body = stage.split("        run: |\n", 1)[1]
        lines = []
        for line in body.splitlines():
            if line and not line.startswith("          "):
                break
            lines.append(line[10:])
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
                '    if not (root / tag.replace("/", "_")).exists(): sys.exit(1)\n'
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
            result = subprocess.run(
                ["bash", "-e", "-c", "\n".join(lines)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for tag in ("v1.2.3", "sha-abc"):
                self.assertEqual(
                    (root / f"ghcr.io_example_app:{tag}").read_text(),
                    env["SOURCE_IMAGE"],
                )

    def test_version_and_required_gates_precede_vcs_publication(self) -> None:
        self.assertIn("uses: SpencerRWood/workflows/.github/workflows/validate.yml@v3", INTEGRATED)
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
        self.assertIn("needs: validation", INTEGRATED)

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
        self.assertIn("release-${{ github.repository }}-${{ github.ref }}", INTEGRATED)

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
