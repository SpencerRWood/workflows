# Main branch protection

After the GitHub Pro upgrade on 2026-09-23, all four repositories have rulesets
targeting `refs/heads/main`. The `workflows` ruleset is active. The three consumer
rulesets are currently disabled because their semantic-release jobs still push a
generated version commit to `main`; activating PR-only protection without a
release identity would break releases and deployments. No actor can bypass the
active `workflows` ruleset.

| Repository | Ruleset | State | Required PR check when active |
| --- | --- | --- | --- |
| `SpencerRWood/workflows` | [23876698](https://github.com/SpencerRWood/workflows/rules/23876698) | Active | `validation` |
| `SpencerRWood/infrastructure` | [23876717](https://github.com/SpencerRWood/infrastructure/rules/23876717) | Disabled | `validation / validation` |
| `SpencerRWood/homelab` | [23876719](https://github.com/SpencerRWood/homelab/rules/23876719) | Disabled | `validation / validation` |
| `SpencerRWood/portfolio-website` | [23876720](https://github.com/SpencerRWood/portfolio-website/rules/23876720) | Disabled | `validation / validation` |

The configured checks are restricted to the GitHub Actions app. `workflows`
requires the PR head to be current with `main`; the other three are configured
to require passing validation on the PR head without an extra rebase. The rulesets
specify zero approving reviews because `SpencerRWood` is currently the only
eligible collaborator on `workflows`; requiring another approval would block
every workflow change. Add an eligible reviewer and then require one approval
for `workflows` as a separate administration change.

To activate the consumer rulesets, first supply a dedicated release identity
that can push only the generated semantic-release commit through the PR rule,
or change the release process so it no longer pushes to `main`. GitHub rejected
the built-in GitHub Actions integration as a bypass actor for these personal
repositories. Validate the chosen release path before enabling the rulesets.

Infrastructure and homelab use Renovate platform auto-merge for their approved
dependency classes. When their rulesets are active, required validation must
pass before auto-merge. The homelab policy currently includes Docker minor
and major updates; that policy was retained for this tranche and deserves a
separate owner decision. PostgreSQL compatibility-major updates remain attended.

The application image promotion PR requires a manual merge. A future decision
to auto-merge only that PR type needs its own explicit policy and permissions.
