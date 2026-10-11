# Progressive release-branch adoption

The [canonical policy](https://github.com/SpencerRWood/codex-config/blob/main/docs/branching-policy.md)
is owned by codex-config. This change protects reusable publication jobs centrally;
it does not migrate consumers or change their branch strategy.

`validate.yml` uses `workflow_call` and has no main-only validation condition.
It supports release-target PR and release push validation when the caller opts in.
At a separately approved application adoption, its validation-only caller needs:

```yaml
on:
  pull_request:
    branches: [main, 'release/v*']
  push:
    branches: ['release/v*']
permissions:
  contents: read
jobs:
  validation:
    uses: SpencerRWood/workflows/.github/workflows/validate.yml@<approved-ref>
```

Preserve consumer inputs and secret mappings. Required release-branch rulesets and
checks must also be configured at adoption. A reusable workflow cannot install
caller event triggers or protections; existing main-only callers remain unchanged.
Consumers pinned to `@v1` or `@v3` need a separately approved shared workflow
release/reference update before they receive these changes. This task creates no tags.

`release.yml`, `release-container.yml` and legacy `container-release.yml` allow
publishing jobs only on main push or main workflow_dispatch. Feature/release refs,
PR events (including pull_request_target), workflow_run and tag events cannot publish.
The semantic-release configuration generated in runner temporary storage replaces
consumer publishing branch matches with exactly `^main$`; consumer files are untouched.
The head verifier also rejects non-main refs, and the legacy container preparation
helper rejects non-main/nonpublishing events before Git or GitHub access.

Main eligibility is not release-readiness approval. Existing automatic main-push
release callers still require separate authorization before a merge that would
publish. Trunk main merges keep their existing behavior; agents must observe the
separate publication approval gate. Deployment/promotion consumers require their
existing operational approvals and rollback protections. Those caller changes
remain follow-up work, not a fleet migration in this PR.

The v3 artifact gate order is preserved: repository validation, nonpublishing
version determination and metadata preparation, one candidate build, exact digest
and runtime validation, publication of that same digest, then VCS release finalization.
This order prevents a published release tag from preceding required image validation.
Legacy v1 container consumers verify an already published GitHub Release and exact
tag checkout before image publication. Local image builds and nonpublishing version
verification remain distinct from registry publication, tags and deployment.

Recovery Verification R1 is the first controlled application adoption. Its current
caller validates main PRs and releases main pushes through v3; release-target/push
validation and an approved release mapping are deferred to that adoption task.
Keep OP-499's implementation and worktree intact. Foundation tests use isolated
fixtures, not that live worktree or a live release branch.
