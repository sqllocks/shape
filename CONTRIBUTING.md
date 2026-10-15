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


**Early access 0.9.3.** Profiling, contracts and drift are available and supported. Generation from a profile is available and is being hardened. Other surfaces are experimental unless labelled available. The 1.x promises describe future policy.

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

Published packages support Python 3.11 or newer. The complete development and documentation
environment needs Python 3.12 or newer and Rust stable 1.85 or newer to build the native kernel.
Install the pinned uv version and bootstrap the locked workspace:

```bash
python3 -m pip install --user "uv==0.8.17"
make bootstrap
```

Bootstrap runs `uv sync --locked --group docs --group dbt --group benchmark`; core remains the
maturin root project and the 12 first-party plugins are editable workspace members. No shell
activation is needed. For core-only work on Python 3.11, use `uv sync --locked`.
Run tools with `uv run --locked`, adding `--group docs` for documentation tools. Never run
`pip install` in the uv development environment. After changing Rust code, run
`uv run --locked maturin develop --release` to rebuild `shape._kernel`.

Edit development dependencies in `[dependency-groups]` in `pyproject.toml`, run `make lock`
(`uv lock`), and commit `uv.lock` in the same commit. `make lock-check` rejects a stale lock.
To deliberately upgrade one dependency, run `uv lock --upgrade-package X`, review the lock
changes and run `make bootstrap` and the checks again. Published extras are retained for
compatibility; do not change their ranges when editing development groups.

The Fabric SDK requires PyArrow below 20 while docs and benchmarks pin 25.0.1. These groups
are explicitly incompatible in the universal lock. Run Fabric tests in a separate environment:
`UV_PROJECT_ENVIRONMENT=.venv-fabric uv sync --locked --group fabric-demo`, then use the same
variable with `uv run --locked --no-sync`. Do not combine `fabric-demo` with `docs` or
`benchmark`. Bootstrap selects the groups needed by `make check`.

Without uv, the unsupported-but-working fallback is a separate virtual environment:

```bash
python -m venv .venv-pip
. .venv-pip/bin/activate
make bootstrap-pip
```

On Windows, select `.venv-pip/Scripts/python.exe` with `make bootstrap-pip PYTHON=...`.
The fallback uses the previous pip editable install and does not provide lockfile guarantees.

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
uv run --locked pytest -m "not emulator and not live"
SHAPE_KERNEL=python uv run --locked pytest -m "not emulator and not live"
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

Run `make bootstrap` with Python 3.12 or newer. `requirements-docs.txt` is a generated
compatibility export of the `docs` group for the example harness and pip consumers;
`make docs-export-check` checks it against the lock. Regenerate it with
`uv run --locked python scripts/check_docs_requirements.py --write` after changing the lock.
The site reads `mkdocs.yml`;
its hooks generate the CLI reference from the parser, the API reference from exported
names and the performance reference from committed, verified benchmark results.
Do not edit generated output by hand.

List public pages in the navigation. Internal material under `docs/plans/` and
`docs/talks/` stays outside the public site. Links to source files and examples
outside `docs/` are rewritten to their repository locations when the site is built.
Before submitting docs changes, run:

```bash
uv run --locked --group docs mkdocs build --strict
uv run --locked --group docs python scripts/check_doc_links.py --site site
uv run --locked --group docs python scripts/check_user_facing.py --site site
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

The source-test environment prepares the inputs for these commands. The bootstrap preview
prints the locked setup command. The dry run previews the local test
command without running it; use `make check` above for the complete verification.
The kernel examples give you a smaller local test target.

<!-- example: 0 -->

```bash {.runnable-reference}
make --silent --dry-run bootstrap UV=uv
```

??? info "Output (exit 0)"

    ```text {.expected}
    uv sync --locked --group docs --group dbt --group benchmark
    ```

<!-- example: 1 -->

```bash {.runnable-reference}
make --silent --dry-run test UV=uv
```

??? info "Output (exit 0)"

    ```text {.expected}
    uv run --locked --group docs --group dbt --group benchmark pytest -m "not emulator and not live"
    ```

<!-- example: 2 -->

```bash {.runnable-reference}
uv run --active --locked --no-sync pytest -q -p no:warnings -m "not emulator and not live and not heavy" tests/kernel
SHAPE_KERNEL=python uv run --active --locked --no-sync pytest -q -p no:warnings -m "not emulator and not live and not heavy" tests/kernel
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
NO_MKDOCS_2_WARNING=1 uv run --active --locked --no-sync --group docs mkdocs build --strict              # any warning fails the build
uv run --active --locked --no-sync --group docs python scripts/check_doc_links.py --site site           # every link and anchor resolves
uv run --active --locked --no-sync --group docs python scripts/check_user_facing.py --site site
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
      - SHAPE_MANIFESTO.md
      - specs/LOCATION_SPEC.md
      - specs/ONE_ZERO_CONTRACT.md
      - specs/PACK_SPEC.md
      - specs/SHAPE_1_0.md
    INFO    -  Documentation built in 16.75 seconds
    check_doc_links: 0 broken link(s) in the sources and built site
    check_user_facing: clean
    ```
