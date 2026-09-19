# Centralized GitHub Actions Workflows

This repository owns the centralized reusable GitHub Actions workflows for the
Wood project repositories.

Reusable workflows will expose their supported contracts with `workflow_call`.
Consuming repositories should ultimately pin to a stable major contract such as
`@v1`; `@main` is not a long-term consumer contract. Development takes place on
`main`, with future breaking workflow contracts released as `v2` and later
major versions.

Release validation, semantic-release, container publishing, and deployment
automation will ultimately live here. Application-specific configuration remains
in each application repository. Infrastructure provisioning will live separately
in the future `infrastructure` repository.

## Reusable release workflows

Every workflow below is reusable through `workflow_call`. Each validates the
consumer checkout before running `uv run semantic-release version --vcs-release`.
The caller must grant `contents: write` and pass `secrets.GITHUB_TOKEN` as the
required `github_token` secret.

| Workflow | Consumer type | Inputs | Additional secrets | Validation |
| --- | --- | --- | --- | --- |
| `python-release.yml` | Python CLI, API client, analytics, or automation project | Optional `working_directory`, `python_version` | None | uv sync, mypy, pytest, pre-commit |
| `python-quality-release.yml` | Python library or quality-focused package | Optional `working_directory`, `python_version`, `coverage_target` | None | uv sync, Ruff lint/format, mypy, pytest, optional coverage, pre-commit |
| `fastapi-service-release.yml` | FastAPI service with Compose configuration | Optional `working_directory`, `python_version` | None | uv sync, mypy, pytest, `docker compose config`, pre-commit |
| `fastapi-react-release.yml` | FastAPI backend plus React frontend | Optional backend/frontend directories and Python/Node versions | None | backend mypy/pytest/build; frontend npm lint/typecheck/test/build; pre-commit |
| `dbt-release.yml` | dbt project | Optional `python_version`, `dbt_parse_enabled` | Optional dbt connection secrets | uv sync, Ruff lint/format, SQLFluff, dbt deps, optional dbt parse, pre-commit |

The dbt connection secrets are `dbt_host`, `dbt_user`, `dbt_password`,
`dbt_database`, and `dbt_schema`; they are required only when
`dbt_parse_enabled` is true. No workflow emits outputs or uploads artifacts.

Future consumer wrappers will be intentionally thin. For example:

```yaml
name: Release

on:
  push:
    branches: [main]

permissions:
  contents: write

jobs:
  release:
    uses: SpencerRWood/workflows/.github/workflows/python-release.yml@v1
    secrets:
      github_token: ${{ secrets.GITHUB_TOKEN }}
```

Consumers should use a stable major contract such as `@v1`, never `@main` as
their long-term dependency. No `v1` tag exists yet; it will be created only
after the first consumer migrations are validated.
