# Contributing

Status: available.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](docs/contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" CONTRIBUTING
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for CONTRIBUTING
    ```


**Early access 0.9.1.** Profiling, contracts and drift are available and supported. Generation from a profile is available and is being hardened. Other surfaces are experimental unless labelled available. The 1.x promises describe future policy.

You contribute code and documentation under MIT. Sign off every commit with a
`Signed-off-by: Your Name <your email>` trailer. The sign-off certifies the
[Developer Certificate of Origin](https://developercertificate.org/). Use your own identity.
The DCO workflow checks every commit in a pull request.

## Prepare a change

Open an issue for a platform, domain or feature request. Describe the input, expected output
and a small reproducible case. Do not include credentials, production rows or proprietary
reference assets. Maintainers
review behavior against specifications and conformance tests. See [governance](GOVERNANCE.md).

Keep the published `.shape` format readable, deterministic behavior, sensitivity and
provenance propagation, and fail-closed input handling. Core must not gain a mandatory network dependency. Add a regression test for a bug
and conformance tests for new normative behavior. Document the changed behavior.

## Local development

You need Python 3.11 or newer and Rust stable 1.85 or newer to build the native kernel.
Create a virtual environment and install the development dependencies and domains plugin:

```bash
python -m venv .venv
. .venv/bin/activate
make bootstrap
```

After changing Rust code, run `maturin develop --release` to rebuild `shape._kernel`.
Before submitting a change, run the repository checks:

```bash
make check
```

The target runs Ruff lint and format checks on `src/`, `tests/`, `plugins/` and the
benchmark harness, strict mypy, Vulture, import checks and the repository checks in
`scripts/`. It also runs the core tests with their coverage floor, heavy tests,
the pure-Python kernel tests, and Rust formatting, clippy and tests. The repository
checks cover requirements, secrets, user-facing wording, shipped data, plugin
skeletons, versions and conformance coverage. For a quicker local run:

```bash
pytest -m "not emulator and not live"
SHAPE_KERNEL=python pytest -m "not emulator and not live"
```

Tests marked `emulator` need their local services; tests marked `live` need configured
accounts. Their CI and Nightly jobs prepare those prerequisites. Performance-sensitive
changes need before and after measurements from `benchmarks/`, recorded only after
the equivalence verifier passes on the timed output.

New normative behavior needs traceability in `docs/specs/requirements.yaml` and a
conformance test for each affected statement in `docs/specs/SHAPE_2.md`. Artifact,
evidence, generation, classification and query-parsing changes also need the relevant
conformance or security tests. Update the affected docs in the same change.


The docs workflow installs the core and local domains and databases plugins from this checkout,
runs the tutorial harness and API coverage checks, and builds MkDocs in strict mode. It also
checks wording and links. Use [the documentation style guide](docs/contributing/DOCS_STYLE.md)
for a new page. The harness runs only blocks explicitly marked runnable, with their shown output.

A new Fabric API, item type or runtime needs its entry in [the platform inventory](docs/FABRIC_PLATFORM.md)
with the shipped behavior, Microsoft Learn source and date.
`tests/docs/test_fabric_platform.py` rejects unlisted surfaces and entries marked preview or
retired. Keep account-dependent execution claims out of docs.

## Public interfaces

The package's `shape.__all__` is the top-level public Python surface. Generated API documentation
and CI cover every name and its docstring. Modules with a separate `__all__` are also included.
The stable modules `shape.generation.spec_edit` and `shape.generation.spec_schema` have a
compatibility baseline in `tests/api/stable_api_baseline.json`, described in
[the API policy](docs/API_STABILITY.md). The check
`python scripts/stable_api_compat.py --check` runs through
`tests/api/test_stable_api_compat.py` and `make check`.

- After an additive change, run `python scripts/stable_api_compat.py --write` and commit
  the baseline. Additive changes include exported names, methods, trailing optional
  keyword parameters and trailing dataclass fields with defaults. The writer refuses
  breaking changes.
- A breaking change needs a new major version. Keep the old member working with a
  `DeprecationWarning` until the next major version; do not erase a compatibility
  failure by editing the baseline.
- Add a new exported name to the table in `docs/API_STABILITY.md` and the module's
  `__all__`.

See [scope and limits](docs/NOT_BUILDING.md) before proposing a new feature,
[the CLI policy](docs/CLI_STABILITY.md) for command stability and
[the 1.0 checklist](docs/V1_DONE.md) for the release requirements.

## Documentation site

Install the full requirements from `requirements-docs.txt` with Python 3.12 or newer
and the core and first-party plugins from this checkout. The site reads `mkdocs.yml`;
its hooks generate the CLI reference from the parser, the API reference from exported
names and the performance reference from committed, verified benchmark results.
Do not edit generated output by hand.

List public pages in the navigation. Internal material under `docs/plans/` and
`docs/talks/` stays outside the public site. Links to source files and examples
outside `docs/` are rewritten to their repository locations when the site is built.
Before submitting docs changes, run:

```bash
mkdocs build --strict
python scripts/check_doc_links.py --site site
python scripts/check_user_facing.py --site site
```

Warnings fail the strict build. The link check validates source and site links;
the wording check validates the public source and rendered pages.

## Review

A pull request explains the concrete problem, changed behavior, tests and limitations. Keep
code, tests and affected docs in the same change. Maintainers decide whether the change is ready;
the repository owner merges. Security changes may tighten input handling. Report security
issues through [the security policy](SECURITY.md).

## Related

[Documentation style](docs/contributing/DOCS_STYLE.md) · [Governance](GOVERNANCE.md) ·
[Future CLI policy](docs/CLI_STABILITY.md) · [Future API policy](docs/API_STABILITY.md)

## Development command examples

The source-test environment prepares the inputs for these commands. The bootstrap uses
already installed development dependencies. The dry run lists the checks without running
them; use `make check` above for the complete verification. The kernel examples give you
a smaller local test target.

<!-- example: 0 -->

```bash {.runnable-reference}
python -m venv --copies --system-site-packages contributor-env
. contributor-env/bin/activate
PIP_NO_DEPS=1 PIP_QUIET=1 make --silent bootstrap
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```

<!-- example: 1 -->

```bash {.runnable-reference}
make --silent --dry-run check
```

??? info "Output (exit 0)"

    ```text {.expected}
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

<!-- example: 2 -->

```bash {.runnable-reference}
pytest -q -p no:warnings -m "not emulator and not live and not heavy" tests/kernel
SHAPE_KERNEL=python pytest -q -p no:warnings -m "not emulator and not live and not heavy" tests/kernel
```

??? info "Output (exit 0)"

    ```text {.expected}
    ............................................................................................ [ 22%]
    ............................................................................................ [ 45%]
    ............................................................................................ [ 68%]
    ............................................................................................ [ 91%]
    ....................................                                                         [100%]
    404 passed, 46 deselected in 91.62s (0:01:31)
    ............................................................................................ [ 22%]
    ............................................................................................ [ 45%]
    ............................................................................................ [ 68%]
    ............................................................................................ [ 91%]
    ....................................                                                         [100%]
    404 passed, 46 deselected in 108.26s (0:01:48)
    ```

<!-- example: 3 -->

```bash {.runnable-reference}
NO_MKDOCS_2_WARNING=1 mkdocs build --strict              # any warning fails the build
python scripts/check_doc_links.py --site site           # every link and anchor resolves
python scripts/check_user_facing.py --site site
```

??? info "Output (exit 0)"

    ```text {.expected}
    INFO    -  Cleaning site directory
    INFO    -  Building documentation to directory: /tmp/docs-reference-runs/CONTRIBUTING/site
    INFO    -  The following pages exist in the docs directory, but are not included in the "nav" configuration:
      - BRANDING.md
      - DETERMINISM.md
      - EXTERNAL_REFERENCE_ASSETS.md
      - LOCATION_AS_CODE.md
      - NOT_BUILDING.md
      - PERFORMANCE.md
      - SHAPE_MANIFESTO.md
      - specs/LOCATION_SPEC.md
      - specs/ONE_ZERO_CONTRACT.md
      - specs/PACK_SPEC.md
      - specs/SHAPE_1_0.md
    INFO    -  Documentation built in 16.75 seconds
    check_doc_links: 0 broken link(s) in the sources and built site
    check_user_facing: clean
    ```
