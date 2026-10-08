# PF-06b — Two trust fixes from PF-06's findings (lane/PF-06b)

Status: **built; checks listed below.** Branch `lane/PF-06b`, cut from `lane/PF-06`. Commit prefix `PF-06b:`.
Owner's standing decision (2026-10-01): fix defects that would harm user trust. No §11 or §2.3 edit, no gate,
tolerance or decision changed, `$REFENGINE_ROOT` untouched. Live Fabric/Synapse runs were not made (O-07).

## Fix 1 — vacuous contract pass

**Defect.** `shape check` / `shape.check` passed a `{"tables": {...}}` contract against a single-table profile
without testing a rule (the contract's `tables` were ignored), and `shape profile <folder>` reads a folder as one
table, so a pipeline that profiled a folder of generated tables passed on damaged data (PF-06's damage case).

**Before / after.**

| Case | Before | After |
|---|---|---|
| `tables` contract, single-table profile | passed, nothing checked | `ContractError` (exit 2): "the contract has a 'tables' object but the profile is a single table …" |
| contract table missing from a dataset profile | `table_exists` violation (unchanged) | unchanged (exit 1) |
| profile table the contract does not name | ignored | `extra_table` violation (exit 1) |
| `shape profile FOLDER` of different tables | one merged, meaningless table | refused (exit 2): files do not share their columns, add `--dataset` |
| `shape profile FOLDER --dataset` | n/a | one table per file, named by the file name; stems must be unique |
| `shape profile FOLDER` of partitions of one table, a Delta table | one table | unchanged |

**Decisions.** Extra profile tables are an **error** (a violation, always: `extra_table`), as extra columns are
when `allow_extra_columns` is false; a table nobody checks can be damaged. The folder choice follows the profile
engine: its folder is one table (partitions, Delta), so that stays the default, `--dataset` is the opt-in for the
other reading, and a folder whose `.csv`/`.parquet` files differ in columns is refused rather than merged
(header/footer read only). Not detected: differing `.jsonl` files (no cheap schema read).

**Tests (fail before, pass after).** `tests/contracts/test_v1_table_correspondence.py` (contract module),
`tests/cli/test_profile_dataset_cli.py` (retail generated to a folder, then profile, then check through the CLI:
pass; the damage case `customer:row_count.min` exit 1; refusal; multi-table contract on a one-table profile exit
2; partitions unchanged). Existing `tests/demo/core/test_contract.py::test_multi_table_contract` still passes
unchanged (its violation order is unchanged: `extra_table` entries come last).

**ADF workaround.** Not removed: it profiles in process (no extra command) and its guard ("the profile holds
exactly the generated tables") is harmless and now redundant only in the vacuous case. Its comments were updated.

## Fix 2 — same-second artifact overwrite

**Defect.** Notebook artifact folders `<outputDir>/<name>/<timestamp>/` had second resolution; two runs in one
second shared a folder and the second overwrote the first's baseline (and Synapse/Fabric `mkdir(exist_ok=True)`
hid it).

**Before / after.** Name `20260930T120000Z` becomes `20260930T120000123456Z` (microseconds), claimed with an
exclusive `mkdir` (Fabric) or an existence check (Synapse: `mssparkutils.fs.exists`); a taken name gets `_2`,
`_3` …. The folder is claimed when the artifacts are written, not at start, so a failed run leaves no empty
"latest" folder. Names still sort in run order and parse with `shape.integrations.fabric.run_folder.parse_run_folder`
(also reads the old name). Earlier one-second folders sort after the runs of their own second and before the next
one. No reader in the repo parsed the name (a repo search found none); baselines are passed by explicit path.

**Code.** `src/shape/integrations/fabric/run_folder.py` (not `shape.integrations.run_folder`: `tests/test_removed_modules.py` pins `shape.integrations` to `fabric` only, and that assertion is unchanged); the four cells that built `stamp` in
`integrations/fabric/notebooks/build_notebooks.py` and `integrations/synapse/build_synapse.py` (notebooks
regenerated, then `ruff format`). ADF has no such timestamped folder (a search for it found none).

**Tests.** `tests/integrations/test_run_folder.py` (name, parse, sort, collision with a frozen clock, 50 real
runs); `tests/demo/fabric/test_notebooks.py::test_two_runs_in_the_same_instant_keep_both_artifacts` and
`tests/demo/fabric/test_synapse.py::…same_instant…` (clock frozen, both artifacts kept, first unchanged). The
Synapse and Synapse-generation fakes gained `fs.exists` (additive).

**[VERIFY]** `mssparkutils.fs.exists` on a real Synapse runtime (not run); no existing assertion was weakened or
changed; test_synapse fakes only gained a method.

## Changelog

`CHANGELOG.md`, one entry under Unreleased.

## Checks (run in this session, after `git fetch` + merge of `origin/build/main-plan`: already up to date)

| Check | Result |
|---|---|
| `ruff check` / `ruff format --check` (src tests plugins benchmarks/vs_refengine) | passed / 602 files formatted |
| `mypy` (strict) | no issues in 265 files |
| `vulture`, `lint-imports`, `check_user_facing`, `bandit -q -r src -ll` | clean / 1 kept / clean / exit 0 |
| START (`shape --version`, median of 7) | 44 ms (limit 300) |
| `profile_1to1/verify.py --impl shape`, `SHAPE_KERNEL=rust` and `python` | exit 0 both |
| `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`, rust and python | 3954 passed each, 45 deselected |
| `pytest tests/demo/fabric tests/demo/content` (shape-fabric venv, unixODBC) | 249 passed |

One earlier run failed `test_legacy_integrations_stay_removed` (a module I had put directly under
`shape.integrations`); the module was moved, the test is unchanged. Not run: `heavy` tests, GitHub Actions jobs,
live Fabric/Synapse runs, `scripts/ci_pure_wheel.sh`.
