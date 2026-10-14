# W-em-1 review: `export-model --source-type sql-database` alias

Reviewer: independent (did not build the lane). Head reviewed: `3c1b539`.
Diff: `git diff origin/main...HEAD`, main = `c10923be9278bc63f2713ac0ed32a548b59a7afb`.
Plan: `origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md`, Decisions and section 4.11 row
`export-model` ("Accept the hyphen as an alias now; both then freeze").

## Verdict: APPROVE

The change does what the row asks and nothing else: `--source-type` accepts `sql-database` as well
as `sql_database`, the hyphen is normalised once before any use, output is byte-identical, help
lists both, docs and CHANGELOG are updated. No blocking findings.

## Commands and exit codes

| # | Command | Exit | Result |
|---|---------|------|--------|
| 1 | `git log -1` | 0 | head `3c1b539`, as expected |
| 2 | `python3.11 -m venv .venv && make bootstrap` | 0 | |
| 3 | `pip install -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` | 0 | |
| 4 | `ruff check src tests plugins benchmarks/vs_refengine` | 0 | |
| 5 | `ruff format --check src tests plugins benchmarks/vs_refengine` | 0 | 2079 files already formatted |
| 6 | `mypy` | 0 | no issues in 698 source files |
| 7 | `python scripts/cli_surface.py --check` | 0 | 0 breaking changes. No `--plugins` option on this base |
| 8 | `pytest -q plugins/shape-fabric/tests -p no:cov` | 1 | 52 failed, 1244 passed, 25 errors |
| 9 | same 77 failing/erroring tests' files on origin/main sources | 1 | the identical 77 ids fail (diffed: identical). All are live/emulator tests (`test_live*`, `*_emulator`, `test_sql_write_path_cli`) failing for missing credentials (for example `live test needs FABRIC_EVENTSTREAM_CONNECTION_STRING`) or no emulator. Sandbox artefact, not this change |
| 10 | `pytest plugins/shape-fabric/tests/test_export_model.py` with origin/main `commands.py` | 1 | 2 failed (the two new tests), 27 passed: red evidence is genuine |
| 11 | same with lane `commands.py` | 0 | 29 passed |
| 12 | `pytest tests/docs/test_reference_examples.py -k fabric` | 0 | 1 passed |
| 13 | manual: `shape fabric export-model retail --source-type {sql_database,sql-database} --source-name Sales` | 0, 0 | `.bim` files `cmp`-identical; stdout identical apart from the output file name; both print `Source type:   sql_database` |
| 14 | manual: `--source-type sql-db` | 2 | rejected, error lists all four choices |

Not counted, per the lead: full core-stage coverage figure and the docs/docs_site failures
(sandbox artefacts that also fail on clean origin/main here).

## Adversarial checks

- **Normalised once, everywhere downstream.** `commands.py:197` rewrites `a.source_type` before both
  `SemanticModelExporter.to_dict` and `export_bim` calls and before the `Source type:` line, the only
  places that read it. No manifest records the source type; the `.bim` is byte-identical (test and
  manual run). The `.replace("-", "_")` is general, but argparse `choices` limits inputs to the four
  listed, so only `sql-database` changes.
- **Both entry points.** `shape export-model` and `shape fabric export-model` share
  `_configure_export_model`/`_run_export_model` (`commands.py:995-996`); both accept both spellings.
- **Other consumers.** `publish -t` (`TARGETS`, `commands.py:33`) is untouched and still takes only
  `sql-database`. The library API (`semantic_model.SOURCE_TYPES`, `src/shape/design/tmdl.py`) still
  accepts only `sql_database`; that is outside the CLI row and unchanged, as the row asks.
- **Tests not weakened.** No skip, xfail, gate, tolerance, regression store or golden change in the
  diff. Removing the normalisation line makes the identical-output test fail (unknown source type),
  so the test does guard the alias.
- **CHANGELOG.** One line, under `## Unreleased` only.
- **CLI surface.** `cli_surface.py` covers core commands only; adding a choice is additive anyway.

## Findings

Blocking: none.

Non-blocking:

1. `docs/plugins/fabric-commands.md:176-177`: the inserted clause leaves two long lines (about 150
   characters) in a paragraph otherwise wrapped near 100. Reflow the paragraph when the page is next
   touched (the plan's stale-text pass for this page is a natural place). No test depends on it.
2. `plugins/shape-fabric/src/shape_fabric/commands.py:197`: the normaliser mutates the parsed
   `Namespace` in place. Harmless here (nothing reads it after the run); a local variable would be
   slightly clearer. No action needed for this lane.
3. `docs/plans/lane_status/W-em-1.md`: says the 47% coverage was not verified as pre-existing. The
   lead has stated it is a known sandbox artefact; the full suite is proven in GitHub CI. No action
   in this lane.
