# Main branch protection

After the GitHub Pro upgrade on 2026-09-23, all four repositories have active
rulesets targeting `refs/heads/main`. Each requires a pull request and a passing
GitHub Actions validation check, and blocks force pushes and branch deletion.
No actor can bypass these rules.

| Repository | Active ruleset | Required PR check |
| --- | --- | --- |
| `SpencerRWood/workflows` | [23876698](https://github.com/SpencerRWood/workflows/rules/23876698) | `validation` |
| `SpencerRWood/infrastructure` | [23876717](https://github.com/SpencerRWood/infrastructure/rules/23876717) | `validation / validation` |
| `SpencerRWood/homelab` | [23876719](https://github.com/SpencerRWood/homelab/rules/23876719) | `validation / validation` |
| `SpencerRWood/portfolio-website` | [23876720](https://github.com/SpencerRWood/portfolio-website/rules/23876720) | `validation / validation` |

The required checks are restricted to the GitHub Actions app. `workflows`
requires the PR head to be current with `main`; the other three require passing
validation on the PR head without that extra rebase requirement. The rulesets
require zero approving reviews because `SpencerRWood` is currently the only
eligible collaborator on `workflows`; requiring another approval would block
every workflow change. Add an eligible reviewer and then require one approval
for `workflows` as a separate administration change.

Infrastructure and homelab use Renovate platform auto-merge for their approved
dependency classes. Their required validation checks must pass before
auto-merge. The homelab policy currently includes Docker minor
and major updates; that policy was retained for this tranche and deserves a
separate owner decision. PostgreSQL compatibility-major updates remain attended.

The application image promotion PR requires a manual merge. A future decision
to auto-merge only that PR type needs its own explicit policy and permissions.
