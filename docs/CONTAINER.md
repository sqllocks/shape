# Container image

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" CONTAINER
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for CONTAINER
    ```


The Shape CLI ships as a container image for runtimes that take an image and a command line,
such as an Azure Batch Custom activity in Azure Data Factory. It is `python:3.11-slim` with the
Shape wheel (Rust kernel) and the `[azure]` extra (`adlfs`, `azure-identity`, `deltalake`), a
non-root user (`shape`, uid 10001) and no entry point: the command you pass is the whole
command line.

[Run this example](#local-example-0).


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


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
mkdir -p out
export DOCKER_CONFIG="$PWD/docker-config"
docker build --network host --add-host "proxy:$DOCS_PROXY_IP" --quiet --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --secret id=proxy_ca,src="$DOCS_CA_BUNDLE" -t shape-docs-examples .
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/data:/data:ro" -v "$PWD/out:/work" shape-docs-examples \
  shape profile /data/orders.parquet -o /work/orders.shape --json /work/summary.json
test -f out/orders.shape && printf 'Wrote out/orders.shape\n'
```

??? info "Output (exit 0)"

    ```text {.expected}
    sha256:d5cb7c2ce981fc229e9d895e910ca0b5b50e0557100d835f5a4a901f7c3bea42
    {"shape_content_id": "a2946f30e608293e1d58ee69f447666c9dbddd05fd07097913e1c77d45e64811", "written": "/work/orders.shape"}
    Wrote out/orders.shape
    ```
