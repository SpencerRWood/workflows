# Progressive development-branch adoption

The [canonical policy](https://github.com/SpencerRWood/codex-config/blob/main/docs/branching-policy.md)
is owned by codex-config. All active repositories target working branch → development
→ protected main → automatic pinned release execution. Existing trunk and bounded
release mappings remain transitional until approved migration. Shared safeguards
alone do not migrate consumers or install protections. See the
[BR-02 adoption plan](br02-adoption-plan.md).

`validate.yml` uses `workflow_call` and has no main-only validation condition.
It supports development/main PR and development push validation when the caller
opts in. At an approved development adoption, its validation-only caller needs:

```yaml
on:
  pull_request:
    branches: [development, main]
  push:
    branches: [development]
permissions:
  contents: read
jobs:
  validation:
    uses: SpencerRWood/workflows/.github/workflows/validate.yml@<approved-ref>
```

Preserve consumer inputs and validation secret mappings. Keep publication credentials
unavailable to validation-only jobs. Required integration/main rulesets and
checks must also be configured at adoption. A reusable workflow cannot install
caller event triggers or protections; existing main-only callers remain unchanged.
Consumers pinned to `@v1` or `@v3` need a separately approved shared workflow
release/reference update before they receive these changes. This task creates no tags.

`release.yml`, `release-container.yml` and legacy `container-release.yml` allow
publishing jobs only on main push or main workflow_dispatch. Feature/development/release refs,
PR events (including pull_request_target), workflow_run and tag events cannot publish.
The semantic-release configuration generated in runner temporary storage replaces
consumer publishing branch matches with exactly `^main$`; consumer files are untouched.
The head verifier also rejects non-main refs, and the legacy container preparation
helper rejects non-main/nonpublishing events before Git or GitHub access.

Main event/ref eligibility does not establish exact-revision release authorization.
After explicit approval of a validated development → main release integration,
the pinned repository workflow automatically publishes its configured outputs;
no second publication approval is required. Deployment runs automatically only
under approved repository configuration. Existing manual/production exceptions,
rollback protections and legacy manual publication/recovery controls remain until
controlled migration. Ordinary Story integration does not authorize those stages.
BR-02 must enforce approved exact-main execution before privileged work; current
event/ref guards alone do not prove it. Caller migration remains separately scoped.

The v3 artifact gate order is preserved: repository validation, nonpublishing
version determination and metadata preparation, one candidate build, exact digest
and runtime validation, publication of that same digest, then VCS release finalization.
This order prevents a published release tag from preceding required image validation.
Legacy v1 container consumers verify an already published GitHub Release and exact
tag checkout before image publication. Local image builds and nonpublishing version
verification remain distinct from registry publication, tags and deployment.

Recovery Verification retains its approved release mapping until a separately
authorized transition. Keep OP-499 and other existing claims, implementations and
worktrees intact. Tests use isolated fixtures rather than live application releases
or infrastructure deployments. Historical manual controls in
[operation authorization](operation-authorization.md) and
[container publication safeguards](publication-safeguards.md) still apply to their
existing callers; this target architecture does not silently remove them.
