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
| `SpencerRWood/infrastructure` | [23876717](https://github.com/SpencerRWood/infrastructure/rules/23876717) | Disabled | `validation / validation` |
| `SpencerRWood/homelab` | [23876719](https://github.com/SpencerRWood/homelab/rules/23876719) | Disabled | `validation / validation` |
| `SpencerRWood/portfolio-website` | [23876720](https://github.com/SpencerRWood/portfolio-website/rules/23876720) | Disabled | `validation / validation` |

The consumer rulesets remain configured but disabled. Each consumer normally
uses a branch and pull request, delegates PR checks to `validate.yml@v1`, and
declares its checks in `.github/release.toml`. Its local pre-commit configuration
blocks accidental development commits directly to `main`. The shared CI
validation skips only `no-commit-to-branch` so it can check the default branch.
These safeguards remain useful even though GitHub does not require consumer PR
checks server-side.

The consumer release jobs must keep writing the new version to checked-in
`pyproject.toml`, committing `chore(release): X.Y.Z` to `main`, tagging that
commit `vX.Y.Z`, and publishing the GitHub Release. For infrastructure and
homelab, the tag then starts the existing Ansible deployment. For
portfolio-website, the tag starts immutable GHCR publication. Its currently
open [handoff PR](https://github.com/SpencerRWood/portfolio-website/pull/9)
adds an infrastructure artifact-promotion PR for review. Consumer branch
protection is disabled so release write-back works without a bypass credential
or a different release design.

Infrastructure and homelab retain their separate Renovate update and
automerge policies. Renovate PRs run the same centralized validation as human
PRs, but GitHub does not currently require that check before a consumer PR can
merge. Approved dependency merges still enter the same semantic-release and
deployment path. PostgreSQL compatibility-major updates remain attended.

Reconsider consumer branch protection if additional human developers gain
write access, external contributions become common, multiple automated
identities gain write access, direct-push mistakes recur, GitHub-side
enforcement becomes more valuable than release write-back simplicity, or the
release architecture changes to support protected-branch write-back cleanly.
Branch protection is not required for reusable workflows, Renovate, GHCR,
GitHub Environments, deployments, or semantic-release itself.
