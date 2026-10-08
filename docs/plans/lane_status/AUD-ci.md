# AUD-ci: continuous integration, release and supply chain audit

Lane `lane/AUD-ci`, from `build/main-plan` at `5c91ea5` (INT-15). Brief: `docs/plans/AUDIT_BRIEF.md`.
Editable area: `scripts/` and their tests. Workflows, `pyproject.toml`, `CODEOWNERS` and
`SECURITY.md` are outside it: every change there is an exact diff below, for the lead.

## Findings

| # | Sev | Where | Finding | Issue | Status |
|---|---|---|---|---|---|
| 1 | high | `.github/workflows/*` (90 `uses:` lines) | Every action is pinned to a mutable tag; `pypa/gh-action-pypi-publish@release/v1` is a branch. The publish, container and release jobs hold `id-token: write` / `packages: write`. | #265 | diff D1 (+ D7 test) |
| 2 | high | `scripts/offline_lock.py:46-67` | First-party extras lose their plugin's extras: the `postgres`, `mysql` and `databases` lock sets have no `psycopg` / `pymysql`, and `check` passes. Repro: `declared_sets()['postgres']` = numpy, pyarrow, tzdata only. | #259 | fixed `198450c` test, `aeece78` fix |
| 3 | medium | all workflows | Node 20 actions (checkout v4, setup-python v5, upload/download-artifact v4, setup-java v4, docker/* v3/v5/v6, attest-build-provenance v2 → attest v2.4.0), deprecated on hosted runners. | #265 | diff D1 (same lines as #1) |
| 4 | medium | 21 of 30 jobs | No `timeout-minutes`: a hang burns the 6 h default. Measured on run 37121550573: test legs 8–21 min, plugin jobs 3–8 min. | #265 | diff D2 |
| 5 | medium | `ci.yml:9-11`, `security.yml`, `wheels.yml` | `cancel-in-progress: true` also on pushes to `main` / `build/main-plan`: CI runs 637 and 638 on `build/main-plan` were cancelled, so those commits have no result. | #265 | diff D3 |
| 6 | medium | `release.yml:3,13,18` | SBOM (T-25) is `cyclonedx-py environment` of the job itself: pytest, mypy, pyspark, build, twine... A clean `--without-pip` venv with only the wheel gives exactly numpy + pyarrow (checked locally). `id-token: write` unused. Installs `.[dev]` without `plugins/shape-domains` (12 test modules `importorskip` it). | #266 | diff D4 |
| 7 | medium | `publish.yml:60-80` | `workflow_dispatch` with `repository=pypi` publishes from any branch. The published pure wheel is never run through `check_user_facing.py` / `check_shipped_data.py` (both pass on it today, checked locally). | #266 | diff D4 |
| 8 | medium | `ci.yml` `audit`, `security.yml` | pip-audit covers `[dev,streaming]` only; `azure`, `advanced`, `ctgan`, `sign`, `excel` and the plugin drivers are never audited. With #259 fixed, the offline-lock job's 21 hashed lock sets cover all of them at exact versions; all 21 audit clean today (`pip-audit --no-deps --disable-pip -r`, locally). | #267 | diff D5 |
| 9 | medium | `scripts/check_shipped_data.py:289-300` | `from urllib import request`, `from http import client` and `urllib3` are not seen as network clients (W6-02 check bypass). | #261 | fixed `907bfc3` test, `4ea3d44` fix |
| 10 | medium | `scripts/check_secrets.py:4` | Misses Azure Storage `AccountKey=`, Event Hubs `SharedAccessKey=`, GitHub and AWS tokens, `client_secret`, encrypted/DSA/PGP key blocks. | #262 | fixed `c49599b` test, `f055bdb` fix |
| 11 | medium | `pyproject.toml`, `plugins/shape-databases` | Declared floors admit versions with advisories: azure-identity 1.15 (PYSEC-2026-1209, fix 1.16.1), pymysql 1.1.0 (PYSEC-2026-502, fix 1.1.1), scikit-learn 1.3 (PYSEC-2024-110, fix 1.5.0), pyarrow 14.0.1 (PYSEC-2024-161, R bindings; floor held low for Fabric on purpose). Latest versions: clean. | #267 | open: dependency decision for the lead |
| 12 | medium | `CODEOWNERS` | Placeholder `@OWNER` (not a user): GitHub ignores the file; `.github/`, `scripts/` uncovered. | #268 | diff D8 |
| 13 | medium | `SECURITY.md:10` | Lists plugins as untrusted inputs; D-09 says plugins are trusted in-process code (no sandbox). | #268 | diff D8 |
| 14 | low | `scripts/fuzz_artifacts.py:24` | `--iterations 0` / `-5` exits 0 with "0 finding(s)". | #263 | fixed `01e5136` test, `73f7541` fix |
| 15 | low | `nightly.yml:78` | `test_abfss_sink_azurite.py` listed twice. | — | diff D6 |
| 16 | low | `ci.yml:6`, `security.yml:6`, `wheels.yml:7` | Push triggers still name the finished `lane/CI-FIX`. | — | diff D6 |
| 17 | low | `ci.yml` | `scripts/check_requirements.py` runs in `make check` but nowhere in CI. It passes today (89 requirements). | — | diff D6 |
| 18 | low | no `.github/dependabot.yml` | Nothing keeps the pinned SHAs current. | #265 | diff D8 (new file) |
| 19 | low | `pyproject.toml` `[tool.maturin] include` | The sdist that `publish.yml` uploads carries `docs/plans/`, `benchmarks/`, `.github/`: 13,654 lines naming the baseline library (`check_user_facing.py --wheel <sdist>`). Whether the sdist is user-facing under D-13 is an owner call. | #266 | open: owner decision, not changed |
| 20 | low | `scripts/check_user_facing.py` | 0% test coverage; a missing `--wheel` path (unmatched `dist/*.whl` glob) ended in a traceback. | — | `a69eddb` tests (0→98%), `064489a` clear usage error |
| 21 | low | `scripts/pacing_diagnostic.py:7` | Docstring names `.github/workflows/pacing-diagnostic.yml`, removed by CI-FIX. | — | `a81ed62` |
| 22 | low | `scripts/ci_pure_wheel.sh:23` | `stat -c %s` is GNU-only (fails on macOS). CI runs it on Linux only. | — | open: cosmetic, left |
| 23 | low | `scripts/offline_lock.py:128` | `req.marker.evaluate()` uses the host, not the lock's target (3.11, universal), and duplicate per-marker entries keep the last version only. No wrong result on today's Linux CI. | — | open: no live defect |
| 24 | low | `scripts/check_requirements.py:4` | `read_text()` without `encoding` (locale-dependent on Windows). The YAML is ASCII today. | — | open: no live defect |
| 25 | info | `scripts/env.sh` | `SHAPE_ROOT="$PWD"` is wrong when sourced outside the repo root. Kept: it is the plan's §1 block verbatim. | — | not changed |
| 26 | info | `nightly.yml` `emit-live`, `fabric-live`, `abfss-live` | Green with "nothing to run" when secrets are absent: by design (documented, never a skipif). | — | not changed |
| 27 | medium | `ci.yml:32` (`test` job) | The job installs `.[dev,streaming,advanced]` and `plugins/shape-domains` only, but `tests/demo_cmd/test_notebook_and_outputs.py::test_the_semantic_model_is_a_bim_of_the_learned_schema` and `::test_all_writes_the_page_and_the_model` need `shape-fabric` ("the semantic model needs the shape-fabric plugin"). Reproduced here on `origin/build/main-plan` @ `5c91ea5` in a venv built exactly like the job: 2 failed. Both pass with the plugin installed. | — | diff D9 |

Checked and clean: no `pull_request_target`; no `${{ github.event.* }}` interpolated into `run:`
(only `inputs.repository` in `if:`/`environment:` expressions); top-level `permissions:
{contents: read}` in every workflow, write scopes only on the publish jobs; container PR builds
cannot push; `actionlint` 1.7.12 reports nothing before or after D1–D6; T-06 matrix (3.11–3.14
Linux, 3.11 + 3.14 macOS and Windows) and T-04 wheel matrix match the plan; `cargo audit` on
`rust/shape-kernel/Cargo.lock` (114 crates): no advisories; `pip-audit` on every declared
requirement at latest versions: none (the dev venv's `setuptools 79.0.1` advisory is the venv seed,
not a project dependency).

**Base branch is red (not this lane's area):** CI run 37121550573 on `build/main-plan` @ `5c91ea5`
fails in every `test` leg (pytest step; mypy on windows 3.11), `zero-network`, `bench-quick` and
`stream-plugins (windows-latest)`. One cause of the `test`-leg pytest failures is finding 27
(reproduced locally on base; the job logs could not be fetched from this session). The other
jobs were not investigated here.

## Diffs for the lead (`.github/workflows/*` and files outside this lane)

Apply in order D1 → D8 (each is against the result of the previous; checked with `git apply` in
sequence on `5c91ea5`; actionlint clean afterwards; the workflow-reading tests
`tests/demo/core/test_publish_workflow.py`, `tests/integrations/test_container.py` and
`tests/diff/test_drift_sweep_ci.py` pass with D1–D7 applied).

### D1: pin every action to a full commit SHA, on its Node 24 major (#265; findings 1, 3)

SHAs resolved with `git ls-remote` on 2026-10-03 (`refs/tags/<tag>^{}`). Majors chosen are the
first that run on Node 24 (download-artifact v5/v6 are still Node 20; attest-build-provenance v3
uses `actions/attest` v3, Node 24). Breaking changes checked: artifacts are used by name only, so
download-artifact v7's by-ID path change does not apply. **Needs D7**: the existing test pins the
`@release/v1` text, so this lane did not change it (brief: an existing test's expectation is the
lead's call).

```diff
diff -ruN a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml	2026-10-03 13:14:02.781802652 +0000
+++ b/.github/workflows/ci.yml	2026-10-03 13:14:02.808089788 +0000
@@ -25,8 +25,8 @@
           - {os: windows-latest, python: '3.14'}
     runs-on: ${{ matrix.os }}
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '${{ matrix.python }}', allow-prereleases: true}
       - run: python -m pip install -U pip
       - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains
@@ -74,8 +74,8 @@
     # in-process Spark gateway.
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: python -m pip install -U pip
       - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains
@@ -94,13 +94,13 @@
     # needed to resolve) and checked against pyproject.toml. See docs/INSTALL.md.
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: python -m pip install -U pip uv packaging
       - run: python scripts/offline_lock.py generate "$RUNNER_TEMP/offline-lock"
       - run: python scripts/offline_lock.py check "$RUNNER_TEMP/offline-lock"
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with: {name: offline-lock, path: '${{ runner.temp }}/offline-lock/requirements-*.txt'}
   plugin-skeletons:
     # P2-06: every first-party plugin distribution builds a pure wheel, and the example plugin,
@@ -111,8 +111,8 @@
         os: [ubuntu-latest, macos-latest, windows-latest]
     runs-on: ${{ matrix.os }}
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: python -m pip install -U pip
       - run: pip install -e '.[dev]'
@@ -130,8 +130,8 @@
       matrix:
         os: [ubuntu-latest, windows-latest]
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: python -m pip install -U pip
       - run: pip install -e '.[dev]' -e plugins/shape-domains -e plugins/shape-kafka -e plugins/shape-eventhubs -e plugins/shape-sqlserver -e plugins/shape-fabric
@@ -145,8 +145,8 @@
       matrix:
         os: [ubuntu-latest, windows-latest]
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: python -m pip install -U pip
       - run: pip install -e '.[dev]' -e plugins/shape-databases
@@ -156,8 +156,8 @@
     # T-27: cargo fmt / clippy -D warnings / cargo test for rust/shape-kernel.
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: Swatinem/rust-cache@v2
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: Swatinem/rust-cache@6323deb102c322ba6fcbdcafc7e3dddab59af2b6  # v2.9.2
         with: {workspaces: rust/shape-kernel}
       - run: cargo fmt --manifest-path rust/shape-kernel/Cargo.toml --check
       - run: cargo clippy --manifest-path rust/shape-kernel/Cargo.toml --all-targets -- -D warnings
@@ -165,8 +165,8 @@
   audit:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.13'}
       - run: python -m pip install -U pip
       - run: pip install -e '.[dev,streaming]'
@@ -174,10 +174,10 @@
   fabric-demo:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
-      - uses: actions/setup-java@v4
+      - uses: actions/setup-java@b6effb05e454b25005698d916606bdc6ffcbf961  # v5.7.0
         with: {distribution: temurin, java-version: '17'}
       - run: sudo apt-get update -q && sudo apt-get install -y -q unixodbc
       - run: python -m pip install -U pip
@@ -189,8 +189,8 @@
     # and SHAPE_KERNEL=python (no native kernel). Must be green on every PR.
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: sudo apt-get update -q && sudo apt-get install -y -q unixodbc
       - run: bash scripts/ci_pure_wheel.sh python
@@ -202,8 +202,8 @@
     timeout-minutes: 90
     env: {BENCH_OUT_DIR: "${{ github.workspace }}/bench-out"}
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - name: Pinned RefEngine baseline and venv (plan section 1.2)
         run: source scripts/env.sh && bash benchmarks/vs_refengine/setup_refengine.sh
@@ -222,7 +222,7 @@
           "$REFENGINE_PY" benchmarks/vs_refengine/domain_1to1/generate.py --impl refengine --domain retail --scale small --seed 42
           "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/domain_1to1/generate.py --impl reference_port --domain retail --scale small --seed 1042
           "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/verify_1to1/verify.py
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         if: always()
         with:
           name: benchmark-results-quick
@@ -232,8 +232,8 @@
   build:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.13'}
       - run: pip install build twine pip-audit
       - run: python -m pip install --upgrade pip
@@ -244,5 +244,5 @@
       # W6-02: every reference data file is inside the wheel; nothing downloads at run time.
       - run: python scripts/check_shipped_data.py --wheel dist/*.whl
       - run: pip-audit
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with: {name: distributions, path: dist/*}
diff -ruN a/.github/workflows/container.yml b/.github/workflows/container.yml
--- a/.github/workflows/container.yml	2026-10-03 13:14:02.781831376 +0000
+++ b/.github/workflows/container.yml	2026-10-03 13:14:02.809228447 +0000
@@ -14,9 +14,9 @@
   build:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: docker/setup-buildx-action@v3
-      - uses: docker/build-push-action@v6
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069  # v4.4.1
+      - uses: docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc  # v7.4.0
         with: {context: ., load: true, tags: 'shape:ci', cache-from: 'type=gha', cache-to: 'type=gha,mode=max'}
       - name: Image size is under 500 MB
         run: |
@@ -29,7 +29,7 @@
           docker run --rm -e SHAPE_KERNEL=rust shape:ci python -c \
             "import adlfs, azure.identity, deltalake, shape; from shape.kernel import kernel_name; assert kernel_name() == 'rust'"
           docker run --rm shape:ci shape plugins list --group shape.sources
-      - uses: actions/setup-python@v5
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - name: Profile D1, mounted read-only from the host, inside the image
         run: |
@@ -49,19 +49,19 @@
     runs-on: ubuntu-latest
     permissions: {contents: read, packages: write, id-token: write, attestations: write}
     steps:
-      - uses: actions/checkout@v4
-      - uses: docker/setup-buildx-action@v3
-      - uses: docker/login-action@v3
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069  # v4.4.1
+      - uses: docker/login-action@dbcb813823bdd20940b903addbd779551569679f  # v4.6.0
         with: {registry: ghcr.io, username: '${{ github.actor }}', password: '${{ secrets.GITHUB_TOKEN }}'}
       - id: meta
-        uses: docker/metadata-action@v5
+        uses: docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302  # v6.2.0
         with:
           images: ${{ env.IMAGE }}
           tags: |
             type=semver,pattern={{version}}
             type=semver,pattern={{major}}.{{minor}}
       - id: push
-        uses: docker/build-push-action@v6
+        uses: docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc  # v7.4.0
         with: {context: ., push: true, tags: '${{ steps.meta.outputs.tags }}', labels: '${{ steps.meta.outputs.labels }}'}
-      - uses: actions/attest-build-provenance@v2
+      - uses: actions/attest-build-provenance@96278af6caaf10aea03fd8d33a09a777ca52d62f  # v3.2.0
         with: {subject-name: '${{ env.IMAGE }}', subject-digest: '${{ steps.push.outputs.digest }}', push-to-registry: true}
diff -ruN a/.github/workflows/nightly.yml b/.github/workflows/nightly.yml
--- a/.github/workflows/nightly.yml	2026-10-03 13:14:02.781848828 +0000
+++ b/.github/workflows/nightly.yml	2026-10-03 13:14:02.809013194 +0000
@@ -12,8 +12,8 @@
     timeout-minutes: 360
     env: {BENCH_OUT_DIR: "${{ github.workspace }}/bench-out"}
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - name: Pinned RefEngine baseline and venv (plan section 1.2)
         run: source scripts/env.sh && bash benchmarks/vs_refengine/setup_refengine.sh
@@ -25,7 +25,7 @@
           "$SHAPE_VENV/bin/pip" install -e '.[dev]' 'pyarrow==25.0.1'
       - run: source scripts/env.sh && python benchmarks/vs_refengine/check_coverage.py
       - run: source scripts/env.sh && python benchmarks/vs_refengine/run.py --full
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         if: always()
         with:
           name: benchmark-results-full
@@ -38,8 +38,8 @@
     runs-on: ubuntu-latest
     timeout-minutes: 60
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: pip install -e '.[dev]'
       - run: python -m pytest -q tests/diff/test_drift_sweep.py
@@ -52,8 +52,8 @@
     runs-on: ubuntu-latest
     timeout-minutes: 90
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: pip install -e '.[dev]' -e plugins/shape-domains
       - run: python -m pytest -q -s -m heavy tests/streaming/emit/test_soak.py
@@ -63,15 +63,15 @@
     # work packages that own those connectors; until then, keep the compose file valid.
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - run: docker compose -f ci/emulators/docker-compose.yml config --quiet
   azurite-e2e:
     # PF-01: the abfss:// source against Azurite (blob endpoint) with the real adlfs.
     # ISS2-sinks: the abfss:// sink (write, read back, rolling files, errors, the CLI).
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: pip install -e '.[dev,azure]' azure-storage-blob
       - run: docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite
@@ -84,8 +84,8 @@
     runs-on: ubuntu-latest
     timeout-minutes: 30
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: docker compose -f ci/emulators/docker-compose.yml up -d --wait kafka
       - run: pip install -e '.[dev]' -e plugins/shape-domains -e plugins/shape-kafka
@@ -98,8 +98,8 @@
     runs-on: ubuntu-latest
     timeout-minutes: 30
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite eventhubs
       - run: pip install -e '.[dev]' -e plugins/shape-domains -e plugins/shape-eventhubs
@@ -112,8 +112,8 @@
     runs-on: ubuntu-latest
     timeout-minutes: 30
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite eventhubs
       - run: docker compose -f ci/emulators/docker-compose.yml up -d kusto
@@ -143,9 +143,9 @@
       - if: env.HAVE_LIVE != 'true'
         run: echo "no live secrets configured; nothing to run"
       - if: env.HAVE_LIVE == 'true'
-        uses: actions/checkout@v4
+        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - if: env.HAVE_LIVE == 'true'
-        uses: actions/setup-python@v5
+        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - if: env.HAVE_LIVE == 'true'
         run: pip install -e '.[dev]' -e plugins/shape-domains -e plugins/shape-kafka -e plugins/shape-eventhubs -e plugins/shape-fabric
@@ -183,9 +183,9 @@
       - if: env.HAVE_ANY != 'true'
         run: echo "no Fabric secrets configured; nothing to run"
       - if: env.HAVE_ANY == 'true'
-        uses: actions/checkout@v4
+        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - if: env.HAVE_ANY == 'true'
-        uses: actions/setup-python@v5
+        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - name: Microsoft ODBC Driver 18
         if: env.HAVE_ANY == 'true'
@@ -227,7 +227,7 @@
       - if: env.HAVE_ANY == 'true' && env.FABRIC_CLIENT_ID != '' && env.FABRIC_TENANT_ID != '' && env.FABRIC_CLIENT_SECRET != '' && env.FABRIC_SQL_CONNECTION_STRING != ''
         run: python -m pytest -m live plugins/shape-fabric/tests/test_live_commands.py -q -k sql_database
       - if: always() && env.HAVE_ANY == 'true'
-        uses: actions/upload-artifact@v4
+        uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with:
           name: fabric-live-tapes
           path: live-tapes
@@ -250,9 +250,9 @@
       - if: env.HAVE_ABFSS != 'true'
         run: echo "no abfss live secrets configured; nothing to run"
       - if: env.HAVE_ABFSS == 'true'
-        uses: actions/checkout@v4
+        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - if: env.HAVE_ABFSS == 'true'
-        uses: actions/setup-python@v5
+        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - if: env.HAVE_ABFSS == 'true'
         run: pip install -e '.[dev,azure]'
@@ -267,8 +267,8 @@
       BENCH_OUT_DIR: "${{ github.workspace }}/bench-out"
       SHAPE_TEST_MSSQL: "Driver={ODBC Driver 18 for SQL Server};Server=localhost,1433;UID=sa;PWD=Shape_Emulator_1;Encrypt=yes;TrustServerCertificate=yes"
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - name: Microsoft ODBC Driver 18
         run: |
@@ -308,8 +308,8 @@
       SHAPE_POSTGRES_PASSWORD: shape_emulator
       SHAPE_MYSQL_PASSWORD: shape_emulator
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: docker compose -f ci/emulators/docker-compose.yml up -d --wait postgres mysql
       - run: pip install -e '.[dev]' -e 'plugins/shape-databases[postgres,mysql]'
@@ -323,12 +323,12 @@
     runs-on: ubuntu-latest
     timeout-minutes: 60
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: pip install -e '.[dev]'
       - run: python scripts/fuzz_artifacts.py --seed "$(date +%s)" --iterations 3000 --out fuzz-findings
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         if: failure()
         with:
           name: fuzz-findings
diff -ruN a/.github/workflows/publish.yml b/.github/workflows/publish.yml
--- a/.github/workflows/publish.yml	2026-10-03 13:14:02.781875936 +0000
+++ b/.github/workflows/publish.yml	2026-10-03 13:14:02.808329465 +0000
@@ -31,8 +31,8 @@
     name: Build and test the wheel and sdist
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with:
           python-version: "3.11"
       - run: python -m pip install --upgrade pip build twine
@@ -51,7 +51,7 @@
       - name: twine check
         run: python -m twine check dist/*
       - run: ls -l dist
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with:
           name: dist
           path: dist/*
@@ -66,15 +66,15 @@
       id-token: write
       contents: read
     steps:
-      - uses: actions/download-artifact@v4
+      - uses: actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131  # v7.0.0
         with:
           name: dist
           path: dist
       - name: Publish to TestPyPI
         if: github.event_name == 'workflow_dispatch' && inputs.repository == 'testpypi'
-        uses: pypa/gh-action-pypi-publish@release/v1
+        uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33  # v1.14.2
         with:
           repository-url: https://test.pypi.org/legacy/
       - name: Publish to PyPI
         if: github.event_name != 'workflow_dispatch' || inputs.repository == 'pypi'
-        uses: pypa/gh-action-pypi-publish@release/v1
+        uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33  # v1.14.2
diff -ruN a/.github/workflows/release.yml b/.github/workflows/release.yml
--- a/.github/workflows/release.yml	2026-10-03 13:14:02.781895520 +0000
+++ b/.github/workflows/release.yml	2026-10-03 13:14:02.808446995 +0000
@@ -5,8 +5,8 @@
   release:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.13'}
       - run: python -m pip install --upgrade pip
       - run: pip install build twine pip-audit cyclonedx-bom
@@ -16,7 +16,7 @@
       - run: python -m twine check dist/*
       - run: pip-audit
       - run: cyclonedx-py environment -o sbom.json
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with:
           name: release-candidate
           path: |
diff -ruN a/.github/workflows/security.yml b/.github/workflows/security.yml
--- a/.github/workflows/security.yml	2026-10-03 13:14:02.781911759 +0000
+++ b/.github/workflows/security.yml	2026-10-03 13:14:02.807309044 +0000
@@ -14,8 +14,8 @@
   security:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: "3.12"}
       - run: python -m pip install --upgrade pip
       - run: python -m pip install -e ".[dev,streaming]"
diff -ruN a/.github/workflows/wheels.yml b/.github/workflows/wheels.yml
--- a/.github/workflows/wheels.yml	2026-10-03 13:14:02.781928297 +0000
+++ b/.github/workflows/wheels.yml	2026-10-03 13:14:02.807611222 +0000
@@ -30,18 +30,18 @@
           - {name: windows-x64, os: windows-latest, target: x64, kind: native}
     runs-on: ${{ matrix.os }}
     steps:
-      - uses: actions/checkout@v4
-      - uses: PyO3/maturin-action@v1
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: PyO3/maturin-action@e83996d129638aa358a18fbd1dfb82f0b0fb5d3b  # v1.51.0
         with:
           target: ${{ matrix.target }}
           manylinux: ${{ matrix.manylinux || 'off' }}
           args: --release --out dist
           # the wheel is abi3 (py311+), so one interpreter is enough to build it
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with: {name: 'wheel-${{ matrix.name }}', path: dist/*.whl}
       # --- smoke test on the build machine (native kinds) ---
       - if: matrix.kind == 'native'
-        uses: actions/setup-python@v5
+        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - if: matrix.kind == 'native'
         shell: bash
@@ -50,7 +50,7 @@
           python -m pip install dist/*.whl
           SHAPE_KERNEL=rust python -c "import shape; print(shape._kernel.version()); from shape.kernel import kernel_name; assert kernel_name() == 'rust'"
       - if: matrix.kind == 'native'
-        uses: actions/setup-python@v5
+        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.14', allow-prereleases: true}
       - if: matrix.kind == 'native'
         shell: bash
@@ -75,9 +75,9 @@
       fail-fast: false
       matrix: {python: ['3.11', '3.14']}
     steps:
-      - uses: actions/download-artifact@v4
+      - uses: actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131  # v7.0.0
         with: {name: wheel-macos-x86_64, path: dist}
-      - uses: actions/setup-python@v5
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '${{ matrix.python }}', allow-prereleases: true}
       - run: |
           python -m pip install -U pip
@@ -86,12 +86,12 @@
   sdist:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: PyO3/maturin-action@v1
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: PyO3/maturin-action@e83996d129638aa358a18fbd1dfb82f0b0fb5d3b  # v1.51.0
         with: {command: sdist, args: --out dist}
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with: {name: sdist, path: dist/*.tar.gz}
-      - uses: actions/setup-python@v5
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       # the sdist must build with Rust >= 1.85 (the runner's stable) and then run
       - run: |
```

### D2: `timeout-minutes` on every job (#265; finding 4)

```diff
diff -ruN a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml	2026-10-03 13:14:02.808089788 +0000
+++ b/.github/workflows/ci.yml	2026-10-03 13:14:46.186536297 +0000
@@ -24,6 +24,7 @@
           - {os: windows-latest, python: '3.11'}
           - {os: windows-latest, python: '3.14'}
     runs-on: ${{ matrix.os }}
+    timeout-minutes: 90
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -73,6 +74,7 @@
     # in-process socket guard (tests/conftest.py) were bypassed. Loopback stays up for the
     # in-process Spark gateway.
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -93,6 +95,7 @@
     # W6-02: pinned, hashed requirements for core and each extra, built here (a network is
     # needed to resolve) and checked against pyproject.toml. See docs/INSTALL.md.
     runs-on: ubuntu-latest
+    timeout-minutes: 20
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -110,6 +113,7 @@
       matrix:
         os: [ubuntu-latest, macos-latest, windows-latest]
     runs-on: ${{ matrix.os }}
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -126,6 +130,7 @@
   stream-plugins:
     # P3-04, P5-02, P6-07a, P6-08: the Kafka, Event Hubs, SQL Server and Fabric plugins' contract tests.
     runs-on: ${{ matrix.os }}
+    timeout-minutes: 30
     strategy:
       matrix:
         os: [ubuntu-latest, windows-latest]
@@ -141,6 +146,7 @@
     # ISS2-sinks: the PostgreSQL and MySQL sinks, contract tests against an in-memory server
     # (no driver is installed: the sinks load psycopg / PyMySQL only when they connect).
     runs-on: ${{ matrix.os }}
+    timeout-minutes: 20
     strategy:
       matrix:
         os: [ubuntu-latest, windows-latest]
@@ -155,6 +161,7 @@
   rust:
     # T-27: cargo fmt / clippy -D warnings / cargo test for rust/shape-kernel.
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: Swatinem/rust-cache@6323deb102c322ba6fcbdcafc7e3dddab59af2b6  # v2.9.2
@@ -164,6 +171,7 @@
       - run: cargo test --manifest-path rust/shape-kernel/Cargo.toml
   audit:
     runs-on: ubuntu-latest
+    timeout-minutes: 15
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -173,6 +181,7 @@
       - run: pip-audit --skip-editable
   fabric-demo:
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -188,6 +197,7 @@
     # function_app.py pass on Python 3.11 with only PyPI numpy, pyarrow and pandas under them
     # and SHAPE_KERNEL=python (no native kernel). Must be green on every PR.
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -231,6 +241,7 @@
             ${{ github.workspace }}/bench-out/verify/
   build:
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
diff -ruN a/.github/workflows/container.yml b/.github/workflows/container.yml
--- a/.github/workflows/container.yml	2026-10-03 13:14:02.809228447 +0000
+++ b/.github/workflows/container.yml	2026-10-03 13:14:12.619630135 +0000
@@ -13,6 +13,7 @@
 jobs:
   build:
     runs-on: ubuntu-latest
+    timeout-minutes: 45
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069  # v4.4.1
@@ -47,6 +48,7 @@
     if: startsWith(github.ref, 'refs/tags/v')
     needs: build
     runs-on: ubuntu-latest
+    timeout-minutes: 45
     permissions: {contents: read, packages: write, id-token: write, attestations: write}
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
diff -ruN a/.github/workflows/nightly.yml b/.github/workflows/nightly.yml
--- a/.github/workflows/nightly.yml	2026-10-03 13:14:02.809013194 +0000
+++ b/.github/workflows/nightly.yml	2026-10-03 13:14:12.620144046 +0000
@@ -62,6 +62,7 @@
     # The emulator-backed test jobs (Kafka, Event Hubs + Azurite, SQL Server) arrive with the
     # work packages that own those connectors; until then, keep the compose file valid.
     runs-on: ubuntu-latest
+    timeout-minutes: 10
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - run: docker compose -f ci/emulators/docker-compose.yml config --quiet
@@ -69,6 +70,7 @@
     # PF-01: the abfss:// source against Azurite (blob endpoint) with the real adlfs.
     # ISS2-sinks: the abfss:// sink (write, read back, rolling files, errors, the CLI).
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
diff -ruN a/.github/workflows/publish.yml b/.github/workflows/publish.yml
--- a/.github/workflows/publish.yml	2026-10-03 13:14:02.808329465 +0000
+++ b/.github/workflows/publish.yml	2026-10-03 13:14:12.620403006 +0000
@@ -30,6 +30,7 @@
   build:
     name: Build and test the wheel and sdist
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -61,6 +62,7 @@
     name: Publish to ${{ (github.event_name == 'workflow_dispatch' && inputs.repository == 'testpypi') && 'TestPyPI' || 'PyPI' }}
     needs: build
     runs-on: ubuntu-latest
+    timeout-minutes: 15
     environment: ${{ (github.event_name == 'workflow_dispatch' && inputs.repository == 'testpypi') && 'testpypi' || 'pypi' }}
     permissions:
       id-token: write
diff -ruN a/.github/workflows/release.yml b/.github/workflows/release.yml
--- a/.github/workflows/release.yml	2026-10-03 13:14:02.808446995 +0000
+++ b/.github/workflows/release.yml	2026-10-03 13:14:12.620539862 +0000
@@ -4,6 +4,7 @@
 jobs:
   release:
     runs-on: ubuntu-latest
+    timeout-minutes: 60
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
diff -ruN a/.github/workflows/security.yml b/.github/workflows/security.yml
--- a/.github/workflows/security.yml	2026-10-03 13:14:02.807309044 +0000
+++ b/.github/workflows/security.yml	2026-10-03 13:14:12.620690208 +0000
@@ -13,6 +13,7 @@
 jobs:
   security:
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
diff -ruN a/.github/workflows/wheels.yml b/.github/workflows/wheels.yml
--- a/.github/workflows/wheels.yml	2026-10-03 13:14:02.807611222 +0000
+++ b/.github/workflows/wheels.yml	2026-10-03 13:14:12.620911996 +0000
@@ -29,6 +29,7 @@
           - {name: macos-x86_64, os: macos-14, target: x86_64-apple-darwin, kind: cross}
           - {name: windows-x64, os: windows-latest, target: x64, kind: native}
     runs-on: ${{ matrix.os }}
+    timeout-minutes: 45
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: PyO3/maturin-action@e83996d129638aa358a18fbd1dfb82f0b0fb5d3b  # v1.51.0
@@ -71,6 +72,7 @@
     # the x86_64 macOS wheel is cross-compiled, so install it on a real Intel runner
     needs: wheel
     runs-on: macos-15-intel
+    timeout-minutes: 20
     strategy:
       fail-fast: false
       matrix: {python: ['3.11', '3.14']}
@@ -85,6 +87,7 @@
           SHAPE_KERNEL=rust python -c "import shape; print(shape._kernel.version()); from shape.kernel import kernel_name; assert kernel_name() == 'rust'"
   sdist:
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: PyO3/maturin-action@e83996d129638aa358a18fbd1dfb82f0b0fb5d3b  # v1.51.0
```

### D3: cancel superseded runs on pull requests only (#265; finding 5)

```diff
diff -ruN a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml	2026-10-03 13:14:46.186536297 +0000
+++ b/.github/workflows/ci.yml	2026-10-03 13:14:46.204957151 +0000
@@ -8,7 +8,8 @@
   workflow_dispatch:
 concurrency:
   group: ${{ github.workflow }}-${{ github.ref }}
-  cancel-in-progress: true
+  # cancel superseded PR runs only: every push to an integration branch gets a full result
+  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
 permissions: {contents: read}
 jobs:
   test:
diff -ruN a/.github/workflows/security.yml b/.github/workflows/security.yml
--- a/.github/workflows/security.yml	2026-10-03 13:14:12.620690208 +0000
+++ b/.github/workflows/security.yml	2026-10-03 13:14:46.218378644 +0000
@@ -8,7 +8,8 @@
   workflow_dispatch:
 concurrency:
   group: ${{ github.workflow }}-${{ github.ref }}
-  cancel-in-progress: true
+  # cancel superseded PR runs only: every push to an integration branch gets a full result
+  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
 permissions: {contents: read}
 jobs:
   security:
diff -ruN a/.github/workflows/wheels.yml b/.github/workflows/wheels.yml
--- a/.github/workflows/wheels.yml	2026-10-03 13:14:12.620911996 +0000
+++ b/.github/workflows/wheels.yml	2026-10-03 13:14:46.230960408 +0000
@@ -12,7 +12,8 @@
 permissions: {contents: read}
 concurrency:
   group: ${{ github.workflow }}-${{ github.ref }}
-  cancel-in-progress: true
+  # cancel superseded PR runs only: every push to an integration branch gets a full result
+  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
 jobs:
   wheel:
     name: wheel (${{ matrix.name }})
```

### D4: release candidate SBOM of the installed wheel, no OIDC token, domains installed; publish to PyPI only from `main` or a tag, and check the published wheel (#266; findings 6, 7)

```diff
diff -ruN a/.github/workflows/publish.yml b/.github/workflows/publish.yml
--- a/.github/workflows/publish.yml	2026-10-03 13:14:46.194043825 +0000
+++ b/.github/workflows/publish.yml	2026-10-03 13:15:25.602779521 +0000
@@ -47,6 +47,10 @@
         run: grep -qi "early access" README.md
       - name: Build the pure wheel, install it in a clean venv and run tests/demo/core
         run: python scripts/build_pure_wheel.py --verify --python python3.11
+      - name: The published wheel passes the user-facing and shipped-data checks (D-13, W6-02)
+        run: |
+          python scripts/check_user_facing.py --wheel dist/*.whl
+          python scripts/check_shipped_data.py --wheel dist/*.whl
       - name: Build the sdist
         run: python -m build --sdist
       - name: twine check
@@ -61,6 +65,10 @@
   publish:
     name: Publish to ${{ (github.event_name == 'workflow_dispatch' && inputs.repository == 'testpypi') && 'TestPyPI' || 'PyPI' }}
     needs: build
+    # PyPI only from main or a v* tag; TestPyPI from any branch
+    if: >-
+      (github.event_name == 'workflow_dispatch' && inputs.repository == 'testpypi')
+      || github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')
     runs-on: ubuntu-latest
     timeout-minutes: 15
     environment: ${{ (github.event_name == 'workflow_dispatch' && inputs.repository == 'testpypi') && 'testpypi' || 'pypi' }}
diff -ruN a/.github/workflows/release.yml b/.github/workflows/release.yml
--- a/.github/workflows/release.yml	2026-10-03 13:14:46.194060821 +0000
+++ b/.github/workflows/release.yml	2026-10-03 13:15:25.602615914 +0000
@@ -1,6 +1,7 @@
 name: Release candidate
 on: {workflow_dispatch: {}}
-permissions: {contents: read, id-token: write}
+# nothing here signs or publishes, so no OIDC token (T-25 attestation belongs to the publish step)
+permissions: {contents: read}
 jobs:
   release:
     runs-on: ubuntu-latest
@@ -11,12 +12,19 @@
         with: {python-version: '3.13'}
       - run: python -m pip install --upgrade pip
       - run: pip install build twine pip-audit cyclonedx-bom
-      - run: pip install '.[dev]'
+      # the same install as `make bootstrap` and ci.yml: without shape-domains, 12 test modules skip
+      - run: pip install -e '.[dev]' -e plugins/shape-domains
       - run: make check
       - run: python -m build
       - run: python -m twine check dist/*
       - run: pip-audit
-      - run: cyclonedx-py environment -o sbom.json
+      # T-25: the SBOM of what a user installs (the wheel and its dependencies), not of this
+      # job's environment (pytest, mypy, build tools...)
+      - name: SBOM of a clean install of the wheel
+        run: |
+          python -m venv --without-pip "$RUNNER_TEMP/sbom-venv"
+          python -m pip --python "$RUNNER_TEMP/sbom-venv/bin/python" install dist/*.whl
+          cyclonedx-py environment --pyproject pyproject.toml -o sbom.json "$RUNNER_TEMP/sbom-venv/bin/python"
       - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with:
           name: release-candidate
```

### D5: pip-audit every offline lock set (#267; finding 8)

```diff
diff -ruN a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml	2026-10-03 13:15:25.589314181 +0000
+++ b/.github/workflows/ci.yml	2026-10-03 13:15:34.200836680 +0000
@@ -101,9 +101,21 @@
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
-      - run: python -m pip install -U pip uv packaging
+      - run: python -m pip install -U pip uv packaging pip-audit
       - run: python scripts/offline_lock.py generate "$RUNNER_TEMP/offline-lock"
       - run: python scripts/offline_lock.py check "$RUNNER_TEMP/offline-lock"
+      # every extra and plugin driver, at the exact versions the lock pins (the audit job covers
+      # only [dev,streaming])
+      - name: pip-audit every lock set
+        shell: bash
+        run: |
+          rc=0
+          for f in "$RUNNER_TEMP"/offline-lock/requirements-*.txt; do
+            echo "::group::$(basename "$f")"
+            pip-audit --no-deps --disable-pip --progress-spinner off -r "$f" || rc=1
+            echo "::endgroup::"
+          done
+          exit $rc
       - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with: {name: offline-lock, path: '${{ runner.temp }}/offline-lock/requirements-*.txt'}
   plugin-skeletons:
```

### D6: hygiene: duplicate test path, stale `lane/CI-FIX` triggers, `check_requirements.py` in CI (findings 15–17)

```diff
diff -ruN a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml	2026-10-03 13:15:34.200836680 +0000
+++ b/.github/workflows/ci.yml	2026-10-03 13:15:52.106716512 +0000
@@ -3,7 +3,7 @@
   # Full CI runs on the integration branches; lane branches are verified locally by the lead
   # before they merge (the queue reached 179 runs when every lane push ran the full matrix).
   push:
-    branches: [main, build/main-plan, lane/CI-FIX]
+    branches: [main, build/main-plan]
   pull_request:
   workflow_dispatch:
 concurrency:
@@ -44,6 +44,8 @@
       - run: vulture src/shape scripts/vulture_whitelist.py --min-confidence 80
       - run: lint-imports
       - run: python scripts/check_conformance_coverage.py
+      # `make check` runs it; CI did not, so an unknown SHAPE-*-NNN reference went unnoticed
+      - run: python scripts/check_requirements.py
       - run: python scripts/check_user_facing.py
       - run: python scripts/check_plugin_skeletons.py
       # The Fabric demo tests need Java, PySpark and unixODBC; they and the demo content tests
diff -ruN a/.github/workflows/nightly.yml b/.github/workflows/nightly.yml
--- a/.github/workflows/nightly.yml	2026-10-03 13:15:34.190290570 +0000
+++ b/.github/workflows/nightly.yml	2026-10-03 13:15:52.106276212 +0000
@@ -77,7 +77,7 @@
         with: {python-version: '3.11'}
       - run: pip install -e '.[dev,azure]' azure-storage-blob
       - run: docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite
-      - run: python -m pytest -q -m emulator tests/builtins/test_cloud_sources_azurite.py tests/builtins/test_abfss_sink_azurite.py tests/builtins/test_abfss_sink_azurite.py
+      - run: python -m pytest -q -m emulator tests/builtins/test_cloud_sources_azurite.py tests/builtins/test_abfss_sink_azurite.py
       - if: always()
         run: docker compose -f ci/emulators/docker-compose.yml down -v
   kafka-e2e:
diff -ruN a/.github/workflows/security.yml b/.github/workflows/security.yml
--- a/.github/workflows/security.yml	2026-10-03 13:15:34.190481557 +0000
+++ b/.github/workflows/security.yml	2026-10-03 13:15:52.106100521 +0000
@@ -3,7 +3,7 @@
   # Full CI runs on the integration branches; lane branches are verified locally by the lead
   # before they merge (the queue reached 179 runs when every lane push ran the full matrix).
   push:
-    branches: [main, build/main-plan, lane/CI-FIX]
+    branches: [main, build/main-plan]
   pull_request:
   workflow_dispatch:
 concurrency:
diff -ruN a/.github/workflows/wheels.yml b/.github/workflows/wheels.yml
--- a/.github/workflows/wheels.yml	2026-10-03 13:15:34.190498826 +0000
+++ b/.github/workflows/wheels.yml	2026-10-03 13:15:52.106181690 +0000
@@ -4,7 +4,7 @@
 # Nothing is published from here (release is P8-04, by the owner).
 on:
   push:
-    branches: [main, build/main-plan, lane/CI-FIX]
+    branches: [main, build/main-plan]
     paths: ['rust/**', 'pyproject.toml', 'src/shape/_kernel.pyi', '.github/workflows/wheels.yml']
   pull_request:
     paths: ['rust/**', 'pyproject.toml', 'src/shape/_kernel.pyi', '.github/workflows/wheels.yml']
```

### D7: test change that D1 needs (`tests/demo/core/test_publish_workflow.py`)

Keeps the intent (two trusted-publishing steps) and adds that they are SHA-pinned.

```diff
diff --git a/tests/demo/core/test_publish_workflow.py b/tests/demo/core/test_publish_workflow.py
index 643592c..1fed0f7 100644
--- a/tests/demo/core/test_publish_workflow.py
+++ b/tests/demo/core/test_publish_workflow.py
@@ -50,7 +50,8 @@ def test_environments_and_permissions():
 
 def test_uses_trusted_publishing_and_no_secrets():
     body = code_lines()
-    assert body.count("pypa/gh-action-pypi-publish@release/v1") == 2
+    # pinned to a full commit SHA (AUD-ci, #265), never a branch or tag
+    assert len(re.findall(r"pypa/gh-action-pypi-publish@[0-9a-f]{40}\b", body)) == 2
     assert "https://test.pypi.org/legacy/" in body
     lowered = body.lower()
     assert "secrets." not in lowered
```

### D8: Dependabot for actions, a real CODEOWNERS, SECURITY.md matches D-09 (#265, #268; findings 12, 13, 18)

`sqllocks` is the repository owner's GitHub login (user id 29076762).

```diff
diff --git a/.github/dependabot.yml b/.github/dependabot.yml
new file mode 100644
index 0000000..b8d0caf
--- /dev/null
+++ b/.github/dependabot.yml
@@ -0,0 +1,8 @@
+# Keeps the SHA-pinned actions current (AUD-ci, #265): one grouped weekly PR.
+version: 2
+updates:
+  - package-ecosystem: github-actions
+    directory: /
+    schedule: {interval: weekly}
+    groups:
+      actions: {patterns: ["*"]}
diff --git a/CODEOWNERS b/CODEOWNERS
index c2f6084..e84f439 100644
--- a/CODEOWNERS
+++ b/CODEOWNERS
@@ -1,4 +1,8 @@
-# Replace placeholder after GitHub repository creation.
-/docs/PRODUCT_ARCHITECTURE.md @OWNER
-/docs/SECURITY_SPECIFICATION.md @OWNER
-/security/ @OWNER
+# Review is requested from the maintainer for every change; the release and CI paths are named
+# explicitly so that a later narrowing of `*` cannot drop them.
+* @sqllocks
+/.github/ @sqllocks
+/scripts/ @sqllocks
+/pyproject.toml @sqllocks
+/SECURITY.md @sqllocks
+/security/ @sqllocks
diff --git a/SECURITY.md b/SECURITY.md
index 98b0703..6e88ef0 100644
--- a/SECURITY.md
+++ b/SECURITY.md
@@ -7,5 +7,6 @@ PII or customer datasets in reports.
 
 Shape is in early access; only the latest release receives fixes.
 
-Shape treats artifacts, packs, plugins, connector responses and reference assets as
-untrusted inputs. See `docs/THREAT_MODEL.md`.
+Shape treats artifacts, packs, connector responses and reference assets as untrusted
+inputs. Plugins are trusted, in-process code that you choose to install: Shape does
+not sandbox them. See `docs/THREAT_MODEL.md`.
```

### D9: the `test` job installs `shape-fabric`, which the `demo_cmd` tests need (finding 27)

```diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -29,7 +29,8 @@
       - uses: actions/setup-python@v5
         with: {python-version: '${{ matrix.python }}', allow-prereleases: true}
       - run: python -m pip install -U pip
-      - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains
+      # tests/demo_cmd writes the semantic model through shape-fabric
+      - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains -e plugins/shape-fabric
       # T-27 scope: src tests plugins benchmarks/vs_refengine rust (only the paths that exist yet).
       - run: ruff check src tests plugins benchmarks/vs_refengine
       - run: ruff format --check src tests plugins benchmarks/vs_refengine
```

The `zero-network` job (`ci.yml:81`) has the same install line, but its `-m "zero_network and not
heavy"` selection does not include these tests, so it is left as is. D1 changes the `uses:` lines
above this hunk, not this one: the two diffs apply in either order.

## Fix commits (this lane)

| Issue | Regression test (fails before) | Fix |
|---|---|---|
| #259 offline lock drops plugin extras | `198450c` | `aeece78` |
| #261 shipped-data network import bypass | `907bfc3` | `4ea3d44` |
| #262 secret check misses cloud credentials | `c49599b` | `f055bdb` |
| #263 fuzz driver passes with 0 iterations | `01e5136` | `73f7541` |
| improvement: check_user_facing tests | `a69eddb` | — |
| improvement: missing archive usage error | (test in same commit) | `064489a` |
| improvement: pacing_diagnostic docstring | — | `a81ed62` |

`check_secrets.py` note: the new patterns (not the two original ones) accept a match on a line
marked `nosec`, the bandit marker the repo already uses for deliberate fakes
(`plugins/shape-fabric/src/shape_fabric/scenarios.py:488`). The original patterns are unchanged and
take no exemption (tested).

## Left open

- #265, #266 (except the sdist question), #267 (audit coverage), #268: diffs D1–D9 above,
  for the lead to apply.
- #267 dependency floors and #266 item 6 (sdist contents): owner/lead decisions.
- Findings 22–26: low, no live defect; recorded only.

## Commands and results (this session)

Run on `lane/AUD-ci` @ `6ca5989` (code identical to `dadbd87`; later commits change only this file).
`origin/build/main-plan` had not moved (`5c91ea5` is an ancestor of the branch), so there was no merge.
Python 3.11.15, rustc 1.97.0, Linux.

Two venvs, so that each check runs in the environment its CI job uses:

- **full** (`$SHAPE_VENV`, §1): `pip install -e '.[dev,streaming,advanced]' -r tests/demo/fabric/requirements.txt`
  plus `-e` for all seven `plugins/*`, plus system `unixodbc` (`libodbc.so.2`, needed by
  `fabric-user-data-functions`) and Java 21. The fabric requirements resolve to pyarrow 19.0.1,
  pandas 3.0.6, numpy 2.4.6, pyspark 4.2.0, azure-functions 1.25.0.
- **ci-test** (`~/.venvs/shape-ci`): exactly the `ci.yml` `test` job install,
  `pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains`. That gives pyarrow 25.0.1.

| Command | Venv | Result |
|---|---|---|
| `make check`: ruff check, ruff format --check, mypy, compileall, vulture, lint-imports, check_requirements, check_secrets, check_user_facing, check_shipped_data, check_plugin_skeletons, check_conformance_coverage | ci-test | all pass (ruff: all checks passed, 1090 files formatted; mypy: no issues in 436 files; contracts 1 kept 0 broken; 89 requirements; secrets OK; user-facing clean; 21 data files; 7 skeletons) |
| `make check` pytest step (`-m "not emulator and not live and not heavy"`, coverage ≥ 86) | ci-test | 2 failed, 6813 passed, 2 skipped (`shape_databases` not in this job's install); coverage 92.52 %. The 2 failures are finding 27 and fail identically on base (below). |
| same pytest step | full | 3 failed, 6814 passed, 0 skipped; coverage reached. The 3 failures are environment ones (E1–E3 below), all on base too. |
| `pytest -q -m heavy tests/kernel tests/profile tests/streaming` | ci-test | 42 passed |
| `SHAPE_KERNEL=python pytest -q tests/kernel` | ci-test | 265 passed |
| the same two steps | full | fail only on E2 (float16), pyarrow 19 |
| `cargo fmt --check`, `cargo clippy --all-targets -D warnings`, `cargo test` (`rust/shape-kernel`) | full | pass; cargo test 34 passed |
| `python scripts/check_user_facing.py` | full | `check_user_facing: clean` |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | full | 4 failed, 7109 passed, 0 skipped, 13 deselected (32 min) |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | full | 4 failed, 7109 passed, 0 skipped, 13 deselected (1 h 42 min; `test_bounded_mode_memory_does_not_grow_with_rows` alone takes 65 min on the Python kernel) |

Nothing was deselected, skipped or xfailed by hand. The "deselected" counts come from the markers in
the commands above. Every failure was rerun on `origin/build/main-plan` @ `5c91ea5` in the same
venv, and every one fails there too:

- **E1** `tests/iss_gaps/test_landing_and_batches.py::test_file_sinks_take_path_template_and_batch_date`:
  with pyarrow 19, `pq.read_table` of a hive path adds the `ingest_date` partition column. Passes in
  ci-test (pyarrow 25).
- **E2** `tests/kernel/test_hashing.py::test_rust_equals_reference_on_a_million_values[float16]` and
  `::test_one_and_one_point_zero_hash_equal`: pyarrow 19 has no float16 `if_else` kernel and rejects a
  Python float for float16 (`Expected np.float16 instance`). Passes in ci-test.
- **E3** `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references`:
  depends on test order. `tests/demo/fabric/test_udf.py` imports `fabric.functions`, which loads
  `azure.functions`, so the "no cloud SDK in `sys.modules`" assertion sees it. Run alone it passes.
  `pytest tests/demo/fabric/test_udf.py tests/security/test_credential_refs.py` fails on both
  `dadbd87` and `5c91ea5` (1 failed, 74 passed each). In CI the fabric tests run in their own job,
  so the two never share a process.
- **Finding 27** (`tests/demo_cmd/test_notebook_and_outputs.py`, 2 tests): base in ci-test, 2 failed,
  12 passed. Diff D9.

None of these touch a file this lane changed (`scripts/`, `tests/release/`, this file,
`CHANGELOG.md`). E1–E3 come from the test environment (the fabric requirements pull in pyarrow 19 and
`azure-functions`), not from a code defect on this branch. They are recorded, not fixed (out of scope).

Setup note: the first `make check` also failed `tests/plugins/test_plugin_kit_install.py::test_every_skeleton_builds_a_pure_wheel`
because a non-editable `pip install plugins/*` had left `plugins/*/build/` behind (timestamps before the
test run). After `rm -rf plugins/*/build` and editable installs, it passes in both venvs.
