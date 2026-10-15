# Approved development integration and automatic release

OP-555 / BR-02 (PLAT-BR-03 and PLAT-BR-04) extends the existing reusable workflows.
It does not migrate callers, publish workflow tags, configure live protections,
release applications or deploy infrastructure. The opt-in interfaces below must
be delivered under a reviewed immutable workflow SHA before a consumer adopts them.
Existing callers retain their existing manual and routine-update policies.

## Development and Story completion

An adopted repository declares its approved OpenProject scope in root
`[tool.wood.branching]`. Story branches originate from and integrate into the
configured `development` branch. Development PRs/pushes perform validation only.
Complete Stories after acceptance, validation, required review, development
integration and Wood evidence posting. Publication, image pins, deployment and
runtime observations are separate. An explicit Story requirement for deployed
runtime evidence must remain visible; ordinary completion does not invent it.

This repository has a validation-only `.github/release.toml`, locked test dependencies
and direct `validate.yml` triggers for development/main PRs and development pushes.
It is a uv workspace containing contract helpers, not an installable Python package.
`wood repo validate --json` executes its pytest checks; `wood repo verify --json`
executes actionlint. Semantic publication is disabled in its own capability contract.
The `--validation-only` config-reader option permits real checks without requiring
publication capability; publication callers still require it.

## Release approval and exact revision

Set `release_integration: true` on `release.yml`, `release-container.yml`, or
`deploy-ansible.yml`; pin `workflow_revision` to the same full SHA as the workflow.
`release-containers.yml` always uses this authorization. The shared helper accepts
a main push or an explicitly approved main recovery dispatch and independently verifies:

- The checkout equals the event SHA and current remote main.
- Root integration mapping includes a release ID and approved scope reference.
- Effective main rulesets, or classic protection when no rulesets apply, require
  PR review and CI, including the configured `readiness_check` (default
  `release-readiness`). Classic protection inspection requires administration read;
  unreadable/absent protection fails closed.
- One merged same-repository PR targets main at that exact merge revision; its
  source is the configured development branch for normal release integration.
- A two-parent merge preserves the candidate as its second parent. A one-parent
  merge is accepted only with an identical candidate tree and matching planned
  semantic impact at publication. Preserve conventional commit signals in the
  reviewed release merge message when using squash or rebase.
- The prior main revision is an ancestor of the candidate, blocking unreconciled
  hotfix divergence before the next normal release.
- Required checks pass at the candidate SHA before merge. The readiness check must
  originate from GitHub Actions and satisfy any required app binding.
- Required trusted non-author reviews approve that exact candidate before merge.
  An approving review authorizes release; where no independent review is required,
  a trusted maintainer's premerge `Release-Approval: <full candidate SHA>` comment
  supplies explicit approval. Outstanding change requests fail. Deferrals also
  require a premerge `Release-Deferrals: <sorted comma-separated Story IDs>` comment.

Private evidence may require `release_read_token`: a read-only token scoped to
contents, pull requests, checks and Actions, plus administration read for classic
protection inspection. It is forwarded to release/deployment authorization and
readiness evidence inspection. The job retains PR, reviews,
checks, planning mapping, source/merge SHA and run/attempt in an authorization artifact.
Publication jobs acquire their existing writer permissions only after authorization
and validation succeed. No broad administrator/bot bypass is introduced.

The reusable `release-readiness.yml` runs on main PRs at their exact candidate SHA
with read-only GitHub permissions and pinned workflow/Wood source SHAs. The shared
helper verifies the live OpenProject release and complete/deferred Story scope,
Wood ancestry readiness, accumulated Wood validation, required consumer-owned
`integration_command` and `security_command`, and nonpublishing semantic impact.
It saves candidate/target/scope/run-bound evidence and hashes of retained logs;
authorization downloads and verifies that artifact from the pinned readiness run.
Supply `openproject_api_token` through the caller's approved credential integration
and `OPENPROJECT_URL` as a repository variable. Application checks receive no
GitHub/OpenProject token; supported dbt credentials remain available for validation.
The helper rejects modified or untracked candidate contents after the checks.

Main protection provisioning and consumer adoption remain OP-556/OP-557 work.
The [representative release caller](../tests/fixtures/br02/release.yml) is a fixture;
its sample SHAs and repository-owned integration/security commands must be replaced
on adoption.
The readiness check's full displayed Actions name must match `readiness_check`
and the required ruleset context. Approval of this Story targets development only.

## Outputs, images and retries

CLI/library/configuration consumers retain `release.yml` and existing release
configuration. Container consumers retain `release-container.yml` and its candidate
validation, runtime checks and signed OCI provenance. The new opt-in path tags the
approved main revision without pushing a generated version commit to protected main;
semantic-release still owns version calculation, Git tags and GitHub Releases.
Static container package versions and lockfiles are prepared in isolated build
inputs; signed receipts bind those inputs to the approved source and helper SHA.
Configured semantic-release builds and explicit release assets are preserved.
An isolated rebuild recovers missing assets at the original version; uploaded
digests must match and conflicting assets are never replaced. All six supported
dbt secrets are forwarded through coordinated image/finalization calls.
No-impact history avoids a new release. This change adds no package-registry
publisher or output profile; callers retain their existing configured capabilities.

For multiple images, `release-containers.yml` takes `images`, a JSON array of
`image_name`, `dockerfile`, `context` objects. Paths must be repository-relative;
names are unique; at most sixteen images are allowed. It determines one semantic
tag and runs independent image jobs with `fail-fast: false`, then finalizes the
Git/GitHub Release only after every image succeeds. Each job preserves its own
digest/provenance evidence; no combined success is inferred from one image.

Retries at the exact current approved main SHA reuse valid signed candidate
digests and fill only missing immutable tags. Conflicting digests/tag targets
fail; tags are never retargeted. An existing tag at this exact SHA does not skip
image verification. A partial Git publication recovers signed prepared inputs
before verifying the existing image, then uses the existing OP-517 reconciliation
helper to complete a missing GitHub Release without another version or commit.
An older tag represents no new impact only when its published Release exists and
its target is an ancestor of the approved revision. A superseded main run fails
or skips as its existing contract specifies; obtain a fresh validated run.

## Configured deployment and rollback

Publication-only callers have no deployment job. A standing approved deployment
policy adds a downstream call guarded by `needs.release.outputs.released == 'true'`,
passing the verified immutable `release_tag` and existing target configuration.
The [connected caller fixture](../tests/fixtures/br02/automatic-deployment.yml)
binds deployment to successful publication outputs. The
[deployment fixture](../tests/fixtures/br02/deployment.yml) exposes the same
opt-in authorization to a reusable caller; neither runs a live target. Container
pin promotion retains its existing verified digest inputs and accepts an additive
`infrastructure_base_branch` selector. It never silently changes the consumer's base.

`deploy-ansible.yml` independently verifies release approval and that its checked-out
tag resolves to the approved release SHA in the same repository and has a completed
GitHub Release. Deployment recovery can use that published immutable release after
main advances, provided its source remains an ancestor of main and its original
readiness/approval evidence verifies. A manual retry requires `approved: true` on
a main dispatch. Main-commit deployment retains its existing explicit opt-in in
legacy mode; integration deployment requires the approved published tag.
Retain environment/production approvals
and runner isolation. Automatic rollback requires a repository-owned scoped
`privileged_rollback_command`; it never falls back to applying an entire prior target.
Failure of deployment does not invalidate the published release.

Configured durable deployment metadata retains the source/helper revisions, release,
target, run/attempt, outcome and rollback reference/state. A repeated successful
deployment of the same release preserves its previous known-good reference. A failed
attempt preserves the last successful release; retry the published immutable release
through the existing deployment gate. Do not rerun publication to change identity.

## Emergency hotfix

A reviewed hotfix PR uses a supported `fix/op-*`, `feat/op-*` or `feature/op-*`
branch and supplies both `hotfix_incident` and `hotfix_approval`. Its reviewed PR
body contains matching `Hotfix-Incident:` and `Hotfix-Approval:` lines. The same
exact candidate review/check and main merge gates apply. Back-integrate main into
development with evidence; normal release remains blocked until ancestry is restored.

## Regression evidence

Tests use local Git fixtures, mocked GitHub/registry authorities and safe deployment
metadata/rollback commands. They cover development validation without publication,
protected-main approval, one release identity, CLI/library semantic publication,
single/multiple images, partial recovery, configured deployment and publication-only
callers, emergency hotfix/divergence, version impact/no-impact and legacy opt-in
compatibility. Installed Wood v0.23.0's existing evidence/delivery tests separately
cover Story completion independent of runtime/publication, revision-bound CI and
explicit runtime requirements. Retain their saved log in Story evidence; this Story
does not duplicate or alter Wood/agent lifecycle code. No regression publishes an
application release or deploys a live infrastructure target.

| BR02-10 scenario | Bounded evidence |
| --- | --- |
| 1. Stories integrate into development without publication | `test_br02_scenarios.py::test_01_multiple_stories_integrate_into_development_without_publication` |
| 2. Story completion without deployment | Installed Wood `test_workflow_evidence.py::test_release_evidence_accepts_actual_target_without_publication` for integration and all three Story prefixes |
| 3. Development CI detects failure | Scenario 03 executes failed accumulated validation; authorization tests reject failed/pending/skipped/stale checks |
| 4. Approved main integration produces one release cycle | Scenario 04 executes readiness, authorization and idempotent publication |
| 5. CLI/library GitHub Release publication | Existing `test_release_publish.py` and actual PSR successive tag-only releases |
| 6. Single-image GHCR publication | Scenario 06 executes registry promotion before release, plus signed provenance/registry tests |
| 7. Multi-image publication | Scenario 07 interrupts image publication and recovers missing tags before finalization |
| 8. Configured automatic infrastructure-dev deployment | Scenario 08 executes published-release authorization, deployment and runtime verification against a local fixture |
| 9. Publication without deployment | Scenario 09 publishes without changing fixture runtime |
| 10. Protected-main authorization | Rules, review/check provenance, exact candidate and arbitrary-push rejection fixtures |
| 11. Emergency hotfix and reconciliation | Scenario 11 publishes a hotfix, blocks normal readiness, then back-integrates and verifies readiness |
| 12. Publication/deployment retries | Scenario 12 recovers publication, executes health failure/rollback and retries the original release after main advances |
| 13. Breaking/fix/dependency/successive/no-impact versions | Scenario 13 executes actual PSR successive calculations plus publication/history tests |
| 14. Legacy consumers | Unchanged input defaults, legacy writer/recovery tests and additive caller schema checks |

The older environment-dependent live recovery-consumer matrix remains skipped when
`RECOVERY_GOVERNANCE_CHECKOUTS` is absent. This is disclosed separately from the
portable caller fixtures; no live rollout or full consumer-fleet certification is claimed.
