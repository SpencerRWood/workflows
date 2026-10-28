## Development lifecycle

Use root [tool.wood.branching]: feature branches and Story PRs start from and target development. Development validates without publication/deployment. Main requires a separately approved release PR and pinned release workflow. Emergency hotfixes require incident/approval references and reconciliation into development.

# Repository Guidance

## Purpose and boundaries
- Maintain reusable GitHub Actions release, validation, container, and Ansible deployment contracts.
- Workflow inputs, outputs, permissions, and major tags are public interfaces for consumer repositories.

## Development
- Keep `.github/workflows/` generic; consumer capabilities come from `.github/release.toml` and repository-local configuration.
- Put deterministic contract helpers in `scripts/` and their behavior checks in `tests/`.

## Validation
- Run `python3 -m unittest discover -s tests` for helper contracts.
- Validate changed workflow YAML and exercise a representative consumer for input/output or permission changes.

## Architecture constraints
- Preserve `workflow_call` compatibility within a major version; publish breaking contracts under a new major tag.
- Do not hardcode one consumer's repository, secret names, or deployment target into shared workflows.

## Safety
- Keep release checkout immutable and deployment rollback limited to repository-defined runtime configuration.

## Branching strategy

- This repository uses **integration-based development**. Read root `[tool.wood.branching]`, create short-lived Story branches from fetched `development` and target PRs to `development`. The approved scope is Wood Platform planning version 29, initiative 571, for the shared container promotion merge-method correction and consumer rollout approved on 2026-10-28; record live release and scope verification before claim activation.
- Development PRs and pushes run validation only. A development → protected main release integration requires separate explicit approval and accumulated validation; ordinary Story approval does not authorize publication or deployment.
- Follow the [canonical branching and delivery policy](https://github.com/SpencerRWood/codex-config/blob/main/docs/branching-policy.md), available through the installed codex-config source path. Verify the strategy, release association, fetched base and PR target before implementation and record them in Story evidence. Story completion does not authorize publication or deployment; retain existing review, CI, security and rollback requirements.
