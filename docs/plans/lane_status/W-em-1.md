# W-em-1: `export-model --source-type sql-database` alias

Target: 0.9.4 only. Branch `lane/W-em-1`. Plan read from
`origin/plan/promotion-093` (Decisions; section 4.11 row `export-model`); no plan content committed.
This resolves the earlier blocker (plan location), which the lead answered.

## What changed

- `plugins/shape-fabric/src/shape_fabric/commands.py`: `--source-type` choices now
  `lakehouse, warehouse, sql_database, sql-database`; the hyphen is normalised to
  `sql_database` before use, so output (including the `Source type:` line) is identical.
  Help text says `sql-database` is an alias, as in `publish -t`.
- `plugins/shape-fabric/tests/test_export_model.py`: two tests: identical stdout/stderr/exit and
  byte-identical `.bim` for both spellings; help lists both spellings.
- `docs/plugins/fabric-commands.md`: alias documented. `CHANGELOG.md`: one line under Unreleased.
- CLI surface snapshot: no update needed. `scripts/cli_surface.py` covers core commands only;
  `export-model` is a plugin command (`plugins/shape-fabric`). `--check` passed inside `make check`.
- No gate, tolerance, regression store, golden corpus or test was changed, skipped or xfailed.

## Red evidence (new tests, before the implementation)

```text
E       assert (2, '', "usag...database')\n") == (0, 'Shape v0...point.\n', '')
E         At index 0 diff: 2 != 0
E       assert ('sql_database' in "usage: shape export-model [-h] ... [--source-type {lakehouse,warehouse,sql_database}] ..." and 'sql-database' in "...")
FAILED plugins/shape-fabric/tests/test_export_model.py::test_the_hyphen_spelling_of_sql_database_gives_identical_output
FAILED plugins/shape-fabric/tests/test_export_model.py::test_the_help_lists_both_spellings_of_sql_database
2 failed, 27 passed
```

After: `29 passed` for the file.

## Setup (repository does not use uv)

`python -m venv .venv && . .venv/bin/activate && make bootstrap` then
`pip install -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka`.
Rust 1.97 was already present. `export-model` lives in `plugins/shape-fabric`
(`grep -rn export-model`).

## Commands and exit codes

1. `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093`: 0
2. venv + `make bootstrap` + plugin `pip install -e`: 0
3. `pytest plugins/shape-fabric/tests/test_export_model.py` before implementation: 1 (2 failed, red above)
4. same, after implementation: 0 (29 passed)
5. `make check`, run 1: 2. Stopped at `ruff check` (E501 in my new test); fixed.
6. `make check`, run 2: **2**. All steps through `scripts/check_v1_done.py` passed (ruff, format,
   mypy, compileall, vulture, lint-imports, scripts, `cli_surface.py --check`). The first pytest
   step then ended with `22 failed, 18262 passed, 45 skipped` and
   `Required test coverage of 86% not reached. Total coverage: 47.25%`, so make stopped there.
   The later steps (heavy tests, `SHAPE_KERNEL=python` kernel tests, cargo fmt/clippy/test) did not run.
7. The 22 failed tests re-run alone with my changes stashed: 1, the identical 22 fail.

## `make check` is not green, and the failures are not from this change

The 22 failures are in `tests/docs/*` and `tests/docs_site/*`: `test_branded_site`,
`test_tutorial[06-domain-duckdb]`, 18 `test_reference_page[...]` cases and two docs-site tests.
Causes seen in the log: `ModuleNotFoundError: No module named 'pymdownx'` (mkdocs build), no Docker
daemon (`/var/run/docker.sock`), and `No module named 'shape_healthcare_codes'`. The environment lacks
the docs extras and Docker; the CI plugin set does not include `shape-domains` docs fixtures.
They fail identically without this change (step 7). I did not make them pass, because that
would need the docs toolchain and Docker.

The 47% coverage figure is not verified as pre-existing: I did not run the full suite on the base
(76 minutes). It is probably a side effect of the failed or skipped docs tests, and this change adds
two lines of code with two covering tests, but I cannot show that.

Needs a decision from the lead: run `make check` where the docs extras and Docker exist (CI), or tell
me which extras to install here.
