# Approved container publication and recovery

The integrated container workflow's approval-gated interface requires an exact
`approved_revision`, matching checkout and current remote `main`. Keep
`require_approval: true`. Manual approval of a source merge does not authorize
publication. A new source, workflow pin, version or build identity needs new
review. Existing pinned consumers are unchanged; consumers adopting this immutable
workflow revision must grant `id-token: write` and `attestations: write` in addition
to contents/packages permissions. No floating major tag is changed by this work.

Version preparation may change declared TOML version fields, the root package
version in `uv.lock`, and the configured changelog. Any dependency resolution
change requires a reviewed source commit before publication; it cannot enter the
candidate through release-time lock regeneration.

## Exclusive writers during publication

Before dispatch, establish and retain an active GitHub branch ruleset targeting
`refs/heads/main` with **Restrict updates**, **Restrict deletions**, and **Block
force pushes**. Only the specifically reviewed release GitHub App may bypass
restricted updates, in `always` mode. Human administrators, merge queues,
dependabot/automerge, other apps and workflows must have no bypass. Limit that
app's credential to this approved release run, using the existing secret resolver;
do not distribute it to other workflows. The selected release credential must be
able to make semantic-release's normal metadata commit through this rule; if the
current GITHUB_TOKEN cannot be the sole permitted principal, publication is
blocked until a separately reviewed credential arrangement exists.

Record ruleset ID, active policy and exact bypass actors with the source approval.
Keep this restriction active from dispatch through final Git/Release verification.
Do not disable protection to make a failed push succeed. Establish a sole GHCR
writer for the image namespace over the same interval. GHCR tag updates do not
offer compare-and-swap: pre-write checks alone cannot prevent another writer from
racing them. Repository admins with authority to alter policy remain a trust
boundary; unexpected policy changes invalidate publication approval.

Configuring/releasing this temporary restriction requires separate operational
authorization. This workflow does not alter rulesets or credentials. Its strict
head checks run before candidate publication/reuse, each missing final image tag
write and semantic-release finalization. They never update the checkout to a newer
main. Concurrency serializes release runs, but is **not** a lock against unrelated
main or registry writers.

## Signed build identity

For approval-gated publication, build from a retained snapshot of explicit Git
source files plus prepared metadata. Ignored runtime files, credentials and `.venv`
are excluded. Missing/symlink/escaping inputs fail closed. A custom GitHub artifact
attestation binds the registry manifest digest to:

- approved source commit M and exact reusable workflow SHA;
- stable semantic version, platform list and package identity;
- Dockerfile, prepared uv.lock and complete snapshot path/content/mode hashes;
- run ID/attempt and Docker Buildx build metadata.

Buildx emits maximum available provenance and SBOM attestations. The workflow also
uses GitHub's immutable-pinned attestation action for SLSA provenance and the
custom receipt. Receipts/bundles, image metadata, promotion observations and
available build records are retained as run artifacts, including on failure. Their
retention must be extended/exported under the release evidence policy before
expiry; missing evidence is not verification.

Before accepting a new or cached image, `gh attestation verify` requires the
expected reusable signer workflow/digest, source repository/digest/main ref,
predicate type and GitHub hosted runner. Then the helper matches signed input
identity/digest and checks the actual image's platform, OCI source/revision/version/
input labels and installed package version using a network-disabled read-only
container. Signature verification is mandatory; labels or a candidate tag alone
are not provenance. An unavailable registry/attestation service fails closed.
Verify the exact registry digest in Dagster/PostgreSQL smoke. No real recovery
jobs, schedules, sensors, production secrets or mounts are used by these checks.

Publication approval includes pushing the candidate and its attestations **before**
runtime smoke. Final version and SHA tags must resolve to that same verified
digest. Existing tags are inspected first; a conflict is never overwritten.

M identifies the source/prepared build. Semantic-release may subsequently create
metadata/tag commit F. Verify F is a direct child of M, its files match the attested
prepared identity, remote main/tag point to F, and the GitHub Release is published
and not a prerelease/draft. Record both M and F; do not claim the image was built
from F just because the final Git tag targets F.

## Partial publication: inspect first, authorize separately

`recovery_approved` defaults false. Run reruns and discovery of an existing
candidate or partial final image tags stop without explicit recovery authorization.
Setting the input records previously granted operator authorization; it does not
grant that authority itself. A fresh approval must name source/version/digests,
the failed run and the exact recovery mutations. No automatic rebuilding, retagging,
deletion, new version generation or operational rollback is permitted.

| Observed state | Required next action |
| --- | --- |
| Candidate exists, smoke failed/interrupted | Preserve candidate/build/attestation receipts; diagnose exact digest. Separately authorize same-input reuse and failed smoke retry; missing provenance blocks reuse. |
| Only one version/SHA tag exists | Compare all tags with verified digest. With explicit recovery approval, complete only the missing same-digest tag. Conflict stops before any write. |
| Image tags exist, finalization failed | Preserve logs, refs and digest. Inspect main, metadata commit and remote tag before proposing a narrowly authorized continuation. |
| Git tag exists, Release missing/draft | Stop without registry or Git mutations; ordinary workflow rerun does not repair it. Verify M/F/digest identity and prepare separately authorized finalization at that existing tag, using the supported semantic-release/GitHub path. Do not create another version or move the tag. |
| Main advanced | Abort. Do not follow newer main, reuse an old approval or overwrite refs. Reconcile the new source in a new review. |
| Provenance/registry conflict or lookup unavailable | Stop and preserve evidence. Neither an access failure nor a missing receipt proves absence or permits overwrite. |

Successful publication is not deployment authorization. Verify tag/Release and
version/SHA digest equality plus provenance/runtime receipts before proposing
deployment. Preserve schedules/sensors disabled and retain operational acceptance
requirements until separately approved execution supplies real evidence.

## Planned Recovery Verification caller integration

After this remediation is separately approved and merged, update
`SpencerRWood/recovery-verification`'s `.github/workflows/release.yml` in a focused
caller PR. Replace both the reusable `release-container.yml` reference and
`workflow_revision` input, currently pinned to
`9307f9b80141f4ed9c463af360a1b13a49ca3353`, with the same verified immutable
merged workflow SHA. Keep the main-only manual dispatch and
`require_approval: true` guards. Add a required string dispatch input
`approved_revision`, pass it to the reusable workflow, and add an optional boolean
`recovery_approved` input defaulting false, also passed through. Retain
contents/packages write permissions and grant id-token/attestations write for
the signed receipt. Validate caller YAML and provider contracts on the caller PR.

The caller update changes the provider's main revision and invalidates any earlier
source-specific publication approval. Recompute semantic-release on the reviewed
new main. Use a `ci:` commit for the caller-only update; the previously observed
`v0.6.0` remains an expectation from the approved feature history, not a reserved
version or authorization to publish. Keep provider implementation, package
versions and Git tags untouched during preparation. The old unpublished local
Linux image cannot substitute for the later attested prepared-version candidate.

Caller integration, sole main/GHCR writer controls, fresh source/version/collision
review and explicit publication approval remain Gate B prerequisites. This shared
workflow PR alone does not satisfy them or the separate deployment and operational
acceptance gates.

Official contract references:
[GitHub attestation verification](https://cli.github.com/manual/gh_attestation_verify),
[GitHub attest action](https://github.com/actions/attest).
