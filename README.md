# Centralized GitHub Actions Workflows

`release.yml` and `validate.yml` are the stable reusable release and PR
validation contracts for Wood repositories. They are called with `workflow_call`; project archetypes are internal
implementation concerns, not workflow APIs. Consumers declare their required
capabilities in `.github/release.toml` and should never reference implementation
files in this repository.

Consumers pin each public contract to its stable major version. Development
happens on `main`; breaking contracts receive a new major tag. Consumers
must not use `@main` as their long-term contract.

| Capability | Public reusable workflow |
| --- | --- |
| Non-container release and validation | `release.yml@v1`, `validate.yml@v1` |
| Integrated container release and PR validation | `release-container.yml@v3`, `validate.yml@v3` |
| Dev image promotion | `promote-container-to-dev.yml@v2` |
| Ansible deployment and release resolution | `deploy-ansible.yml@v1`, `resolve-release.yml@v1` |

The legacy `container-release.yml@v1` remains available for existing callers
while they migrate to the integrated container release. Its filename is the
reverse of the current public container release workflow.
The [main branch policy](docs/branch-rules.md) records the protected consumer
release model.

`deploy-ansible.yml` is the complementary reusable deployment contract. A
consumer calls it only after a GitHub Release is published, passes an immutable
release tag, its inventory/playbook, a target-specific concurrency group, and a
repository-local health command. It checks out that tag (never a moving branch),
syncs locked tooling, parses and syntax-checks inventory/playbook, applies the
canonical playbook, and runs health checks. A failed apply or health check restores
the last successful GitHub Environment deployment ref once and checks it again.
It restores repository-defined runtime configuration only, never a blind database
rollback.

`resolve-release.yml@v1` verifies a requested published release tag or selects
the latest published release when its `ref` input is empty. It returns `ref` to
the consumer's deployment job. Consumers can call it from one `deploy.yml`
that supports both `workflow_call` and `workflow_dispatch`; the repository's
runner, inventory, playbook, and protected local paths stay in that file.

Automated release callers can pass `skip_superseded: true` to
`deploy-ansible.yml@v1` (or through a target wrapper). After target validation
and immediately before apply, it compares the immutable release checkout with
the repository's current default branch head. A superseded deployment exits
successfully without applying or updating deployment metadata. The default is
`false`, so an explicitly requested historical deployment remains available.

Deployment runs only on a trusted self-hosted control-node runner. Secret values
remain in runner-local protected files and are sourced only for Ansible; callers
pass only their non-secret paths. Consumers must create the named GitHub
Environment before first deployment so its deployment history provides the
previous-successful rollback target.

## Optional PR validation commit status

`validate.yml@v1` runs the checks declared in the caller's `.github/release.toml`.
GitHub Actions check runs remain its normal validation result. A caller that
needs to expose that result to a cross-repository integration can also mirror
it to a commit status on the exact pull request head SHA:

```yaml
permissions:
  contents: read
  statuses: write

jobs:
  validation:
    uses: SpencerRWood/workflows/.github/workflows/validate.yml@v1
    with:
      publish_commit_status: true
      status_context: infrastructure-validation
```

Both inputs are optional: `publish_commit_status` defaults to `false`, and
`status_context` defaults to `validation`. Existing callers need no changes or
extra token permissions. The shared workflow uses the caller's token scope;
an opted-in caller grants its `GITHUB_TOKEN`
`statuses: write`; the shared workflow posts `pending` before the configured
checks and then `success`, `failure`, or `error` from the same validation job.
Publishing requires a pull request event and a valid head SHA and context.
The status is an interoperability bridge, not another set of validation checks.
For infrastructure artifact promotion, only the exact dev image PR may merge
automatically after this status succeeds and its diff is reverified. Production
promotion remains manual.

## Container publishing

### Dagster candidate-image runtime gate

Dagster consumers can opt in with no CI database credentials:

```toml
[dagster]
runtime_validation = true
smoke_job = "runtime_smoke_job" # default
grpc_port = 4000               # default
```

The application template owns its compatible Dagster, `dagster-postgres`,
SQLAlchemy, and psycopg2 dependency set and exposes the secret-free smoke job
through `Definitions`. The centralized container workflow owns temporary
PostgreSQL, a standard PostgreSQL-backed `dagster.yaml`, candidate gRPC startup,
the real smoke run, and direct PostgreSQL run/event row checks. The disposable
database uses `pgvector/pgvector:pg16`, retaining PostgreSQL 16 and making the
vector extension available for consumer-owned application smoke fixtures.
Consumers still own extension creation in their disposable schemas/database;
the gate does not weaken run or event-log verification. Infrastructure
owns the deployed code-location configuration. This gate checks the
Dagster/PostgreSQL runtime boundary; it does not exercise application APIs.

For opted-in consumers the workflow builds once to a unique temporary GHCR
candidate tag, runs the image by its immutable digest, and copies that same
digest to the semantic version and commit SHA tags only after validation. It
verifies the final tag digest equals the tested digest and emits that digest to
downstream promotion. A failed runtime check prevents container publication and
Infrastructure promotion. Semantic-release currently creates the GitHub release
first in the legacy v1 wrapper. Container consumers should use the integrated
`release-container.yml@v3` contract described below.

`container-release.yml@v1` is the legacy GHCR publishing contract for application
repositories. Call it after `release.yml` reports `released == 'true'`, passing
the `release_tag` output. It checks out that tag in the caller repository,
verifies the checkout matches the tag and a published, stable GitHub Release,
then builds and pushes the image. A branch commit or draft release cannot be
published through this workflow. Stable `vMAJOR.MINOR.PATCH` tags are supported.
Existing callers can continue using this contract while migrating to the
integrated `release-container.yml@v3` workflow. The `v1` major tag is
updated only after a backwards-compatible contract release. Legacy consumers
call `release.yml@v1`, `validate.yml@v1`,
`container-release.yml@v1`, and `deploy-ansible.yml@v1`. The immutable
`v1.1.0` tag remains available for consumers that need that exact revision.

The legacy caller must grant `contents: write` to the release job and `contents: read`
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

The legacy flow is `semantic release` → `released=true` and `release_tag=v1.2.3` →
`container-release.yml` → `ghcr.io/<owner>/<repository>:v1.2.3` → a future
infrastructure deployment. The workflow also pushes a full commit SHA tag and
provides `version_image_digest` for downstream deployment. Deployment should
use that digest-qualified reference, never a moving `latest` tag. Both image
tags are release identifiers; the workflow refuses to overwrite existing tags.
GHCR permits tag reassignment by other users with package write access, so the
digest is the immutable artifact identity.

### Integrated container release (v3)

Container consumers opt in with `[container] publish = true` in
`.github/release.toml` and call `release-container.yml@v3`. The caller grants
only that release job `contents: write` and `packages: write`; promotion needs
`contents: read`. The container table also accepts `image_name` (default:
repository name), `dockerfile` (default: `Dockerfile`), `context` (default:
`.`), and `platforms` (default: `linux/amd64`). Non-container repositories
continue to call `release.yml@v1` and need no Docker or package write access.

The new sequence is source validation → semantic-release `version --print-tag`
→ prepare local version/changelog metadata and lockfile without a commit or tag → one
commit-specific candidate image → exact digest verification → Dagster
PostgreSQL runtime gate when enabled → release image tags on that same digest →
semantic-release `version --vcs-release` → Infrastructure promotion. **GitHub
Release publication happens only after all required image and runtime gates
pass.** The candidate tag can be reused on retry; both released image tags
must resolve to its digest. The workflow emits that same digest and a
digest-qualified reference for promotion. A failed build or runtime gate leaves
no Git tag or GitHub Release. A failed image tag operation prevents semantic
publication. A failed semantic publication blocks promotion; already created
image tags are retained and can be verified on retry. If semantic-release
pushes its Git tag but GitHub Release creation fails, manual inspection and
recovery of that partial VCS publication is required before another release
attempt. A later Infrastructure promotion failure leaves the valid release
and image intact.

The candidate build includes the locally prepared version metadata and
changelog. Semantic-release commits and tags those same files only after
validation. Release calls are serialized per repository and branch, and the
checkout is rechecked against the remote branch immediately before final
publication.

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
| `[dagster]` | `runtime_validation` when table is present | Enables the candidate-image PostgreSQL runtime gate; `smoke_job` and `grpc_port` default to `runtime_smoke_job` and `4000`. |
| `[container]` | `publish = true` for integrated releases | Selects the v3 container release contract and optional image build settings. |

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

## Containerized Application Dev Deployment

`promote-container-to-dev.yml@v2` connects a published, digest-qualified GHCR
image to one image pin in `infrastructure/environments/dev.yml`. It creates or
reuses an infrastructure PR, waits up to 20 minutes for the trusted
`infrastructure-validation` commit status on its exact head SHA, rechecks the
one-line diff, and squash-merges the dev PR. Infrastructure then performs its
own semantic patch release and Beelink dev deployment. This workflow never
deploys or promotes production.

To onboard another containerized application:

1. Create the application repository and `.github/release.toml`.
2. Use `validate.yml@v3` for PRs and `release-container.yml@v3` for
   integrated semantic and image releases. Set `[container] publish = true`.
3. Grant only the release call `contents: write` and `packages: write`.
4. Add the service definition and `<app>_image_ref` to infrastructure's
   `environments/dev.yml`, initially set to a valid digest-qualified image.
5. Configure runtime secrets, environment, migrations, and health checks in
   infrastructure as required by the service.
6. Call `promote-container-to-dev.yml@v2` after a successful integrated release.
7. Add an application repository secret containing a fine-grained token scoped
   only to `SpencerRWood/infrastructure`: Contents read/write, Pull requests
   read/write, Commit statuses read, and Metadata read. Pass it as
   `infrastructure_token`. Checks and administration permissions are unnecessary.
8. Merge normal application changes. New releases then deploy to dev through
   the infrastructure-owned flow; production promotion stays manual.

The application release wrapper needs only the following promotion job in
addition to its integrated `release` job:

```yaml
  promotion:
    needs: release
    if: ${{ needs.release.outputs.released == 'true' }}
    uses: SpencerRWood/workflows/.github/workflows/promote-container-to-dev.yml@v2
    permissions:
      contents: read
    with:
      infrastructure_repository: SpencerRWood/infrastructure
      image_key: portfolio_website_image_ref
      image_name: portfolio-website
      release_tag: ${{ needs.release.outputs.release_tag }}
      image_repository: ${{ needs.release.outputs.image_repository }}
      version_image: ${{ needs.release.outputs.version_image }}
      image_digest: ${{ needs.release.outputs.image_digest }}
      version_image_digest: ${{ needs.release.outputs.version_image_digest }}
    secrets:
      infrastructure_token: ${{ secrets.INFRASTRUCTURE_PR_TOKEN }}
```

The v2 public inputs are exactly:

| Input | Required | Default |
| --- | --- | --- |
| `infrastructure_repository` | yes | — |
| `infrastructure_base_branch` | no | `main` |
| `image_key` | yes | — |
| `image_name` | yes | — |
| `release_tag` | yes | — |
| `image_repository` | yes | — |
| `version_image` | yes | — |
| `image_digest` | yes | — |
| `version_image_digest` | yes | — |
| `status_context` | no | `infrastructure-validation` |

The required secret is `infrastructure_token`.
`promote-container-to-dev.yml@v2` always targets `environments/dev.yml`;
callers cannot select another manifest. The `@v1` contract remains available
for callers that have not migrated.
The workflow derives the branch `chore/<image_name>-<release_tag>` and the
PR, commit, and squash-merge title
`chore(deps): update <image_name> to <release_tag>` from its validated inputs.
The workflow accepts only the dev environment file and a single top-level
`<app>_image_ref` key. The four
publisher outputs must agree with the release tag, GHCR owner, image name, and
SHA256 digest. A newer dev version or a changed digest for the same version
blocks an older rerun.

## Implementation

The validation workflow checks out the consumer repository and
checks out its own helper implementation at the called workflow's commit into
the runner's temporary directory,
outside the consumer checkout. It then loads the consumer configuration using
`scripts/release_config.py`, installs locked
dependencies, and runs only the declared capabilities. The release workflow
calls that same validation contract before invoking
`semantic-release version --vcs-release`.
Release runs for one repository branch share a concurrency group with
`cancel-in-progress: false`: an active release is allowed to finish, and GitHub
keeps the newest pending run when more commits arrive. Immediately before
semantic-release, the shared workflow compares its validated checkout with the
remote branch and skips a superseded run. The newest pending run then validates
and releases the accumulated changes on main.
It has no public workflow inputs. It uses the automatic `GITHUB_TOKEN` for
semantic-release and exposes `released` and `release_tag` outputs to gate the
consumer's existing deployment job. Container publishing and infrastructure
provisioning are outside this release contract.
# Repository-owned application runtime checks

Container consumers of `release-container.yml@v3` can opt into an additive gate:

```toml
[runtime]
validation = true
check_module = "your_package.runtime_check"
timeout_seconds = 120
```

The module must belong to the consumer and be installed in its candidate image.
The shared helper runs `python -m <check_module>` in the exact verified candidate
digest against disposable PostgreSQL 16. It supplies `RUNTIME_DATABASE_URL` and
an isolated internal Docker network, with no production credentials or volumes.
The consumer owns migrations, startup, authentication and persistence assertions;
a nonzero exit or timeout fails the release before release image tags, Git tags,
or a GitHub Release are published. Candidate tags necessarily exist before the
gate. The total runtime has a configured 10–300 second deadline, followed by
bounded cleanup. Existing Dagster and non-runtime consumers retain their paths;
the two runtime modes cannot be enabled together.

To diagnose a local candidate, pass an immutable local image ID with the explicit
`--allow-local-image` option. Publication continues to require a GHCR digest.
Consumer adoption requires publishing this shared contract to v3 first.
