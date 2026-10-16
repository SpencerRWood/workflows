# Consumer branching policies

Branching is explicit consumer configuration. The shared validation, main-only
semantic publisher, immutable artifact, deployment health and rollback contracts
serve both policies; validation grants no publication or deployment authority.

| Policy | Permanent branches | PR target | Release model | Consumers |
| --- | --- | --- | --- | --- |
| infrastructure | main | main | incremental main changes | infrastructure, homelab |
| application | main, development | development; approved promotion to main | release promotion | existing application consumers |

Set `[branching] policy = "infrastructure"` or `"application"` in
`.github/release.toml`. Existing consumers that omit this additive field retain
the application policy and their current workflow behavior. The helper exposes
`branching_policy`, `integration_branch` and `release_model` outputs. It does not
change repository settings or weaken publication authorization.

Use the matching root Wood mapping: infrastructure consumers declare
`[tool.wood.branching] strategy = "trunk"`; application consumers retain their
approved `integration` mapping, release association and scope. Wood's existing
trunk mode starts work from main without requiring a planning release.

The infrastructure routing caller retains the required check name `pr-routing`.
After checking out the consumer PR head with full history, check out this
repository at the same immutable shared revision and run
`python3 <shared-checkout>/scripts/github_flow_routing.py --event-file
"$GITHUB_EVENT_PATH" --github-output "$GITHUB_OUTPUT"` from the consumer root.
The helper checks the explicit policy, same-repository provenance, supported
short-lived branch names, exact checked-out revisions and current main ancestry.
It fills the existing incident/approval outputs with empty values because
GitHub Flow needs no promotion/hotfix exception. The existing pinned Wood
integration router does not support trunk PRs; application callers continue to
use it unchanged.

Infrastructure caller template:

```toml
# .github/release.toml (retain existing validation/delivery capabilities)
[branching]
policy = "infrastructure"
```

```toml
# pyproject.toml
[tool.wood.branching]
strategy = "trunk"
```

```yaml
# Validation/routing caller triggers; keep the pinned shared validation job.
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
```

Create feature, fix, maintenance and Renovate branches from fetched main and open
PRs into main. Require existing validation/routing checks, prohibit force pushes
and main deletion, and delete merged short-lived branches. Keep release triggers
on main only. Preserve each consumer's existing automatic/manual deployment
selection, environment controls, concurrency, health checks and rollback.

For a migration that must not publish or deploy, GitHub's workflow-disable API
can temporarily hold the repository's release workflow while its reviewed PR
merges. Retain validation and protections; record the prior active state, verify
no release job ran, and re-enable immediately afterward. Re-enabling does not
replay the held main push. This is a migration operation, not a permanent gate.
Never retire an integration branch until its commits are ancestors of main and
open PRs/worktrees/claims are reconciled. Preserve active local worktrees.

Application callers require no configuration changes. Their development PRs,
release readiness, main promotion authorization and publication safeguards remain
unchanged. Infrastructure runtime environments named dev/development are unrelated
to Git branch policy and remain supported.
