# Centralized GitHub Actions Workflows

`release.yml` and `validate.yml` are the stable reusable release and PR
validation contracts for Wood repositories. They are called with `workflow_call`; project archetypes are internal
implementation concerns, not workflow APIs. Consumers declare their required
capabilities in `.github/release.toml` and should never reference implementation
files in this repository.

Consumers pin the public contract to the stable major version:
`SpencerRWood/workflows/.github/workflows/release.yml@v1`. Development happens
on `main`; a future breaking public contract will be released as `v2`. Consumers
must not use `@main` as their long-term contract. The published `v1` tag is the
current stable public contract.

`deploy-ansible.yml` is the complementary reusable deployment contract. A
consumer calls it only after a GitHub Release is published, passes an immutable
release tag, its inventory/playbook, a target-specific concurrency group, and a
repository-local health command. It checks out that tag (never a moving branch),
syncs locked tooling, parses and syntax-checks inventory/playbook, applies the
canonical playbook, and runs health checks. A failed apply or health check restores
the last successful GitHub Environment deployment ref once and checks it again.
It restores repository-defined runtime configuration only, never a blind database
rollback.

Deployment runs only on a trusted self-hosted control-node runner. Secret values
remain in runner-local protected files and are sourced only for Ansible; callers
pass only their non-secret paths. Consumers must create the named GitHub
Environment before first deployment so its deployment history provides the
previous-successful rollback target.

## Container publishing

`container-release.yml` is the reusable GHCR publishing contract for application
repositories. Call it after `release.yml` reports `released == 'true'`, passing
the `release_tag` output. It checks out that tag in the caller repository,
verifies the checkout matches the tag and a published, stable GitHub Release,
then builds and pushes the image. A branch commit or draft release cannot be
published through this workflow. Stable `vMAJOR.MINOR.PATCH` tags are supported.
The `v1` major tag is updated only after a backwards-compatible contract
release. Consumers call `release.yml@v1`, `validate.yml@v1`,
`container-release.yml@v1`, and `deploy-ansible.yml@v1`. The immutable
`v1.1.0` tag remains available for consumers that need that exact revision.

The caller must grant `contents: write` to the release job and `contents: read`
plus `packages: write` to the container job. The container job uses its automatic
`GITHUB_TOKEN` to read the release and publish to GHCR. No PAT is needed when the
calling repository and package permit GitHub Actions package access. The image
owner comes from the caller's repository owner and is lowercased for GHCR.

| Input | Required | Default | Meaning |
| --- | --- | --- | --- |
| `release_tag` | yes | — | Published stable semantic release tag, such as `v1.2.3`. |
| `image_name` | no | Caller repository name | Single image name under the caller's GHCR owner. |
| `dockerfile` | no | `Dockerfile` | Repository-relative Dockerfile path. |
| `context` | no | `.` | Repository-relative build context. |
| `build_args` | no | empty | Newline-separated, non-secret Docker build arguments. |
| `target` | no | empty | Optional Dockerfile target stage. |
| `platforms` | no | `linux/amd64` | Comma-separated Docker platforms; the default targets Beelink. |

| Output | Example | Meaning |
| --- | --- | --- |
| `image_repository` | `ghcr.io/spencerrwood/website-portfolio` | Normalized image path. |
| `version_image` | `ghcr.io/spencerrwood/website-portfolio:v1.2.3` | Semantic version tag. |
| `sha_image` | `ghcr.io/spencerrwood/website-portfolio:sha-<full commit SHA>` | Commit tag. |
| `image_digest` | `sha256:...` | Pushed image or manifest-index digest. |
| `version_image_digest` | `ghcr.io/spencerrwood/website-portfolio:v1.2.3@sha256:...` | Deployable, digest-qualified reference. |

For example, a consumer may extend its release wrapper:

```yaml
name: Release and publish container

on:
  push:
    branches: [main]

jobs:
  release:
    uses: SpencerRWood/workflows/.github/workflows/release.yml@v1
    permissions:
      contents: write

  container:
    needs: release
    if: needs.release.outputs.released == 'true'
    uses: SpencerRWood/workflows/.github/workflows/container-release.yml@v1
    permissions:
      contents: read
      packages: write
    with:
      release_tag: ${{ needs.release.outputs.release_tag }}
```

The flow is `semantic release` → `released=true` and `release_tag=v1.2.3` →
`container-release.yml` → `ghcr.io/<owner>/<repository>:v1.2.3` → a future
infrastructure deployment. The workflow also pushes a full commit SHA tag and
provides `version_image_digest` for downstream deployment. Deployment should
use that digest-qualified reference, never a moving `latest` tag. Both image
tags are release identifiers; the workflow refuses to overwrite existing tags.
GHCR permits tag reassignment by other users with package write access, so the
digest is the immutable artifact identity.

Buildx uses BuildKit and GitHub Actions cache. OCI labels record the source
repository, release commit, and version. A normal PR validation run never
calls this workflow or publishes an image.

The canonical contract intentionally has no compatibility mode or repository
name exceptions. Repositories must converge on the quality checks their own
configuration declares. The five repositories currently undergoing substantial
refactors are deferred from migration and do not influence this contract.

## Future consumer wrapper

Every consumer wrapper is intentionally almost identical:

```yaml
name: Release

on:
  push:
    branches: [main]

permissions:
  contents: write

jobs:
  release:
    uses: SpencerRWood/workflows/.github/workflows/release.yml@v1
```

The caller must grant `contents: write`. GitHub automatically provides
`secrets.GITHUB_TOKEN` to the called workflow; it must not be redeclared or
mapped under another name. dbt parsing additionally requires `dbt_host`,
`dbt_user`, `dbt_password`, and `dbt_schema`; `dbt_port` and `dbt_dbname` are
optional.

## Release configuration

Each consumer owns `.github/release.toml`. Schema version 1 has these tables:

| Table | Required fields | Purpose |
| --- | --- | --- |
| Root | `version = 1` | Selects the release configuration schema. |
| `[python]` | none | Python `version` (default `3.14`) and `dependency_group` (default `dev`). |
| `[validation]` | `checks` | A non-empty list of declared validation capabilities. |
| `[coverage]` | `target` when selected | Declares the package/module measured by `pytest-coverage`. |
| `[release]` | `semantic_release = true` | Keeps semantic-release mandatory for this release contract. |
| `[project]` | none | Repository-relative `working_directory` (default `.`). |
| `[build]` | none | `python_package = true` enables `uv build`. |
| `[node]` | all fields when present | Enables locked npm setup and Node checks. |
| `[dbt]` | none | `profiles_example` for the `dbt-parse` capability (default `profiles.example.yml`). |

Supported Python validation capabilities are `ruff`, `ruff-format`, `mypy`,
`pytest`, `pytest-coverage`, `pre-commit`, `docker-compose`, `sqlfluff`,
`dbt-deps`, and `dbt-parse`. `pytest-coverage` requires `pytest` and a
`coverage.target`. `dbt-parse` requires `dbt-deps`; it runs only when the
consumer's `DBT_PARSE_ENABLED` repository variable is `true`, and then requires
the `dbt_host`, `dbt_user`, and `dbt_password` secrets. Optional `dbt_port`,
`dbt_dbname`, and `dbt_schema` secrets map to the corresponding standard dbt
environment variables. A Node
table enables npm validation; its `checks` may contain `lint`, `typecheck`,
`test`, and `build`. The loader rejects invalid TOML, unknown capabilities,
unsafe paths, missing lockfiles, and incomplete capability combinations before
dependency installation.

The shared release parser keeps Conventional Commit semantics: `feat` creates a
minor release, `fix` creates a patch release, and `chore(deps)` creates a patch
release. Other `chore` commits and `docs` commits do not create releases. The
workflow applies this rule to each consumer's existing semantic-release config
at runtime, preserving its version, tag, and publishing settings.

### Python package or CLI

```toml
version = 1

[python]
version = "3.14"

[validation]
checks = ["ruff", "ruff-format", "mypy", "pytest", "pytest-coverage", "pre-commit"]

[coverage]
target = "example_package"

[build]
python_package = true

[release]
semantic_release = true
```

### Python service with Docker Compose

```toml
version = 1

[python]
version = "3.14"

[validation]
checks = ["ruff", "ruff-format", "mypy", "pytest", "docker-compose", "pre-commit"]

[release]
semantic_release = true
```

### Python and React application

```toml
version = 1

[project]
working_directory = "backend"

[python]
version = "3.14"

[validation]
checks = ["mypy", "pytest", "pre-commit"]

[node]
directory = "frontend"
version = "24"
checks = ["lint", "typecheck", "test", "build"]

[build]
python_package = true

[release]
semantic_release = true
```

### dbt project

```toml
version = 1

[python]
version = "3.14"

[validation]
checks = ["ruff", "ruff-format", "sqlfluff", "dbt-deps", "dbt-parse", "pre-commit"]

[dbt]
profiles_example = "profiles.example.yml"

[release]
semantic_release = true
```

## Implementation

The validation workflow checks out the consumer repository and
checks out its own helper implementation at the called workflow's commit into
the runner's temporary directory,
outside the consumer checkout. It then loads the consumer configuration using
`scripts/release_config.py`, installs locked
dependencies, and runs only the declared capabilities. The release workflow
calls that same validation contract before invoking
`semantic-release version --vcs-release`.
It has no public workflow inputs. It uses the automatic `GITHUB_TOKEN` for
semantic-release and exposes `released` and `release_tag` outputs to gate the
consumer's existing deployment job. Container publishing and infrastructure
provisioning are outside this release contract.
