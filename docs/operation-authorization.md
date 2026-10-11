# Opt-in manual release operations

`require_approval=true` and `approved=true` enable an explicit manual main-branch
publication/deployment contract. The shared `authorize-operation.yml` rejects any
other event, ref or approval before privileged work. Callers must require their
own separately authorized dispatch and always enable the shared check. Approval
to publish does not approve deployment. Inputs default to the prior behavior for
non-adopters, preserving major-version call compatibility.

The supported publisher and Ansible deployment contracts depend on authorization;
publication continues to require normal validation and preserves artifact identity
and partial-release recovery. Ansible retains its existing immutable release,
health checks, automatic rollback and state handling. No rollback step was edited.

Pin publisher/validation `workflow_revision` to the same immutable source SHA as
the reusable workflow reference. Helpers remain outside the consumer workspace;
the consumer commit cannot substitute its own helper source. Release validation
calls the local reusable validation at the same source revision. This supported
input replaces the invalid `job.workflow_sha` expression on these paths.

OP-499's local branch references must be replaced by the reviewed shared merge SHA
before caller integration. Merging this repository does not publish a workflow
major tag. Old v1/v3 refs and unrelated callers are unchanged; opt-in controls are
not a claim that all fleet publication has been disabled.
