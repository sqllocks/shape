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
