# BR-02 reconciliation and workflows adoption plan

Preparation for [OP-555](https://projects.woodhost.cloud/work_packages/555), under
initiative 552, Epic 553, Wood Platform project 3 and planning version 26,
**R1 — Platform Branching and Release Standardization**. This document does not
activate a claim, create a remote branch, configure protections, migrate a consumer,
publish a release or deploy infrastructure.

## Verified starting point

On 2026-10-14, fetched `SpencerRWood/workflows` main was
`97ff5f0b8d64b814906ca61df8b4324b3090b140`. OP-554 is Closed; version 26 is open.
The installed Wood executable exposes branching contract version 1 with
trunk/release/integration, feature/feat/fix and durable Story-session commands.
Workflows has no adopted branching/OpenProject mapping, remote development branch
or `.github/release.toml`. Wood reports `CONTRACT_MISSING` for repository info
and undeclared trunk for branching. Contract CI targets main PRs only.

The isolated documentation preparation branch starts at fetched main under the
existing trunk policy. It is not OP-555's implementation branch. Dirty primary
checkout changes and unrelated worktrees remain intact.

## Policy reconciliation

The installed canonical policy already specifies the universal development
lifecycle and automatic pinned publication following approved release integration.
OP-555's packet refers to an older policy and says OP-554 is In progress; neither
statement reflects current inspection. Its planning-only non-goals describe the
prior planning revision. The user's request to reconcile policy and prepare adoption
authorizes this preparation; it does not change OP-554, OP-556 or OP-557.

Repository purpose selects publication outputs, not branching strategy. Existing
trunk/release arrangements remain transitional until deliberate migration.
Routine Story completion requires acceptance, local checks, CI, applicable approval,
integration into the designated development target and OpenProject evidence.
Release/deployment observations stay separate; a runtime exception must be explicit.

Approval of validated development → main release integration authorizes automatic
pinned publication at the approved exact revision without a second publication
approval. Deployment is included only under standing approved repository configuration.
Manual/production exceptions remain. Legacy manual publication, recovery and writer
controls remain effective until their callers migrate; wording changes do not
change executable guards or grant protection bypass.

## Approved setup and adoption sequence

The user approved the concrete development setup and OP-555 activation on
2026-10-14, recorded in OP-555 activity 1414. This bootstrap applies the repository
mapping and validation-only development triggers through a setup PR to development.
Main protections remain unchanged. Broader protection provisioning remains OP-556
scope; the repository-owned Wood validation contract remains an implementation gap.
This authorization does not approve development → main release integration,
publication or deployment. The original preparation sequence below records the
gates and ownership; its setup approval is now supplied by activity 1414.

1. Review this documentation batch and approve its commit/push/PR to main using
   the existing trunk arrangement. The repository currently has no push publishing
   caller; recheck before delivery. Preparation approval does not authorize live
   application publication or deployment.
2. Approve workflows adoption for project 3/version 26 and OP-555. Create development
   from the then-verified protected main revision. Declare integration strategy,
   `integration_branch = "development"`, `release_id = 26` and the actual approval
   reference under root `[tool.wood.branching]`. Declare `project_id = 3` and
   `initiative_id = 552` under `[tool.wood.openproject]`. Do not invent a scope
   approval or derive SemVer from R1.
3. Establish a supported validation contract for this non-package repository.
   Reuse unittest and YAML contract checks. Inspect Wood's supported schema before
   selecting configuration; do not add a fake Python package, empty validation
   list or compatibility wrapper. If unsupported, document the precise gap and
   confirm a supported evidence path using retained repository-native results.
4. Validate development/main PRs and development pushes with read-only permissions;
   install and verify the applicable required checks/protections without broad
   bypass. Protection provisioning belongs to OP-556: coordinate its gate rather
   than duplicate that Story. Remote branch/protection mutation requires concrete
   reviewed authorization first.
5. Inspect live release/branch context and Wood's OP-555 start preview with explicit
   development base/target, approved scope, fetched revision, unique owner and
   isolated worktree. Apply only after all gates match. Never create a main-based
   implementation claim to bypass missing adoption context.

No SemVer is reserved. Public workflow changes must retain major-version compatibility
or use a reviewed new major. Existing pinned callers remain functional until explicit
migration. Preparation creates no tags or floating-reference updates.

## Implementation scope after adoption

| Requirements | Existing interfaces and bounded work |
| --- | --- |
| BR02-01 | Test Story integration/evidence independently from release/runtime observations. Coordinate any needed Wood/agent code change outside the listed affected repositories before expanding scope. |
| BR02-02, BR02-06 | Development validation; release scope, combined checks, semantic-impact dry run, exact source/target approval and hotfix ancestry reconciliation. |
| BR02-03, BR02-08 | Extend `authorize-operation.yml` and exact-head helpers to verify approved release/hotfix main revisions before privileged execution. A main push or caller boolean alone is insufficient. |
| BR02-04, BR02-07 | Reuse existing release/container/configuration/semantic-release interfaces. Inspect multi-image support before choosing additive compatible inputs; introduce no release-profile abstraction. |
| BR02-05 | Reuse Ansible, release resolution and image promotion. Infrastructure/homelab changes are limited to supported opt-in authorization/evidence integration, preserving manual policies. |
| BR02-09 | Reuse OP-517 recovery and immutable container provenance. Preserve release identity across retries and separate deployment failure from publication success; retain run/attempt, authorization, digests and rollback evidence. |
| BR02-10 | Bounded fixture/mocked tests for all fourteen scenarios plus permissions, no-impact, explicit runtime exception and partial-publication identity. |

The fourteen scenarios are: multiple Stories integrate without publication; Story
completion without deployment evidence; failing development CI; approved main
integration triggers one release cycle; CLI/library publication; single-image
publication; multi-image publication; configured automatic infrastructure-dev
deployment; publication without deployment; protected-main authorization; emergency
hotfix and reconciliation/divergence blocking; publication/deployment retries;
breaking/fix/dependency and successive-release calculations; legacy callers.

Inspect actual infrastructure/homelab callers before implementation and record
missing capabilities explicitly. Regression uses fixtures/mocks or isolated
nonproduction targets. No live application publication, infrastructure deployment,
fleet migration or broad bypass is authorized as a test. Retain PLAT-BR-03/PLAT-BR-04
traceability. No closed predecessor claims or artifacts are rewritten.

## Validation and review

The approved setup adds root mapping, updates repository guidance and enables
development validation alongside the documentation reconciliation. Check TOML,
workflow YAML, whitespace, local links and repository-native contracts. Wood's
repository validation remains unsupported until a suitable contract is implemented;
do not claim it passed. Verify bootstrap PR CI before integration and inspect Wood's
matching start preview before activation. Retain the isolated checkout and base
revision for recovery. OP-555 implementation still requires its own reviewed
commit/push/PR batch approval after implementation validation.
