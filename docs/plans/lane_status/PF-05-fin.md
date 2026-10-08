# PF-05-fin — Container image: local build, 500 MB check, CI job (lane/PF-05-fin)

Status: **built and verified locally; one workflow diff for the lead to apply (below); CI has not
yet run the container job on any commit.** Branch `lane/PF-05-fin`, cut from `int/INT-18`
f94de92. Commit prefix `PF-05:`. No §11 or §2.3 edits, no `.github/workflows/*` edits, no tags, no
PR. `$REFENGINE_ROOT` was only read (cloned by `setup_refengine.sh` for `tests/demo/content`).

## Finding: the image was over 500 MB, and the CI check could not see it

Built locally from the unchanged `Dockerfile` (f94de92) with Docker Engine 29.6.2:

| Measure | Before | After (this lane) |
|---|---|---|
| Uncompressed: files in all layers (`scripts/image_size.py`) | **523.2 MB (over)** | **471.0 MB** |
| Flattened rootfs (`docker export \| wc -c`, tar headers included) | 518.9 MB | 466.3 MB |
| Compressed content size (`docker image inspect .Size` under the containerd store) | 180.5 MB | 167.9 MB |

- `docker image inspect --format '{{.Size}}'`, which `container.yml` tests against 500000000,
  is the **uncompressed** size under the classic image store but the **compressed** content size
  under the containerd image store (the default for new Docker Engine installs; this daemon
  reported `containerd-snapshotter=true`). So the existing check passed this 523 MB image at
  "180 MB" here, and would have failed it on a classic-store runner. The gate is not changed;
  the measurement is made store-independent: `scripts/image_size.py` sums the regular-file bytes
  of every layer from `docker save` (plain or gzip layers), which is what the classic store's
  `.Size` reports.
- The 471.0 MB includes a 0.2 MB local-only layer (see "How it was built here"); the
  repository image is about 470.8 MB.

## What changed

- **`Dockerfile`**: the build stage now also installs the wheel with `[azure]` into
  `/install` (`pip install --no-compile --ignore-installed --prefix=/install`), and slims it
  there, where binutils is:
  - removes what a CLI never loads (as before: Arrow Flight, headers, `.pyx/.pxd/.pxi`, test
    suites; new: `pyarrow/_pyarrow_cpp_tests*`);
  - `strip --strip-unneeded` on every shared object (Debian's `dh_strip` treatment for
    libraries), **except** the auditwheel-grafted `*.libs` folders (`numpy.libs`), whose
    patchelf-edited files are the ones strip is known to damage. Saves about 41 MB;
  - pip unpacks a wheel's library aliases as copies (`libarrow_python.so`, `.so.2500`,
    `.so.2500.1.0`, and the same for `libarrow_python_parquet_encryption`): identical `lib*.so*`
    files in one folder become symlinks to the longest name again.
  The runtime stage copies only `/install` into `/usr/local` (no wheel, no toolchain), then
  byte-compiles `shape` and **fails the build** unless every shared object under site-packages
  `dlopen()`s (`ctypes.CDLL`) and `adlfs, aiohttp, azure.identity, cryptography's Rust bindings,
  deltalake, numpy, pyarrow.compute/dataset/parquet/csv/json, shape` import with the Rust
  kernel. `substrait` was considered and kept: `pyarrow/lib*.so` links `libarrow_substrait`.
- **`scripts/image_size.py`** (new, stdlib only): `python3 scripts/image_size.py IMAGE` prints
  the uncompressed size in bytes (per-layer detail on stderr).
- **`tests/integrations/test_container.py`**: the runtime-stage install assertion is replaced
  by the new structure, with the same intent and more: the build stage installs
  `sqllocks_shape-*.whl)[azure]` into the prefix and strips (not `*.libs`); the runtime stage's
  only `COPY` is `--from=build /install /usr/local`; the runtime stage dlopens every shared
  object and imports the Azure packages with the Rust kernel. New test: `image_size.py` on a
  synthetic `docker save` archive (a gzip layer, a plain layer, a non-layer blob, a directory
  entry) sums 4500 bytes. 12 tests, all pass.
- **`docs/CONTAINER.md`**, **`CHANGELOG.md`**: the uncompressed measure, the slimming, the
  measured sizes.

## Verified locally (this session, image `shape:local2` from this branch's Dockerfile)

| Check (the `container.yml` steps, run by hand) | Result |
|---|---|
| Build (`docker build`, both stages) | exit 0, 2 min 40 s (Rust kernel compiled in the image) |
| Size under 500 MB, uncompressed (`image_size.py`, the patched step) | 470,982,894 bytes: PASS |
| `id -u` in the image | 10001 (non-root) |
| `SHAPE_KERNEL=rust` import of `adlfs, azure.identity, deltalake, shape`; `kernel_name() == 'rust'` | ok |
| `shape plugins list --group shape.sources` | 8 sources incl. `abfss`, `delta` |
| D1 (`datasets.py D1`, 200,000 x 6) mounted read-only at `/data`, `shape profile` on `d1.parquet` and `d1.csv` with `--json` (the patched step, distinct output names) | both exit 0; `.shape` and `.json` written |
| Same content ids from the stripped image, the unstripped image and the host's Rust-kernel build | d1.parquet `4e00b9d0…6e372d8d`, d1.csv `a816109f…0cd83e6bfb`: identical in all three |
| Delta through the stripped `deltalake`: write a 1,000-row table in the image, `shape profile /tmp/t` | exit 0, `format: delta`, `version: 0` |

The content-id comparison is an equivalence check of the stripped libraries, not a timing; no
performance number is claimed here.

### How it was built here (local only, nothing committed)

The builder session has Docker binaries but no running daemon; `dockerd` was started by hand.
Egress from build containers passes a TLS-intercepting proxy, so the base image was given the
proxy's CA without touching the repository's `Dockerfile`: a local image
`FROM python:3.11-slim` + `COPY ca-bundle.crt` + `SSL_CERT_FILE/PIP_CERT/CURL_CA_BUNDLE/
REQUESTS_CA_BUNDLE/CARGO_HTTP_CAINFO`, exported as an OCI layout and substituted with
`docker build --build-context "python:3.11-slim=oci-layout://…@sha256:…"`. That adds the 0.2 MB
layer counted above and is not part of the repository image.

## Workflow change for the lead (exact diff; I cannot edit `.github/workflows/*`)

Applies cleanly to f94de92 (`git apply --check` ok). It (1) measures the uncompressed size with
`scripts/image_size.py`, independent of the runner's image store (`python3` is on
`ubuntu-latest` before `setup-python`); (2) adds that script to the trigger paths; (3) stops the
CSV run from overwriting the Parquet run's output (`${f%.*}` is `d1` for both) and also checks the
`--json` summary. Both patched steps were run by hand against `shape:local2` and pass. Note that
this branch's image also passes the unpatched check (471 MB < 500 MB on either store), so the
diff is about the check's correctness, not about getting green.

```diff
--- a/.github/workflows/container.yml
+++ b/.github/workflows/container.yml
@@ -4,9 +4,9 @@
 on:
   push:
     tags: ['v*']
-    paths: ['Dockerfile', '.dockerignore', '.github/workflows/container.yml', 'rust/**', 'src/**', 'pyproject.toml']
+    paths: ['Dockerfile', '.dockerignore', '.github/workflows/container.yml', 'scripts/image_size.py', 'rust/**', 'src/**', 'pyproject.toml']
   pull_request:
-    paths: ['Dockerfile', '.dockerignore', '.github/workflows/container.yml', 'rust/**', 'src/**', 'pyproject.toml']
+    paths: ['Dockerfile', '.dockerignore', '.github/workflows/container.yml', 'scripts/image_size.py', 'rust/**', 'src/**', 'pyproject.toml']
   workflow_dispatch: {}
 permissions: {contents: read}
 env: {IMAGE: ghcr.io/sqllocks/shape}
@@ -18,9 +18,10 @@
       - uses: docker/setup-buildx-action@v3
       - uses: docker/build-push-action@v6
         with: {context: ., load: true, tags: 'shape:ci', cache-from: 'type=gha', cache-to: 'type=gha,mode=max'}
-      - name: Image size is under 500 MB
+      - name: Image size is under 500 MB (uncompressed layers, whatever the image store)
+        # `docker image inspect .Size` is the compressed size under the containerd image store.
         run: |
-          size=$(docker image inspect shape:ci --format '{{.Size}}')
+          size=$(python3 scripts/image_size.py shape:ci)
           echo "image size: $((size / 1000000)) MB"
           test "$size" -lt 500000000
       - name: Runs as a non-root user with the [azure] packages and the Rust kernel
@@ -39,8 +40,8 @@
           mkdir -p "$RUNNER_TEMP/out" && chmod 777 "$RUNNER_TEMP/out"
           for f in d1.parquet d1.csv; do
             docker run --rm -v "$BENCH_DATA_DIR/profile:/data:ro" -v "$RUNNER_TEMP/out:/work" \
-              shape:ci shape profile "/data/$f" -o "/work/${f%.*}.shape" --json "/work/${f%.*}.json"
-            test -s "$RUNNER_TEMP/out/${f%.*}.shape"
+              shape:ci shape profile "/data/$f" -o "/work/${f/./_}.shape" --json "/work/${f/./_}.json"
+            test -s "$RUNNER_TEMP/out/${f/./_}.shape" && test -s "$RUNNER_TEMP/out/${f/./_}.json"
           done
   publish:
     # Only for a version tag; the owner creates tags (plan section 9).
```

`tests/integrations/test_container.py::test_workflow_builds_checks_size_and_profiles_d1_in_the_image`
passes before and after the diff (it checks `-lt 500000000`, `datasets.py D1`, the read-only
mount and `shape profile`). `docs/CONTAINER.md` already describes the patched size step.

## CI status of the container job

`container.yml` (workflow id 372545389) has **never run a job**: its three runs (#1
37123796536, #2 37146121893, #3 37149800416, 2026-10-03) are fork pull requests from an outside
contributor, two concluded `failure` and one `action_required`, each with 0 jobs (not approved
to run). Its triggers are pull requests touching its inputs, `v*` tags and `workflow_dispatch`;
pushes to `build/main-plan`/`int/*` do not run it. To get PF-05's "CI builds the image"
acceptance: apply the diff, then dispatch it (`workflow_dispatch`) on the integration branch, or
let the next same-repo PR touching `Dockerfile`/`src/**` run it. PF-05 stays **pending that CI
run**; everything else in its acceptance passes locally (table above).

## Gate GF report

GF needs: PF-01..PF-05 done; the `pure-wheel` CI job green; the owner's live dry run of the
Fabric pipelines passing (runbook checklist). PF-06 is needed only for G8.

| Item | State | Evidence |
|---|---|---|
| PF-01 cloud sources | tracker: "done (nightly Azurite e2e pending)". **The e2e is now green**: nightly run 37193636580 (schedule, `main` 3cc0e65, 2026-10-04), job `azurite-e2e` success (`pytest -m emulator tests/builtins/test_cloud_sources_azurite.py`). | GitHub Actions |
| PF-02 Fabric notebooks | done (9d0b2f1) | tracker |
| PF-03 UDF package | done (a726dd4) | tracker |
| PF-04 pipeline templates | done (b98dbac) | tracker |
| PF-05 container image | this lane: built, under 500 MB (471.0 MB uncompressed), D1 profiled in the image; **pending one CI run of `container.yml`** (never ran a job, above) | this file |
| `pure-wheel` CI job | **green** on the latest same-repo CI runs: #639 37121550573 (`build/main-plan` 5c91ea5, 2026-10-03) job 111198522513 success; #636 37113750877 job 111176459290 success. The three later CI runs (#640-#642) are fork PRs with 0 jobs. No CI run exists on `int/INT-18` (CI pushes run on `main`, `build/main-plan`, `lane/CI-FIX` only), so "green on every PR" is unverified for the INT-16..INT-18 commits. | GitHub Actions |
| Owner live dry run (O-07) | **not run** (needs the owner's Fabric workspace). | — |

What the owner's live dry run needs: **`integrations/fabric/RUNBOOK.md` section 11, "Owner
live dry-run checklist"** (19 items: lakehouse `shape_demo` and uploads, `shape_setup`,
`shape_profile` day 1/day 2 with the expected pass/fail and drift, Environment `shape-env` on
Runtime 2.0, `shape_profile_spark` and `shape_profile_distributed` (distributed and exact)
parity, pipeline inline install with `_inlineInstallationEnabled = true`, `"kernel": "rust"`
with the platform wheel, `shape_udf` functions, the three gate pipelines `shape_gate_notebook`,
`shape_gate_spark`, `shape_gate_udf` (day 1 succeeds, day 2 fails with violations), timings in
`demo/LIVE_TIMINGS.md`, and any section 10 corrections). Prerequisites are section 1, the
first-run verifications section 10. The checklist also lists the generation checklist, section
12.7 (PF-06, needed for G8, not GF). Synapse and ADF have their own checklists
(`integrations/synapse/RUNBOOK.md` section 7, `integrations/adf/RUNBOOK.md` section 7; the ADF
one needs this image on the Batch pool, i.e. a `v*` tag publishing to GHCR or a self-built
image), but GF names only the Fabric pipelines.

## Checks run in this session (after the changes)

Environment: Python 3.11.15; `~/.venvs/shape` = `pip install -e '.[dev,streaming,advanced]' -e
plugins/shape-domains` (as CI's `test` job; Rust kernel built by maturin); `~/.venvs/shape-fabric`
= `pip install -e '.[dev]' -e plugins/shape-dbt -r tests/demo/fabric/requirements.txt` (as CI's
`fabric-demo` job; its Fabric SDK pins pyarrow<20, so it is kept apart, as PF-03 and PF-06 did);
unixODBC installed; Java 21; the pinned baseline from `benchmarks/vs_refengine/setup_refengine.sh`.
Private `TMPDIR` per run.

| Check | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine scripts/image_size.py` | All checks passed |
| `ruff format --check` (same paths) | 1571 files already formatted |
| `mypy` (project config, strict) | Success: no issues found in 565 source files |
| `mypy --strict scripts/image_size.py` | Success |
| `python scripts/check_user_facing.py` (D-13) | clean |
| `pytest tests/integrations/test_container.py` | 12 passed |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live" --ignore=tests/demo/fabric --ignore=tests/demo/content` | 7 failed, 11725 passed, 24 skipped, 16 deselected (30 min 57 s) |
| `SHAPE_KERNEL=python`, same command in one run | **stopped** by the session's 2-hour background limit at 61%, inside the heavy 48M-row test below; not counted |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live and not heavy"` (same ignores; CI's selection) | 7 failed (the same 7 as below), 11682 passed, 24 skipped, 59 deselected (25 min 27 s) |
| `SHAPE_KERNEL=python pytest -m "heavy and not emulator and not live"` (same ignores) | 1 failed, 42 passed (1 h 22 min); the failure is below |
| `pytest -m "not emulator and not live" tests/demo/fabric tests/demo/content` in `~/.venvs/shape-fabric`, `SHAPE_KERNEL=rust` | 273 passed (9 min 32 s) |
| same, `SHAPE_KERNEL=python` | 273 passed (10 min 0 s) |

Together, the two python-kernel runs cover the same test selection as the single command.

The 7 failures are **pre-existing on `int/INT-18` f94de92** and unrelated to this lane: the same
7 fail with this lane's changes stashed (`git stash -u`; 7 failed, 68 passed). For the lead:

- `tests/test_removed_modules.py::test_removed_module_is_not_importable[history]`: W3-03
  (fcda3a7, `shape bisect`) added `src/shape/history/`, which P0-04's list says must not import.
- `tests/plugins/test_trust_reach.py::test_no_plugin_starts_a_subprocess_or_opens_a_socket`:
  `plugins/shape-fabric/src/shape_fabric/kerberos.py` uses `subprocess` (3 hits).
- `tests/bridge/test_vectors.py::...[report_card]`, `[report_card_read]`: with scikit-learn
  installed (`[advanced]`), `overall_reasons` gains `tier1_adversarial_auc (customers)`, which
  the vectors do not expect.
- `tests/demo_cmd/test_notebook_and_outputs.py` (2): "the semantic model needs the shape-fabric
  plugin" (not installed in CI's `test`-job environment either).
- `tests/generation/test_w1_06_spec_schema.py::test_the_shipped_file_is_what_the_builder_makes`:
  the shipped spec schema differs from what the builder makes.

**Heavy test, python kernel only:**
`tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows` (4090 s) failed
with `SHAPE_KERNEL=python`: peak RSS `{24000000: 558718976, 48000000: 454598656}`, a 19%
difference (the band is 10%). The peak was *lower* at 48M rows. It passed in the
`SHAPE_KERNEL=rust` full run above, and CI's heavy step runs in the default (Rust) kernel. This
lane changes nothing under `src/` or `tests/profile/`. I did not re-run it on the base tree (68
min), so "pre-existing" is by construction here, not observed. Left for the lead; the test is
unchanged.
