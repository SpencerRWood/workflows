"""Prepared input identity and signed OCI receipts; never publish or repair images."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import tomllib
from release_head import is_current

PREDICATE = "https://woodanalytics.dev/attestations/container-inputs/v1"
SIGNER = "SpencerRWood/workflows/.github/workflows/release-container.yml"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def fingerprint(files: list[dict]) -> str:
    return hashlib.sha256(canonical(files).encode()).hexdigest()


def inputs(root: Path) -> list[dict]:
    """Only explicit Git source/prepared files enter the isolated build context."""
    names = command(
        "git",
        "-C",
        str(root),
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    )
    result = []
    for name in sorted(set(names.rstrip("\0").split("\0"))):
        path = root / name
        if (
            not name
            or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
            or not path.is_file()
        ):
            raise ValueError(
                "build input is missing, outside the checkout, or a symlink"
            )
        result.append(
            {
                "path": name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mode": path.stat().st_mode & 0o777,
            }
        )
    return result


def python_metadata(root: Path, directory: Path, version: str, by_path: dict) -> tuple:
    """Permit only the existing declared Python version preparation."""
    lock = str(directory / "uv.lock")
    project = tomllib.loads((root / directory / "pyproject.toml").read_text())[
        "project"
    ]
    if project["version"] != version[1:]:
        raise ValueError("prepared package version differs from release version")
    original_lock = tomllib.loads(
        command("git", "-C", str(root), "show", "HEAD:" + lock)
    )
    prepared_lock = tomllib.loads((root / lock).read_text())
    original_packages = [
        p for p in original_lock["package"] if p["name"] == project["name"]
    ]
    prepared_packages = [
        p for p in prepared_lock["package"] if p["name"] == project["name"]
    ]
    if len(original_packages) != 1 or len(prepared_packages) != 1:
        raise ValueError("lockfile must identify exactly one root package")
    if prepared_packages[0]["version"] != version[1:]:
        raise ValueError("prepared lockfile version differs from release version")
    prepared_packages[0]["version"] = original_packages[0]["version"]
    if prepared_lock != original_lock:
        raise ValueError("version preparation changed locked dependencies")
    # Prepared metadata cannot smuggle application changes into the approved build.
    configuration = tomllib.loads((root / directory / "pyproject.toml").read_text())[
        "tool"
    ]["semantic_release"]
    declarations = configuration["version_toml"]
    allowed = {lock}
    changelog = str(
        directory
        / configuration.get("changelog", {})
        .get("default_templates", {})
        .get("changelog_file", "CHANGELOG.md")
    )
    allowed.add(changelog)
    for declaration in declarations:
        name, key = declaration.split(":", 1)
        name = str(directory / name)
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("unsafe version declaration")
        allowed.add(name)
        original = tomllib.loads(
            command("git", "-C", str(root), "show", "HEAD:" + name)
        )
        prepared = tomllib.loads((root / name).read_text())
        before, after = original, prepared
        parts = key.split(".")
        for part in parts[:-1]:
            before, after = before[part], after[part]
        if after[parts[-1]] != version[1:]:
            raise ValueError("prepared version declaration differs")
        after[parts[-1]] = before[parts[-1]]
        if prepared != original:
            raise ValueError("version preparation changed undeclared TOML fields")
    return allowed, {"lockfile": by_path[lock], "package_name": project["name"]}


def prepare(root: Path, snapshot: Path, environment: dict[str, str]) -> dict:
    source, workflow = environment["SOURCE_REVISION"], environment["WORKFLOW_REVISION"]
    if not SHA.fullmatch(source) or not SHA.fullmatch(workflow):
        raise ValueError("source and workflow revisions must be immutable full SHAs")
    if command("git", "-C", str(root), "rev-parse", "HEAD") != source:
        raise ValueError("prepared source differs from approved source")
    version = environment["RELEASE_TAG"]
    if not re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ValueError("stable release version required")
    files = inputs(root)
    by_path = {f["path"]: f for f in files}
    directory = Path(environment.get("WORKING_DIRECTORY", "."))
    dockerfile = environment["DOCKERFILE"]
    context = environment["BUILD_CONTEXT"]
    for name in (dockerfile, context, str(directory)):
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("build paths must be repository relative")
    capabilities = tomllib.loads((root / ".github/release.toml").read_text())
    if "python" in capabilities:
        consumer_type = "python"
        allowed, package = python_metadata(root, directory, version, by_path)
    else:
        consumer_type = "node"
        if capabilities["release"].get("tag_merged_commit") is not True:
            raise ValueError("Node provenance requires tag-at-merge publication")
        node_directory = Path(capabilities["node"]["directory"])
        if node_directory.is_absolute() or ".." in node_directory.parts:
            raise ValueError("Node directory must be repository relative")
        manifest = str(node_directory / "package.json")
        lock = str(node_directory / "package-lock.json")
        manifest_data = json.loads((root / manifest).read_text())
        lock_data = json.loads((root / lock).read_text())
        if any(lock_data.get(key) != manifest_data[key] for key in ("name", "version")):
            raise ValueError("Node manifest and lockfile identity differ")
        # Node release tags do not rewrite application metadata. Every input,
        # including package.json/package-lock.json, must remain the approved tree.
        allowed = set()
        package = {"package_name": manifest_data["name"],
                   "package_version": manifest_data["version"],
                   "manifest": by_path[manifest], "lockfile": by_path[lock]}
    tracked = set(
        command(
            "git", "-C", str(root), "ls-tree", "-r", "--name-only", "HEAD"
        ).splitlines()
    )
    changed = set(
        command("git", "-C", str(root), "diff", "HEAD", "--name-only").splitlines()
    )
    if changed - allowed or (set(by_path) - tracked) - allowed:
        raise ValueError("prepared inputs contain unapproved source changes")
    platforms = sorted(environment["PLATFORMS"].split(","))
    if not platforms or any(
        not re.fullmatch(r"linux/[a-z0-9_]+(?:/[a-z0-9_]+)?", p) for p in platforms
    ):
        raise ValueError("invalid publication platforms")
    if snapshot.exists():
        raise ValueError("build snapshot already exists; preserve it")
    snapshot.mkdir(parents=True)
    for f in files:
        target = snapshot / f["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / f["path"], target)
    if not (snapshot / context).is_dir():
        raise ValueError("build context is unavailable")
    return {
        "schema_version": 1,
        "repository": environment["GITHUB_REPOSITORY"],
        "source_sha": source,
        "workflow_sha": workflow,
        "version": version,
        "platforms": platforms,
        "dockerfile": by_path[dockerfile],
        "consumer_type": consumer_type,
        "build_context": context,
        "inputs_sha256": fingerprint(files),
        "files": files,
        **package,
    }


def verified_receipt(
    results: list, identity: dict, repository: str, digest: str
) -> dict:
    """Accept only gh's cryptographically verified statements, then enforce policy."""
    if not DIGEST.fullmatch(digest):
        raise ValueError("invalid registry digest")
    for result in results:
        statement = result["verificationResult"]["statement"]
        predicate = statement.get("predicate", {})
        subject = {"name": repository, "digest": {"sha256": digest[7:]}}
        if (
            statement.get("predicateType") == PREDICATE
            and subject in statement.get("subject", [])
            and predicate.get("identity") == identity
            and predicate.get("digest") == digest
            and predicate.get("build_metadata", {}).get("containerimage.digest")
            == digest
            and predicate.get("run_id")
        ):
            return predicate
    raise ValueError("missing or inconsistent signed candidate provenance")


def verify(identity: dict, image_repository: str, digest: str, evidence: Path) -> None:
    if not DIGEST.fullmatch(digest):
        raise ValueError("invalid registry digest")
    image = image_repository + "@" + digest
    raw = command(
        "gh",
        "attestation",
        "verify",
        "oci://" + image,
        "--repo",
        identity["repository"],
        "--signer-workflow",
        SIGNER,
        "--signer-digest",
        identity["workflow_sha"],
        "--source-digest",
        identity["source_sha"],
        "--source-ref",
        "refs/heads/main",
        "--deny-self-hosted-runners",
        "--predicate-type",
        PREDICATE,
        "--format",
        "json",
    )
    (evidence / "verified-attestations.json").write_text(raw)
    verified_receipt(json.loads(raw), identity, image_repository, digest)
    expected = {
        "org.opencontainers.image.source": "https://github.com/"
        + identity["repository"],
        "org.opencontainers.image.revision": identity["source_sha"],
        "org.opencontainers.image.version": identity["version"],
        "io.wood.release.inputs": identity["inputs_sha256"],
    }
    for platform in identity["platforms"]:
        command("docker", "pull", "--platform", platform, image)
        config = json.loads(command("docker", "image", "inspect", image))[0]
        actual = config["Os"] + "/" + config["Architecture"]
        if config.get("Variant"):
            actual += "/" + config["Variant"]
        if actual != platform or any(
            config["Config"].get("Labels", {}).get(k) != v for k, v in expected.items()
        ):
            raise ValueError(
                "candidate platform or OCI metadata disagrees with receipt"
            )
        if identity["consumer_type"] == "node":
            healthcheck = config["Config"].get("Healthcheck", {}).get("Test", [])
            if not healthcheck or healthcheck[0] == "NONE":
                raise ValueError("Node candidate must retain its declared HEALTHCHECK")
            # The pinned workflow health-checks this exact digest before signing
            # the receipt. Static Node images need no Python/Node executable.
        elif identity["consumer_type"] == "python":
            verify_python_package(image, platform, identity)
        else:
            raise ValueError("unsupported candidate consumer type")
        (evidence / ("image-" + platform.replace("/", "-") + ".json")).write_text(
            canonical(config)
        )


def verify_python_package(image: str, platform: str, identity: dict) -> None:
    version = command(
        "docker",
        "run",
        "--rm",
        "--platform",
        platform,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--entrypoint",
        "python",
        image,
        "-c",
        "import importlib.metadata,sys; print(importlib.metadata.version(sys.argv[1]))",
        identity["package_name"],
    )
    if version != identity["version"][1:]:
        raise ValueError("candidate package version disagrees with receipt")


def recover_identity(environment: dict, digest: str, evidence: Path) -> dict:
    """Recover only the identity signed by this exact pinned build contract."""
    expected = {"repository": environment["GITHUB_REPOSITORY"],
                "source_sha": environment["SOURCE_REVISION"],
                "workflow_sha": environment["WORKFLOW_REVISION"],
                "version": environment["RELEASE_TAG"],
                "dockerfile": environment["DOCKERFILE"],
                "build_context": environment["BUILD_CONTEXT"],
                "platforms": environment["PLATFORMS"].split(",")}
    if not SHA.fullmatch(expected["source_sha"]) or not SHA.fullmatch(expected["workflow_sha"]):
        raise ValueError("recovery requires immutable source and workflow revisions")
    if not DIGEST.fullmatch(digest):
        raise ValueError("recovery requires a prior candidate digest")
    image_repository = environment["IMAGE_REPOSITORY"]
    results = json.loads(command(
        "gh", "attestation", "verify", "oci://" + image_repository + "@" + digest,
        "--repo", expected["repository"], "--signer-workflow", SIGNER,
        "--signer-digest", expected["workflow_sha"], "--source-digest", expected["source_sha"],
        "--source-ref", "refs/heads/main", "--deny-self-hosted-runners",
        "--predicate-type", PREDICATE, "--format", "json"))
    identities = []
    for result in results:
        identity = result["verificationResult"]["statement"].get("predicate", {}).get("identity", {})
        if all(identity.get(key) == value for key, value in expected.items()):
            verified_receipt([result], identity, image_repository, digest)
            if fingerprint(identity["files"]) != identity["inputs_sha256"]:
                raise ValueError("recovered input fingerprint disagrees with signed files")
            if identity not in identities:
                identities.append(identity)
    if len(identities) != 1:
        raise ValueError("missing or ambiguous signed recovery identity")
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "identity.json").write_text(canonical(identities[0]))
    return identities[0]


def verify_finalized(root: Path, identity: dict) -> None:
    revision = command("git", "-C", str(root), "rev-parse", "HEAD")
    parents = command(
        "git", "-C", str(root), "show", "-s", "--format=%P", "HEAD"
    ).split()
    if (
        revision != identity["source_sha"]
        and (parents != [identity["source_sha"]]
             or fingerprint(inputs(root)) != identity["inputs_sha256"])
    ):
        raise ValueError(
            "final metadata commit is not the direct child with the attested prepared inputs"
        )
    if not is_current("refs/heads/main"):
        raise ValueError("final metadata commit is not current remote main")
    tag = command(
        "git",
        "ls-remote",
        "origin",
        "refs/tags/" + identity["version"],
        "refs/tags/" + identity["version"] + "^{}",
    )
    refs = {line.split("\t")[1]: line.split("\t")[0] for line in tag.splitlines()}
    if (
        refs.get(
            "refs/tags/" + identity["version"] + "^{}",
            refs.get("refs/tags/" + identity["version"]),
        )
        != revision
    ):
        raise ValueError("remote release tag does not match final metadata commit")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", choices=["prepare", "receipt", "verify", "finalized", "capture", "recover"]
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    path = args.evidence / "identity.json"
    try:
        if args.phase == "capture":
            (args.evidence / "build-metadata.json").write_text(
                canonical(json.loads(os.environ["BUILD_METADATA"]))
            )
        elif args.phase == "recover":
            recover_identity(dict(os.environ), os.environ["IMAGE_DIGEST"], args.evidence)
        elif args.phase == "prepare":
            if args.snapshot is None:
                raise ValueError("explicit fresh snapshot path required")
            identity = prepare(
                Path(command("git", "rev-parse", "--show-toplevel")),
                args.snapshot,
                dict(os.environ),
            )
            path.write_text(canonical(identity))
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
                stream.write("inputs_sha256=" + identity["inputs_sha256"] + "\n")
        else:
            identity = json.loads(path.read_text())
            if args.phase == "receipt":
                digest = os.environ["IMAGE_DIGEST"]
                if not DIGEST.fullmatch(digest):
                    raise ValueError("invalid registry digest")
                metadata = json.loads(os.environ["BUILD_METADATA"])
                if metadata.get("containerimage.digest") != digest:
                    raise ValueError(
                        "build metadata does not bind the candidate digest"
                    )
                (args.evidence / "receipt.json").write_text(
                    canonical(
                        {
                            "identity": identity,
                            "digest": digest,
                            "run_id": os.environ["GITHUB_RUN_ID"],
                            "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
                            "build_metadata": metadata,
                        }
                    )
                )
            elif args.phase == "verify":
                verify(
                    identity,
                    os.environ["IMAGE_REPOSITORY"],
                    os.environ["IMAGE_DIGEST"],
                    args.evidence,
                )
            else:
                verify_finalized(
                    Path(command("git", "rev-parse", "--show-toplevel")), identity
                )
    except (
        ValueError,
        KeyError,
        OSError,
        TypeError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"candidate provenance failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
