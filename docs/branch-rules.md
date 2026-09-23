# Main branch protection checklist

The four repositories were unprotected on 2026-09-23. GitHub's private-repository
branch protection and ruleset APIs returned HTTP 403 with an upgrade message.
An administrator must enable these rules when the account plan supports private
repository protection. Do not bypass validation to enable auto-merge.

| Repository | Required PR check | Additional review rule |
| --- | --- | --- |
| `SpencerRWood/workflows` | `validation` | Require one approving reviewer; changes affect all consumers. |
| `SpencerRWood/infrastructure` | `validation / validation` | Keep normal review policy compatible with approved Renovate auto-merge. |
| `SpencerRWood/homelab` | `validation / validation` | Keep normal review policy compatible with approved Renovate auto-merge. |
| `SpencerRWood/portfolio-website` | `validation / validation` | Normal application review policy. |

For each `main` branch, require a pull request before merging, require the listed
status check to pass, block force pushes, and block deletion. Select the GitHub
Actions check as the required source when GitHub offers that choice. For
`workflows`, require at least one approving review. Do not add a bypass actor
for the release or deployment workflows.

Infrastructure and homelab use Renovate platform auto-merge for their approved
dependency classes. Configure GitHub to require the validation check before
allowing their auto-merge. The homelab policy currently includes Docker minor
and major updates; that policy was retained for this tranche and deserves a
separate owner decision. PostgreSQL compatibility-major updates remain attended.

The application image promotion PR remains review-gated. A future decision to
auto-merge only that PR type needs its own explicit policy and permissions.
