# Exclusive publication writer design (OP-500)

Design baseline: Recovery Verification main
`bde975bebdfbc67bdfca00ba40751fe9f0a33977`, shared Workflows
`74b31b0d4f9f39dfe6a3a0f3c1bbeffca8bfaee4`, expected v0.6.0.
Gate A complete; publication and deployment remain NO-GO. This document and local
code do not authorize App, policy, package, secret or publication mutations.

## Inventory and capability limits

The observed personal repository has only Release and Validate workflows, one
administrator (`SpencerRWood`), no deploy keys, no main rulesets/protection, default
workflow token contents-read, and no repository secrets/environments. Release
alone requests contents/packages/id-token/attestations write on reviewed main;
Validate has contents-read. Release is manual/main/explicit-approval only. These
are observations, not proof that historical refs or previously minted credentials
cannot write. Before an exclusive interval, enumerate executable refs and active
runs, drain other runs/tokens and review the workflows at their actual refs.
The refreshed remote-ref audit found two retained pre-adoption branches with
push-to-main callers using floating v3 (architecture-metadata and release-methodology
documentation branches). They have no manual dispatch and their push trigger is
main-only, so they do not create an additional current main publisher. Preserve
them; record their old content and reject any unreviewed workflow change during
the exclusion interval. Current queued/in-progress run queries returned no runs.

GitHub App enumeration returned 403 for the available credential. Full installed
App inventory, account plan eligibility, package inherited permissions, Actions
repository grants and human/PAT writers remain unavailable. The private GHCR
package is linked to Recovery Verification. Package linkage alone does not prove
its ACL. No credential values were inspected. Existing Infisical patterns are
local process injection and host runtime resolution, not an established GitHub
Actions OIDC release integration.

GitHub documents GHCR authentication with GITHUB_TOKEN or classic PATs. Do not
assume a custom GitHub App installation token can publish GHCR. Package Actions
access is repository-scoped, not workflow-scoped; concurrency is not an ACL.
Consequently a literal single App identity for Git and GHCR is not established.
The feasible design has one dedicated Git writer and a separate ephemeral Actions
registry/attestation principal, controlled as one approved release operation.
If a literal single principal is mandatory, stop: this design does not satisfy it.

## Exact proposed Git identity and branch restrictions

Select a dedicated release GitHub App (proposed purpose: Recovery Verification
release finalization). Its slug, numeric App ID, client ID and installation ID
are UNRESOLVED; do not reuse an unrelated App or substitute fictitious IDs. Install
only on Recovery Verification. Grant repository Contents **write** and automatic
Metadata **read**; no Administration, Workflows, Actions-write, Pull requests,
Secrets, Deployments or organization permissions. Contents covers release
metadata commits/tags and GitHub Releases. Scope each minted token to this one
repository and Contents-write. Review the actual installation/grants first.

Apply separately reviewed active rulesets with no exclusions:

1. Update-only branch rule (`update`) on `refs/heads/main`: bypass list exactly one Integration actor, the
   verified release App ID, mode `always`. No administrator/role/user/other App
   bypass. This permits its normal metadata commit, not other main writers.
2. Destructive-write branch rules (`deletion`, `non_fast_forward`) on `~ALL`: **empty bypass list**.
   Do not put these rules only in the first ruleset: an always-bypass App would
   bypass deletion/force-push protection as well.

3. Tag creation rule on all tags: only the release App may bypass creation restriction.
4. Independent tag update/deletion restrictions on all tags: no bypass actors.
   This prevents changing or deleting an existing tag, including by the App.

Contents-write cannot itself exclude destructive operations; independent
branch/tag restrictions supply that boundary and also affect ordinary cleanup.
The checker verifies only the main update/destructive branch split; tag rules
and installation permissions require their own authority inspection.
Check eligible plan and effective enforcement. Keep existing review/check rules;
the release metadata commit must meet all independent rules. Do not grant an App
administration to manage its own constraints.
Administrators capable of changing policy remain an explicit trust boundary;
unexpected policy changes invalidate release authorization.

`scripts/release_writer_policy.py --rulesets DETAILS.json --app-id VERIFIED_ID`
checks supplied API-shaped full ruleset details for this exact split. It does not
apply rules, verify live freshness, inspect installations or attest GHCR access.
Obtain each full ruleset from its authority and record observation time, IDs,
hashes and exact bypass actors. Passing a proposed JSON file is not live control
evidence. Missing/inactive/wrong-ref/extra-bypass policies fail closed.

## Registry restrictions and attestation principal

Retain the job-scoped GITHUB_TOKEN for GHCR, signed attestations and registry
reads, plus id-token-write for GitHub signing. Do not give the release App package
administration, registry credentials or an assumed packages-write capability.
Disable inherited package permissions only after reviewing effects on readers.
Propose package Actions **write** only for Recovery Verification; remove other
write/admin Actions repositories and explicit human/team write grants, preserving
required read consumers. Package owner/admin cannot be eliminated here: record
that trust boundary and establish an approved no-manual-write interval.

The package UI must supply complete inherited/explicit/Actions ACL evidence;
available REST metadata does not enumerate it. Drain all other package-writing
runs, wait out/revoke previously issued writers under separate authorization,
freeze workflow changes during the interval, audit all executable refs, and
restrict who can dispatch. Even with these controls, a repository write grant
cannot cryptographically select a single workflow. If independently enforceable
single-workflow access is mandatory, publication stays blocked pending a reviewed
isolated publication repository/namespace or supported registry alternative.
Such migration is outside this task; never silently relocate the existing image.

## Authentication integration prepared locally

The reusable container workflow adds explicit `release_git_auth` modes
`github-token` (existing supported v3 contract) and `github-app`. App mode requires
`release_app_client_id`, `release_app_slug` and the named
`release_app_private_key` secret. Missing App settings fail before candidate build;
App mode never falls back to the workflow token. Registry and signing always use
the Actions token. Checkout no longer persists a literal token; Git uses gh's
credential helper and step-scoped GH_TOKEN. Only finalization switches GH_TOKEN
and GITHUB_TOKEN to the selected App token.

The pinned official create-github-app-token action mints the single-repository,
Contents-write token after digest smoke and before final tag promotion; verifies
the returned App slug, then revokes the token at job cleanup (default behavior).
Tokens expire after one hour; promotion and finalization each have ten-minute
timeouts. Interrupted cleanup still has expiry as a bound. This late mint avoids
using a token that expired during a long build. A mint failure can leave an
already-pushed candidate: retain it and obtain narrowly approved recovery.
No token is minted by local tests. Never put tokens in Git remote URLs, generated
artifacts, outputs across jobs, diagnostic arguments or durable files.

The v3 job still declares contents-write to support its existing github-token
mode. For App mode, independent main restrictions must deny that Actions
principal main writes; the declaration is not an App bypass. Fully narrowing its
other-ref Contents authority requires a separately reviewed job/interface split,
not an undocumented permission fallback. This remains a least-privilege limit.

No provider caller is repinned to uncommitted shared code. Proposed delivery:
review/commit/PR this Workflows branch to main; after its separately approved
merge, pin both provider workflow/helper references to that immutable SHA and
select App mode with the reviewed client ID/slug. The provider change must target
its adopted release branch, followed by separately approved release-to-main
integration. Exact eventual-main approval/version is refreshed afterward.

## Infisical and runtime token lifecycle

The key must remain Infisical-backed. A separately reviewed Actions OIDC machine
identity should permit only this repository, approved main ref, protected release
environment, expected workflow identity and exact secret path, with only the
release App key readable. Retrieve inside the release job after authorization;
mask before any output, pass only in memory to the mint action, and clear the
key/token at cleanup. This OIDC/environment/secret binding does not exist yet and
is **not implemented** by passing a secret declaration. Do not copy the key to a
repository secret or assume local Infisical login works on hosted runners. The
current secret interface is a delivery boundary, not a credential provisioning
workflow. Finalize the reviewed injection path before enabling the caller.

## Guards, emergency access and rollback

Keep approved revision/current-main checks before registry writes and finalization;
signed prepared-input provenance, hosted-runner/source/workflow verification;
actual platform/package checks; image@digest runtime smoke; identical version/SHA
digest promotion; direct-child final metadata/input/tag/Release verification.
Candidate and attestations push before smoke require publication approval.

For interruption, preserve run/attempt/job, candidate digest and signed input
receipt, main/source/metadata commit/tag/Release state. No candidate deletion,
overwrite, automatic rebuild or ordinary-rerun repair. Existing/partial state
requires exact recovery approval; different main/digest/provenance stops.

Emergency access is an administrative **policy-change** procedure, not a standing
bypass: stop/drain publication, retain evidence, obtain explicit named approval,
record prior policy/grants and affected refs/digests, apply the minimal temporary
change, perform only separately approved repair, restore and verify controls.
Never remove hard force-push/deletion protections just to make a release pass.
Rollback code through a reviewed revert; invalidate old source approval. Restore
ACL/ruleset state only under approved rollback, retaining read consumer access.
Key/token compromise requires separate revocation/rotation authorization; none
performed here. Deployment/activation/recovery execution remain separate gates.

## Recommendation

NO-GO to apply live controls or publish until the actual App/install/plan and
complete package ACL are reviewed, supported registry exclusivity is accepted,
and Infisical OIDC/credential delivery is specified. GO to review the locally
validated shared code/design. No PR is created without delivery approval. A
subsequent authorization must name exact App permissions/install, both rulesets
and bypass actors, package grants and runtime-secret/OIDC changes. Publication
approval follows verified application of those controls, separately from deployment.

Sources: [GitHub package permissions](https://docs.github.com/en/packages/learn-github-packages/about-permissions-for-github-packages),
[package access settings](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility),
[ruleset rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets),
[App installation authentication](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation),
[official token action](https://github.com/actions/create-github-app-token).
