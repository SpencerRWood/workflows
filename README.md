# Centralized GitHub Actions Workflows

This repository owns the centralized reusable GitHub Actions workflows for the
Wood project repositories.

Reusable workflows will expose their supported contracts with `workflow_call`.
Consuming repositories should ultimately pin to a stable major contract such as
`@v1`; `@main` is not a long-term consumer contract. Development takes place on
`main`, with future breaking workflow contracts released as `v2` and later
major versions.

Release validation, semantic-release, container publishing, and deployment
automation will ultimately live here. Application-specific configuration remains
in each application repository. Infrastructure provisioning will live separately
in the future `infrastructure` repository.

Workflow interfaces have not yet been designed; this repository intentionally
does not define them yet.
