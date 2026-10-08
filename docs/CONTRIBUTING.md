# Contributing

Shape is in early access (version 0.9.0); the stable 1.x contract arrives with 1.0.0. Changes
must keep the published `.shape` format readable, keep behavior deterministic, and fail closed on
ambiguous or unsafe input.

## Set up

```bash
python -m venv .venv && . .venv/bin/activate
make bootstrap        # pip install -e ".[dev]" and the domains plugin; builds the Rust kernel
```

Rust stable 1.85 or newer is needed to build the kernel (`shape._kernel`). After changing Rust
code, run `maturin develop --release`.

## Before submitting

```bash
make check
```

`make check` runs what the CI `test` job runs: `ruff check` and `ruff format --check` on
`src tests plugins` and the benchmark harness, `mypy` (strict), `vulture`, `lint-imports`, the
repository checks in `scripts/` (requirements, secrets, user-facing wording, shipped data, plugin
skeletons, conformance coverage), the test suite with its coverage floor, the pure-Python kernel
tests, and `cargo fmt`, `clippy` and `cargo test` for the kernel. For a quicker local run:

```bash
pytest -m "not emulator and not live"
SHAPE_KERNEL=python pytest -m "not emulator and not live"   # the pure-Python kernel
```

Tests marked `emulator` (Kafka, Event Hubs, SQL Server) and `live` run in CI only.

## What a change needs

- A test for new behavior, and a regression test that fails before a bug fix.
- Requirement traceability for new normative behavior (`docs/specs/requirements.yaml`) and a
  conformance test for each normative statement of `docs/specs/SHAPE_2.md`.
- Conformance or security tests for changes to artifacts, evidence, generation, classification
  or query parsing.
- For performance-sensitive changes, before and after numbers from the benchmark harness under
  `benchmarks/`, taken only after its equivalence verifier passes on the timed output.
- The docs the change affects, updated in the same change.

Do not commit credentials, production data or proprietary reference assets. Core must not gain
a mandatory network dependency.

## Documentation site

The site is built with mkdocs from `mkdocs.yml` (`pip install -e ".[docs]"`). Every page under
`docs/` (except the internal `docs/plans/` and `docs/talks/` folders) must be listed in its `nav`. The CLI
reference and the performance page are generated when the site is built, from the command's own
parser and the committed benchmark results; do not write them by hand. A link to a file outside
`docs/` (source code, examples, the changelog) keeps working on the site: it is pointed at the
repository. Before submitting a docs change:

```bash
mkdocs build --strict                                   # any warning fails the build
python scripts/check_doc_links.py --site site           # every link and anchor resolves
python scripts/check_user_facing.py --site site
```

A new call to a Fabric or OneLake API, a new Fabric item type or a new Fabric runtime version needs its row in `docs/FABRIC_PLATFORM.md` first (status, Microsoft Learn source, date). `tests/docs/test_fabric_platform.py` fails on an unlisted surface, and on one listed as preview or retired.

## Stable Python modules

`shape.generation.spec_edit` and `shape.generation.spec_schema` are Stable (`docs/API_STABILITY.md`, "Stable Python modules"). `python scripts/stable_api_compat.py --check` (run by `tests/api/test_stable_api_compat.py` and so by `make check`) compares them with `tests/api/stable_api_baseline.json`.

- After an **additive** change (a new exported name, a new method, a new keyword parameter with a default after the existing ones, a new optional dataclass field with a default at the end), run `python scripts/stable_api_compat.py --write` and commit the baseline. `--write` refuses a breaking change.
- A **breaking change** (anything `--check` reports as `BREAKING`) needs a new major version. Do not edit the baseline to make the check pass; deprecate the old member instead (it keeps working with a `DeprecationWarning` until the next major version).
- A new exported name also goes into the table in `docs/API_STABILITY.md` and into the module's `__all__`.

Before proposing a feature, see [NOT_BUILDING.md](NOT_BUILDING.md) for what the project has decided not to build. The command line's stability promise is [CLI_STABILITY.md](CLI_STABILITY.md), and what 1.0 requires is [V1_DONE.md](V1_DONE.md).
