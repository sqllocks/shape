# W-gameday-1 — `gameday run --dry-run --json` document

## The lead's answer (decided)

`actions` holds exactly one entry, the `-o/--output` directory, built with
`machine._file_action(output)`. Report files, round directories, copied tables and
check outputs are not listed (output layout is not frozen; check outputs need real runs).
The document is `machine.dry_run_document("gameday run", actions)`, printed with sorted keys.
The plan is still validated by the native dry run. A bad plan, a disallowed command,
non-local data or an unreadable table exits 2 and prints
`dry_run_document("gameday run", [], <redacted error>)`. A valid dry run exits 0.
The text `--dry-run` output is unchanged. `gameday run` stays in `NATIVE_DRY_RUN`.

## What changed

- `src/shape/cli/gameday.py`: `--dry-run --json` goes to `_dry_run_document`, which runs
  the native dry run for validation, then prints the document (or the error document, exit 2).
- `src/shape/cli/machine.py`: `"gameday run"` added to `NATIVE_DRY_RUN_DOCUMENT` so the
  command's own document is not wrapped in the result envelope (as `emit` and `stream`).
  `SPECS` and `NATIVE_DRY_RUN` are untouched.
- `tests/scenario/test_gameday.py`: two new tests (shape and keys, one action, nothing
  written, exit 0; bad plan gives exit 2, empty `actions`, an `error` key).
- `CHANGELOG.md`: one line under "## Unreleased".
- `docs/GAMEDAY.md` not changed. No gate, tolerance, regression store or golden corpus touched.

## Red evidence (before the implementation)

`pytest tests/scenario/test_gameday.py -k dry_run_json`:

```
E  AssertionError: assert ['command', '...payload', ...] == ['actions', '...t', 'version']
E  KeyError: 'actions'
FAILED ...::test_dry_run_json_is_the_generic_dry_run_document
FAILED ...::test_dry_run_json_of_a_bad_plan_exits_two_with_an_error
2 failed, 72 deselected
```

After the change: `pytest tests/scenario/test_gameday.py tests/cli/test_ci_flags_coverage.py
tests/docs -k "gameday or dry or ci_flags or GAMEDAY"`: 82 passed (exit 0), including the
GAMEDAY reference-example test and the CI-flags coverage test, unchanged.

## Commands and exit codes

| Command | Exit |
| --- | --- |
| `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093` | 0 |
| `python -m venv .venv`; `make bootstrap` | 0 |
| `pytest tests/scenario/test_gameday.py -k dry_run_json` (red) | 1 |
| same tests plus CI-flags and docs subset after implementing | 0 (82 passed) |
| `make check` (first run, plugin modules missing) | 2: collection errors, `shape_fabric` not importable in `tests/builtins/test_shape_sm_script.py`, `tests/scale/test_kql_sink_uri.py` |
| `pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` (the CI plugin set) | 0 |
| `make check` (second run) | 2 (make reports Error 1 from pytest) |
| `pytest` of `test_reference_page[CLI]` and `tests/docs_site/test_docs_site.py`, with and without my changes | 1 both ways: 3 failed on the unmodified tree |

## `make check` result: NOT green

Lint, format, mypy and the other pre-pytest checks passed. The core suite: 20 failed,
18386 passed, 25 skipped, 69 deselected; the coverage gate also reports 47 < 86 (`ERROR: Coverage
failure`), apparently because the run stopped at the pytest failures. Heavy tests, pure-Python
kernel tests and Rust checks did not run.

Failing tests: `test_branded_site`, `test_tutorial[06-domain-duckdb]`, 16 `test_reference_page[...]`
pages (CONTRIBUTING, README, CLI, CONTAINER, DBT, GENERATION_ENGINE, IMPORTERS, INSTALL,
NOTIFICATIONS, PROJECT, SIMULATION_FILES_EVENTS, SINKS, authoring, behavior, healthcare-codes,
simulation) and two `tests/docs_site` tests. I confirmed the CLI page and the docs_site tests
fail the same way without my changes (the CLI transcript differs in Python 3.13 traceback
caret rendering). The baseline before my edits, recorded earlier, also had failures (17 failed,
16 errors) from the environment (missing `playwright`, cargo, file permissions). I did not
investigate each remaining one individually; none touch gameday, and the GAMEDAY page passes.
I did not skip or alter any of them.
