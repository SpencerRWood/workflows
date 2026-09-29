# Main branch policy

This is an intentional single-developer policy. `workflows` is the high-trust
CI/CD control-plane repository: changes there affect multiple consumers. Its
`main` ruleset is active and requires a pull request and the GitHub Actions
`validation` check. It blocks force pushes and deletion, requires the PR head
to be current with `main`, and has no bypass actor. One approving review is not
required while `SpencerRWood` is the only eligible human reviewer.

| Repository | `main` ruleset | Enforcement | PR validation check |
| --- | --- | --- | --- |
| `SpencerRWood/workflows` | [23876698](https://github.com/SpencerRWood/workflows/rules/23876698) | Active | `validation` |
| `SpencerRWood/infrastructure` | [23876717](https://github.com/SpencerRWood/infrastructure/rules/23876717) | Active | `validation / validation` |
| `SpencerRWood/homelab` | [23876719](https://github.com/SpencerRWood/homelab/rules/23876719) | Active | `validation / validation` |
| `SpencerRWood/portfolio-website` | [23876720](https://github.com/SpencerRWood/portfolio-website/rules/23876720) | Disabled | `validation / validation` |

Infrastructure and homelab require the Actions `validation / validation` check
from the GitHub Actions integration before a PR can merge. Their rulesets
require a pull request, block force pushes and deletion, and have no bypass
actor. The shared validation workflow remains the sole required check.

These two repositories use `release.tag_merged_commit = true`. They are
configuration repositories, not Python packages. Semantic-release calculates
the next version from commits, tags the validated merged `main` commit, and
publishes a GitHub Release without writing to `main`. The Git tag is their
version source. Deployment checks out that exact tag. Other consumers retain
the earlier version-file write-back contract until they explicitly opt in.

Infrastructure and homelab retain separate Renovate package rules, but both
use GitHub platform auto-merge for eligible PRs. GitHub holds the merge until
required validation succeeds. PostgreSQL compatibility-major updates remain
manual. Portfolio-website's prepared ruleset remains disabled because its
release path has not yet adopted a protected-branch-compatible design.
