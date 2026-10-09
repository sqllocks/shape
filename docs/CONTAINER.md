# Container image

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


The Shape CLI ships as a container image for runtimes that take an image and a command line,
such as an Azure Batch Custom activity in Azure Data Factory. It is `python:3.11-slim` with the
Shape wheel (Rust kernel) and the `[azure]` extra (`adlfs`, `azure-identity`, `deltalake`), a
non-root user (`shape`, uid 10001) and no entry point: the command you pass is the whole
command line.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

- The working directory is `/work`, writable by the `shape` user. Mount a host directory there
  to keep outputs; mount inputs read-only at `/data`.
- The exit code of `shape` is the container's exit code, so a pipeline activity fails when the
  command fails.
- Cloud inputs work inside the image without extra packages: `shape profile
  abfss://<container>@<account>.dfs.core.windows.net/<path>`. Authentication is described in
  [plugins/cloud-sources.md](plugins/cloud-sources.md); on Azure Batch, the pool's managed
  identity is picked up by `DefaultAzureCredential`, and a service principal can be passed with
  `-e AZURE_CLIENT_ID -e AZURE_TENANT_ID -e AZURE_CLIENT_SECRET`.
- `shape plugins list` and `shape plugins doctor` work as on any install; add third-party
  plugins in a derived image with `pip install`. Domains are plugins: the generate-then-check
  gate of `integrations/adf/` needs `sqllocks-shape-domains` in a derived image (see its runbook).

## How the image is built and published

`.github/workflows/container.yml` builds the image on every change to its inputs, requires its
uncompressed size (the files in all its layers, measured by `scripts/image_size.py` from
`docker save`, so the result does not depend on the runner's image store) to stay under 500 MB,
checks that it runs as a non-root user with the Rust kernel and the
Azure packages, and profiles a generated 200,000-row dataset mounted from the host inside it
(both Parquet and CSV). A version tag (`v*`) additionally publishes `ghcr.io/sqllocks/shape`
with the version and `major.minor` tags and a build-provenance attestation. Nothing is published
from a branch or a pull request.

To stay under that size, the build stage installs the wheel and its dependencies into a prefix,
removes what a CLI never loads (Arrow Flight, headers, Cython sources, test suites), strips the
shared objects with `strip --strip-unneeded` (not the auditwheel-grafted `*.libs` folders) and
turns the copied library aliases back into symlinks; the runtime stage copies only that prefix
and fails the build if any shared object no longer loads. Measured on 2026-10-04: 471 MB
uncompressed (523 MB before stripping), 168 MB compressed.
