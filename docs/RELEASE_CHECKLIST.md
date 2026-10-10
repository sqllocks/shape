# Release checklist

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" RELEASE_CHECKLIST
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for RELEASE_CHECKLIST
    ```


How a Shape release is made, from the owner's approval to the checks after it is on PyPI, and
how to recover when something goes wrong. It covers the core distribution `sqllocks-shape` and
every first-party plugin `sqllocks-shape-<plugin>`: they share one version and are released
together. The [release policy](RELEASE_POLICY.md) says what a version number promises; this page
says how one is shipped.

Every step marked **owner** needs the repository owner: publishing, tags, versions, secrets and
environments are never done by a contributor or an automated session.

## What the release workflows do

| Workflow | Trigger | What it does |
|---|---|---|
| `Release build` (`.github/workflows/release.yml`) | called by `Publish`, or run by hand | Checks that every version agrees (`scripts/check_versions.py`). Builds the abi3 platform wheels for every supported target (`wheels.yml`: manylinux_2_28 and musllinux_1_2 for x86_64 and aarch64, macOS arm64 and x86_64, win_amd64; each installed and imported on Python 3.11 and 3.14), the pure-Python `py3-none-any` wheel, the core sdist (and builds the kernel from it) and every plugin's wheel and sdist (`scripts/build_release_dist.py`). Checks the set is complete and passes `twine check --strict`. Writes a CycloneDX 1.6 SBOM per archive, `SHA256SUMS` and the resolved SBOM of `sqllocks-shape[all]` (`scripts/release_sbom.py`), and audits the resolved dependencies with `pip-audit`. Attests every archive and SBOM with Sigstore (build provenance, and the resolved SBOM), then verifies each attestation. Installs `sqllocks-shape[all]` from those archives on every platform and runs the smoke suite (`scripts/release_smoke.py`). Publishes nothing. |
| `Publish` (`.github/workflows/publish.yml`) | **owner**: a manual run (TestPyPI or PyPI), or a `v*` tag (PyPI) | Runs `Release build`, uploads its archives with trusted publishing (no tokens; PEP 740 attestations are added by the upload), checks that the index serves exactly those files by SHA-256 (`scripts/release_index_check.py`), and installs `sqllocks-shape[all]` back from the index on every platform to run the smoke suite. |
| `Container` (`.github/workflows/container.yml`) | a `v*` tag | Publishes the CLI image `ghcr.io/sqllocks/shape:<version>` with a build-provenance attestation. |

The scripts the workflows call run the same way locally; each one's `--help` describes it.

## 1. Before the release (owner, once)

These are owner actions O-08 and O-01 of the build plan. They are done once, not per release.

1. **O-08: make the repository public** (Settings → General → Danger Zone → Change visibility).
   Artifact attestations need a public repository (or GitHub Enterprise Cloud), and the required
   reviewer on the `pypi` environment needs it too.
2. **O-01: GitHub environments.** `publish.yml` publishes each project from its own environment:
   `pypi` and `testpypi` for `sqllocks-shape`, `pypi-<plugin>` and `testpypi-<plugin>` for each
   plugin (for example `pypi-fabric`). GitHub creates an environment the first time a job uses
   it; to protect one, open it in Settings → Environments and add the owner as **required
   reviewer** with deployment branches and tags limited to `v*` tags (at least `pypi`). No
   secrets: trusted publishing needs none.
3. **O-01: trusted publishers.** PyPI accepts a pending publisher for one project name per
   configuration (owner, repository, workflow, environment), which is why every project has its
   own environment. On pypi.org and on test.pypi.org: owner `sqllocks`, repository `shape`,
   workflow `publish.yml`, and the environment of the table.
   - The core and plugin projects are published at 0.9.1. The core publisher is in the project's
     Settings → Publishing, environment `pypi` (TestPyPI: `testpypi`).
   - For each plugin project, configure its own publisher under Settings → Publishing,
     with the project name and its environment:

     | Project | PyPI environment | TestPyPI environment |
     |---|---|---|
     | `sqllocks-shape-behavior` | `pypi-behavior` | `testpypi-behavior` |
     | `sqllocks-shape-databases` | `pypi-databases` | `testpypi-databases` |
     | `sqllocks-shape-dbt` | `pypi-dbt` | `testpypi-dbt` |
     | `sqllocks-shape-domains` | `pypi-domains` | `testpypi-domains` |
     | `sqllocks-shape-eventhubs` | `pypi-eventhubs` | `testpypi-eventhubs` |
     | `sqllocks-shape-fabric` | `pypi-fabric` | `testpypi-fabric` |
     | `sqllocks-shape-healthcare-codes` | `pypi-healthcare-codes` | `testpypi-healthcare-codes` |
     | `sqllocks-shape-healthcare-standards` | `pypi-healthcare-standards` | `testpypi-healthcare-standards` |
     | `sqllocks-shape-integrations` | `pypi-integrations` | `testpypi-integrations` |
     | `sqllocks-shape-kafka` | `pypi-kafka` | `testpypi-kafka` |
     | `sqllocks-shape-simulation` | `pypi-simulation` | `testpypi-simulation` |
     | `sqllocks-shape-sqlserver` | `pypi-sqlserver` | `testpypi-sqlserver` |

   - A project without a matching publisher fails its own job (the others still publish), so
     register all of them before the first run.

## 2. Prepare the release commit

The release commit changes the version and the changelog, and nothing else. It goes to `main`
through a pull request like any other change.

1. **Owner approves the release** and its version. For 1.0.0 the build plan requires gate G8:
   every gate G0–G7 green on the release commit, the seven-night nightly record, and the final
   review done.
2. **Set the version everywhere** (owner-approved change):

[Run this example](#local-example-0).


   It rewrites exactly the places the release version lives (core and plugin `pyproject.toml`
   versions and first-party `==` pins, `__version__`, the kernel's `Cargo.toml` and
   `Cargo.lock`) and checks them; it refuses to start from a tree whose versions already
   disagree. Version strings in documents, test vectors and file formats (the release a format
   first appeared in, a `min_shape_version`) are history and stay as they are.
3. **Changelog:** move the entries under `## Unreleased` into a new section `## 1.0.0 -
   YYYY-MM-DD` (leave an empty `## Unreleased` above it).
4. **Wording that names the version:** review `git grep -n '0\.9\.0' -- ':!docs/plans' ':!tests'`
   and update what is an *install instruction* (for example `docs/INSTALL.md`, the Fabric and ADF
   runbooks, the CLI image tag in `integrations/adf/`). Whether README and the package description
   still say "early access" at 1.0 is the owner's call: below 1.0 the release check requires the
   words; from 1.0 it neither requires nor forbids them.
5. **Check the tree is releasable:**

[Run this example](#local-example-1).


   `--release` requires a final version (no `.dev`, `a`, `b` or `rc`: Fabric's library pickers
   may hide pre-releases), a `## 1.0.0` changelog section and, below 1.0, the words "early access"
   in README.
6. Open the pull request, wait for CI to be green on its head, and merge it into `main`.

## 3. Rehearse without publishing

Actions → **Release build** → Run workflow, on `main`, with *release* ticked. Every job must be
green:

- `versions`, `wheels` (seven targets, each tested on Python 3.11 and 3.14, and the Intel macOS
  test), `python-dists` (the pure wheel passes `tests/demo/core` in a clean venv; the core sdist
  builds the kernel);
- `sbom`: the release set is complete, every SBOM passes the CycloneDX schema, `pip-audit`
  reports nothing, and every archive verifies against its attestation;
- `smoke` and `smoke-musllinux`: `sqllocks-shape[all]` from these archives passes the smoke suite
  with the Rust kernel on Linux (x86_64, aarch64, glibc and musl), macOS (arm64, x86_64) and
  Windows, on Python 3.11 and 3.14.

Download the `release-dist` and `release-sbom` artifacts and keep them with the release notes.
Fix anything red before going further: nothing has been uploaded yet, so a fix costs nothing.

## 4. TestPyPI (owner)

1. Actions → **Publish** → Run workflow, on `main`, repository **testpypi**.
2. Approve the `testpypi` deployment when GitHub asks.
3. Every job must be green: `build` (the whole of step 3 again), `publish`, `index-hashes`
   (TestPyPI serves exactly the built files) and `installed` / `installed-musllinux`
   (`sqllocks-shape[all]==1.0.0` from TestPyPI passes the smoke suite on every platform).

An index never accepts the same file name twice, even after a deletion. If `publish` failed part
way, re-run the failed jobs: the upload skips the files already there, and `index-hashes` proves
each of them is the one this run built. If a problem shows up only after the upload succeeded,
fix it on `main` and rehearse again (step 3); TestPyPI keeps the old 1.0.0 files, so a new
TestPyPI round needs a new version.

## 5. Tag and publish to PyPI (owner)

1. Tag the release commit on `main` (the same commit the TestPyPI run built) and push the tag:

<!-- example: 2 -->

**Needs a GitHub account. Not run in CI.**

```bash
   git tag -a v1.0.0 -m "Shape 1.0.0" <release-commit>
   git push origin v1.0.0
```

<!-- owner: GitHub maintainer — supply the transcript for docs/RELEASE_CHECKLIST.md example 2. -->


   The tag must be `v` plus the version: `check_versions.py --tag` fails the build otherwise.
2. The tag starts **Publish** (to PyPI) and **Container**. Approve the `pypi` deployment.
3. Every job must be green, as in step 4, against PyPI.

## 6. After the release

1. **The index:** `pip index versions sqllocks-shape` lists 1.0.0; the PyPI page of 1.0.0 shows
   the seven platform wheels, the `py3-none-any` wheel and the sdist; each plugin project shows
   its wheel and sdist at 1.0.0. `index-hashes` already compared every file's SHA-256.
2. **Provenance:** each file on PyPI shows its provenance (PEP 740) in the file's details. For a
   downloaded archive:

<!-- example: 3 -->

**Needs a GitHub account. Not run in CI.**

```bash
   sha256sum -c SHA256SUMS                      # from the release-sbom artifact
   gh attestation verify sqllocks_shape-1.0.0-py3-none-any.whl --repo sqllocks/shape
```

<!-- owner: GitHub maintainer — supply the transcript for docs/RELEASE_CHECKLIST.md example 3. -->


   The attestation names `.github/workflows/release.yml` as the signer workflow.
3. **A clean install:** in a fresh Python 3.11 environment, `pip install "sqllocks-shape[all]==1.0.0"`,
   then `shape --version` prints 1.0.0 and `shape suite run smoke --scale small` meets every
   answer key.
4. **Fabric:** the `py3-none-any` wheel is under 28.6 MB (the release build checks it); in a
   Fabric notebook `%pip install sqllocks-shape==1.0.0` works, as `integrations/fabric/RUNBOOK.md`
   describes.
5. **The image:** `docker pull ghcr.io/sqllocks/shape:1.0.0` and `shape --version` inside it.
6. **GitHub release (owner):** create a release from the tag `v1.0.0` with the changelog section
   as its notes, and attach the `release-sbom` artifact's files (the per-archive SBOMs, the
   resolved SBOM and `SHA256SUMS`).
7. **Owner action O-06:** the deprecation notice the build plan lists for after 1.0.0.
8. Announce the release only after steps 1–5 pass.

## 7. Rollback

A version on PyPI cannot be replaced: an uploaded file name can never be uploaded again, even
after the file or the release is deleted. Recovery is always forward.

- **A bad release:** *yank* it rather than delete it (pypi.org → the project → Manage →
  Releases → 1.0.0 → Options → Yank, with the reason). Yank the core and every plugin project at
  that version. A yanked version is skipped by `pip install sqllocks-shape` and by ranges, but
  still installs when pinned exactly (`==1.0.0`), so existing pinned users keep working.
  Deleting would break them and frees nothing.
- **Then fix forward:** the fix goes into `main`, and the next version (for example 1.0.1, with
  `python scripts/set_version.py 1.0.1`) follows this checklist from step 2.
- **A publish that failed part way:** re-run the failed jobs of that workflow run (step 4).
  Never start a new run for the same version after a partial upload: it rebuilds the archives,
  and a rebuilt file can differ from the one already on the index, which `index-hashes` then
  reports.
- **A wrong tag:** if the tag points at the wrong commit and `publish` has not run yet, cancel
  the run, delete the tag (`git push origin :refs/tags/v1.0.0`; owner) and tag the right commit.
  Once anything was uploaded, the version is used: yank and fix forward.
- **The image:** a bad `ghcr.io/sqllocks/shape:1.0.0` is replaced by the fixed release's image;
  mark the package version as deprecated in GitHub Packages rather than deleting it.
- **TestPyPI** is a rehearsal index: a bad TestPyPI upload needs no rollback, only a new version
  for the next TestPyPI round.


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
python scripts/set_version.py 1.0.0 --dry-run   # the 16 files it will change
   python scripts/set_version.py 1.0.0
```

??? info "Output (exit 0)"

    ```text {.expected}
    plugins/shape-behavior/pyproject.toml
    plugins/shape-databases/pyproject.toml
    plugins/shape-dbt/pyproject.toml
    plugins/shape-domains/pyproject.toml
    plugins/shape-eventhubs/pyproject.toml
    plugins/shape-fabric/pyproject.toml
    plugins/shape-healthcare-codes/pyproject.toml
    plugins/shape-healthcare-standards/pyproject.toml
    plugins/shape-integrations/pyproject.toml
    plugins/shape-kafka/pyproject.toml
    plugins/shape-simulation/pyproject.toml
    plugins/shape-sqlserver/pyproject.toml
    pyproject.toml
    rust/shape-kernel/Cargo.lock
    rust/shape-kernel/Cargo.toml
    src/shape/__init__.py
    would change 16 files to version 1.0.0
    plugins/shape-behavior/pyproject.toml
    plugins/shape-databases/pyproject.toml
    plugins/shape-dbt/pyproject.toml
    plugins/shape-domains/pyproject.toml
    plugins/shape-eventhubs/pyproject.toml
    plugins/shape-fabric/pyproject.toml
    plugins/shape-healthcare-codes/pyproject.toml
    plugins/shape-healthcare-standards/pyproject.toml
    plugins/shape-integrations/pyproject.toml
    plugins/shape-kafka/pyproject.toml
    plugins/shape-simulation/pyproject.toml
    plugins/shape-sqlserver/pyproject.toml
    pyproject.toml
    rust/shape-kernel/Cargo.lock
    rust/shape-kernel/Cargo.toml
    src/shape/__init__.py
    changed 16 files to version 1.0.0
    ```

<a id="local-example-1"></a>

### Example 2

This fixture intentionally bumps the source version without adding release notes,
so the version check reports the missing heading. The final command previews the
check target; run `make check` for the actual release validation outside this
documentation fixture, and require every gate to pass.

<!-- example: 1 -->

```bash {.runnable-reference}
python scripts/check_versions.py --release --expect 1.0.0
   python scripts/check_plugin_skeletons.py
   make --silent --dry-run check
```

??? info "Output (exit 0)"

    ```text {.expected}
    FAIL CHANGELOG.md has no '## 1.0.0' section
    plugin skeletons OK (12 distributions, version 1.0.0)
    ruff check src tests plugins benchmarks/vs_refengine
    ruff format --check src tests plugins benchmarks/vs_refengine
    mypy
    python -m compileall -q src/shape
    vulture src/shape scripts/vulture_whitelist.py --min-confidence 80
    lint-imports
    python scripts/check_requirements.py
    python scripts/check_secrets.py
    python scripts/check_user_facing.py
    python scripts/gen_exit_codes.py --check
    python scripts/gen_failure_modes.py --check
    python scripts/check_shipped_data.py
    python scripts/check_plugin_skeletons.py
    python scripts/check_versions.py
    python scripts/check_release_workflows.py
    python scripts/check_conformance_coverage.py
    python scripts/cli_surface.py --check
    python scripts/check_v1_done.py
    pytest -q -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric --ignore=tests/demo/content --cov=shape --cov-fail-under=86
    pytest -q -m heavy tests/kernel tests/profile tests/streaming
    SHAPE_KERNEL=python pytest -q tests/kernel
    cargo fmt --manifest-path rust/shape-kernel/Cargo.toml --check
    cargo clippy --manifest-path rust/shape-kernel/Cargo.toml --all-targets -- -D warnings
    cargo test --manifest-path rust/shape-kernel/Cargo.toml
    ```

This command exits nonzero. Read the diagnostic; this transcript shows a refusal or failed check, not a passing gate.
