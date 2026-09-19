# Centralized GitHub Actions Workflows

`release.yml` is the single stable public reusable release contract for Wood
repositories. It is called with `workflow_call`; project archetypes are internal
implementation concerns, not workflow APIs. Consumers declare their required
capabilities in `.github/release.toml` and should never reference implementation
files in this repository.

Consumers pin the public contract to the stable major version:
`SpencerRWood/workflows/.github/workflows/release.yml@v1`. Development happens
on `main`; a future breaking public contract will be released as `v2`. Consumers
must not use `@main` as their long-term contract. The published `v1` tag is the
current stable public contract.

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

The public workflow checks out the consumer repository with full history, loads
its configuration using `scripts/release_config.py`, installs locked
dependencies, runs only the declared capabilities, and invokes
`semantic-release version --vcs-release` only after every selected check passes.
It has no public workflow inputs. It uses the automatic `GITHUB_TOKEN` for
semantic-release and exposes no outputs or artifacts. Container publishing, deployment, and
infrastructure provisioning remain outside this repository's current scope.
