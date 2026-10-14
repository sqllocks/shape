# W-plug lane status

Target: 0.9.4. No commands promoted (`STABLE_PLUGIN_COMMANDS` is empty). No version changed.

Status: implementation complete. `make check` does not exit 0 in this environment; see "Checks" for exactly which stages fail and why.

## The lead's answer

Extend the shared comparator in this task. Section 6.2 relies on `compare()` rejecting a renamed
positional, so the gap belongs to W-plug. `_compare_positionals()` now also compares positional
names and order, for core and plugin commands alike.

## Choices made

- A positional whose name differs from the baseline at the same index is one problem:
  `<command>: positional 'OLD' was renamed or moved (now 'NEW')`. A reordered pair therefore
  gives two such lines (plus any `nargs` problems the swap causes). Simplest rule; no separate
  "moved" detection.
- An added positional follows the rule the comparator already had: optional (`?`, `*`) passes,
  required (no nargs, `+`, or a digit) is a problem. Unchanged.
- Experimental commands are skipped by `compare()` before positionals are looked at, so their
  renames are not problems. Unchanged.
- The CHANGELOG line for the comparator is under "## Unreleased" / "### Fixed".
- To run the wider test suite I installed the CI plugin set and the `docs` extra into `.venv`
  (environment only, no repository change).

## What changed

Earlier builder (kept): `STABLE_PLUGIN_COMMANDS` and `level()` in `src/shape/cli/stability.py`;
loader refusal for a listed name from another distribution; `snapshot_plugins()`,
`tests/cli/cli_surface_plugins_v1.json` (empty list), `scripts/cli_surface.py --plugins` with
`--write`/`--check`, wired into `make check` and CI, covering both Fabric spellings;
`(experimental)` markers on plugin stubs and loaded plugin parsers; the listed-command exit-class
harness and `gen_exit_codes.py` rows; lockstep coverage test; missing-plugin one-line exit 2;
`docs/CLI_STABILITY.md` and `docs/plugins/stability.md`; promise-page test.

This builder:

- `scripts/cli_surface.py`: `_compare_positionals()` compares names and order.
- `tests/cli/test_cli_surface.py`: four new tests (rename, reorder, unchanged, experimental rename).
- `tests/cli/test_plugin_surface.py`: a `rename` case for `publish` and `fabric publish`.
- `CHANGELOG.md`: one "Fixed" line.

## Red evidence

Earlier builder, before implementation: `pytest -q tests/cli/test_plugin_surface.py tests/plugins/test_stability_promise.py`
exit 1, 33 failed, 29 passed (the failure list is in this file's history at commit 2af003e).

This builder, before the comparator fix (`pytest -q tests/cli/test_cli_surface.py`, exit 1):

```text
FAILED tests/cli/test_cli_surface.py::test_renaming_a_stable_positional_breaks
FAILED tests/cli/test_cli_surface.py::test_reordering_stable_positionals_breaks
2 failed, 26 passed
```

(The unchanged-surface and experimental-rename tests pass before and after, as they should.)
After the fix: `tests/cli/test_cli_surface.py` 28 passed; `tests/cli/test_plugin_surface.py` 35 passed.

## Checks (this session, exit codes seen)

| Command | Exit | Result |
|---|---:|---|
| `python scripts/cli_surface.py --check` | 0 | 0 breaking, 0 notes (core baseline untouched) |
| `python scripts/cli_surface.py --check --plugins` | 0 | 0 breaking, 0 notes |
| `ruff check` / `ruff format --check` (src tests plugins benchmarks/vs_refengine) | 0 / 0 | clean |
| `mypy` | 0 | no issues in 698 files |
| `compileall`, `vulture`, `lint-imports` | 0 | clean |
| `check_requirements`, `check_secrets`, `check_user_facing`, `gen_exit_codes --check`, `gen_failure_modes --check`, `check_shipped_data`, `check_plugin_skeletons`, `check_versions`, `check_release_workflows`, `check_conformance_coverage`, `check_v1_done` | 0 each | clean |
| `pytest -q -m heavy tests/kernel tests/profile tests/streaming` | 0 | 52 passed |
| `SHAPE_KERNEL=python pytest -q tests/kernel` | 0 | 450 passed |
| `cargo fmt --check`, `cargo clippy -D warnings`, `cargo test` | 0 each | clean |
| Core pytest stage of `make check` (`-m "not emulator and not live and not heavy" --cov-fail-under=86`) | 1 | 20 failed, 18424 passed, 25 skipped; coverage 47.43% vs the unchanged 86% gate |

### Why `make check` is not green (not caused by this lane)

- Coverage 47.43% is the same figure measured before any change (earlier builder's baseline).
  Cause not found. The gate is unchanged.
- Of the 20 core failures: 2 in `tests/docs_site` passed once the `docs` extra was installed
  (missing `pymdownx`); `tests/docs/test_brand.py` needs `playwright`, not installed here.
- 9 `tests/docs/test_reference_examples.py` pages (README, CLI, CONTAINER, DBT, GENERATION_ENGINE,
  IMPORTERS, INSTALL, NOTIFICATIONS, PROJECT) still fail with all plugins installed. They fail the
  same way on a clean checkout of `origin/main` (c10923b): 9 failed there too.
- The remaining reference/tutorial failures (SINKS, SIMULATION_FILES_EVENTS, authoring, behavior,
  healthcare-codes, simulation, `06-domain-duckdb`) passed after installing all plugins from
  `plugins/*` (rerun: 9 failed, 7 passed of the previous 16).
- I did not rerun the whole core pytest stage after installing the extra packages, so the final
  count with a complete environment is not measured.

## Open question for the lead

None blocking. Please say whether the 47% coverage and the 9 docs example failures are known
environment issues in this container, or whether they need their own task.
