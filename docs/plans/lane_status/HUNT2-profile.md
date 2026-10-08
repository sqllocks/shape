# HUNT2-profile — second-pass bug hunt, profile area (lane/HUNT2-profile)

Brief: the lane prompt plus `docs/plans/AUDIT_BRIEF.md` (its hard rules apply). Area:
`src/shape/profile/**`, `capture/**`, `diff/**`, `drift/**`, `contracts/**`, `relations/**`,
`proposals/**` and their tests. Branch started from `origin/int/INT-17` (8683435d). No gate,
tolerance, D-xx or T-xx decision changed; §11 and §2.3 untouched; `.github/workflows` untouched
(no workflow change is needed); the pinned RefEngine checkout only read; no force-push or rebase.

The first audit's findings (`origin/lane/AUD-profile`, `docs/plans/lane_status/AUD-profile.md`) were
not re-filed. That lane is **not** merged into INT-17, so its fixes are absent on this branch.

Every fix has a regression test committed before it (failing output in the test commit's message):
`tests/profile/test_hunt2_merge.py` (both kernels), `tests/drift/test_hunt2_drift.py`,
`tests/contracts/emit/test_hunt2_emit.py`, `tests/proposals/test_hunt2_proposals.py`,
`tests/capture/test_hunt2_capture.py` (both kernels). Negative and boundary cases are included.

## Findings, issues and fixes

| # | Issue | Severity | Finding | Fix commit |
|---|---|---|---|---|
| 1 | #592 | high | merging fails on decimal, time-of-day, binary and duration columns, even `--exact-only` | 537f4071 |
| 2 | #616 | high | `mean_shift` / `spread_change` from rounding on a constant float column | 4dae6762 |
| 3 | #617 | high | a drop to zero scores 0 (empty table passes score gates); docs say 1 | fbb8f8fa |
| 4 | #641 | high | contract emit drops columns with no rules; emitted schemas reject them | 8e8e5e9e |
| 5 | #683 | high | `share-bundle verify` passes a signed bundle with an added `data/X.CSV` | **outside area**, filed only |
| 6 | #684 | high | `share-bundle verify` has no size/ratio limit (40 KB bundle, 1.2 GB RSS) | **outside area**, filed only |
| 7 | #593 | medium | sketched merge refuses reordered columns, saying the types differ | a6d15898 |
| 8 | #594 | medium | sketched merge refuses an empty or all-null CSV partition (documented to work) | a6d15898 |
| 9 | #595 | medium | newer `snapshot_version` and `merge` block versions accepted silently | 16359ae9 |
| 10 | #618 | medium | diff of a merged profile reports a false `pattern_change` | 04efbf86 |
| 11 | #619 | medium | joint drift kinds ignore per-column thresholds, `min_severity`, ignore/only | ae171bd0 |
| 12 | #620 | medium | `--only` drops table-level changes (even `*`); `table.column` never matches a single table | ae171bd0 |
| 13 | #621 | medium | a dataset's `row_count_change` does not name its table | ae171bd0 |
| 14 | #642 | medium | DDL constraint names collide across tables; PostgreSQL limit counted in characters | a7912cff |
| 15 | #643 | medium | contract emit writes an invalid empty `CREATE TABLE`, exit 0 under `--strict` | **open**, see below |
| 16 | #644 | medium | contract emit writes `NaN`/`Infinity` into JSON output; GX `mostly` outside 0..1 | b8dff64e |
| 17 | #645 | medium | `proposals contract --merge` rewrites a newer-version or malformed contract | 1b8c76f6 |
| 18 | #646 | medium | no `range` proposal for a CSV date column | 4c1969ca |
| 19 | #685 | medium | `capture_rows` / `shape capture` turn integers into floats (wrong past 2**53) | b660b49c |
| 20 | #686 | medium | `shape capture` output and content id depend on `SHAPE_KERNEL` | **open**, kernel (outside area) |
| 21 | #687 | medium | `capture_columns` raises `KeyError` on an empty bool/null/list column | b660b49c |
| 22 | #688 | medium | `shape capture` of a non-UTF-8 CSV stores `b'...'` reprs as values | b660b49c |
| 23 | #691 | medium | `shape capture -o X.json` has no `format`/`version`; newer versions accepted | **outside area** (CLI writer), filed only |
| 24 | #597 | medium | `to_html()` crashes on an exact-only merged profile | **outside area** (`shape.report`), filed only |
| 25 | #598 | medium | `shape profile merge` has no `--verify`, yet its warning says to pass it | **outside area** (CLI), filed only |
| 26 | #622 | medium | `shape.yml` owner/annotations missing for bare names in dataset drift | **outside area** (`cli/project.py`), filed only |
| 27 | #599 | low | merged min/max keep the partition's tag after type promotion | 16e73da3 |
| 28 | #647 | low | a range proposal clamped at zero is `-0.0` | 4c1969ca |
| 29 | #648 | low | T-SQL `NVARCHAR(n)` past the 4000 limit | **open**, cause in the SQL sink (outside area) |
| 30 | #649 | low | `contract emit --strict --json` still prints the text | **outside area** (CLI), filed only |
| 31 | #689 | low | `shape capture` differs between CSV and JSONL for the same dates | **open**, needs a decision |
| 32 | #690 | low | `shape capture` does not read workbooks or take `shape profile`'s reader options | **outside area** (CLI), filed only |

Test commits: ec71b8fa (#592-#595, #599), 29ee46c1 (#616-#621), 8ab9c09d (#641-#644), d2563f51
(#645-#647), 43104515 (#685, #687, #688). Each fix commit is listed on its issue.

## Left open, and why (for the lead)

- **#643 (an existing test pins the defect).** `tests/contracts/emit/test_emit_api.py::test_an_empty_contract_emits[ddl]`
  and `tests/contracts/emit/test_emit_compat.py::test_the_declared_format_and_version` require
  `emit({}, "ddl")` to succeed with an empty `not_expressed`, which is the reported behaviour (the
  output is `CREATE TABLE "t" (\n\n);`). A fix (refuse a table that names no column, exit 2) was
  written and reverted; its new tests were withdrawn in 9e815bb9. Owner decision needed. Since
  #641's fix, a contract whose columns merely have no rules no longer produces an empty table.
- **#648**: the type is spelled by the built-in SQL sink's type table
  (`src/shape/builtins/sinks/sql.py`, `_column_type`), shared with generation; outside the area.
  Suggested: `NVARCHAR(MAX)` past 4000 for tsql.
- **#686**: the two kernels accumulate mean/m2 of a float column in a different order
  (`shape.kernel`, outside the area; related to #552). Capture reports the kernel's numbers.
- **#689**: needs a decision on one temporal representation across readers (JSONL infers
  timestamps, CSV keeps text), or a change to the `shape capture` help text.
- **#595 follow-up**: the sketch state and the merge block are not registered in `compat.KINDS`, so
  `min_shape_version` does not apply to them; the newer-version error is raised by their own
  readers. Registering them changes the documented support table (`STATE_AND_COMPATIBILITY.md`);
  left to the owner of that table.
- Outside-area defects filed only: #597, #598, #622, #649, #683, #684, #690, #691.
- Not filed (judged intended): whole-valued float64 columns profiled as `integer` give a
  `dtype_change` between two reference profiles; the code comment in `drift/engine.py` says this
  is deliberate. Non-UTF-8 bytes in a Parquet binary column crash `shape.profile`; the pinned
  baseline (pandas `astype(str)`) fails the same way, so it is parity.

## Choices made where the issues left room

- **#616**: means and spreads closer than 1e-9 of the column's magnitude are equal (documented in
  `docs/DRIFT.md`). This is a drift-detection threshold for floating-point rounding, not a gate or
  an equivalence tolerance.
- **#593/#594**: a partition's sketch state whose schema differs from the common one (other order,
  or a `null`-typed column) is conformed through the shared snapshot format (rebuilt by the
  reference twin, read by the active kernel). Merges whose schemas agree take the same path as
  before, so their bytes are unchanged.
- **#620**: a table-level change matches `*`, the table's name and `table.*`; a bare column name
  does not match it. `*` per-column thresholds now apply to a single table's `row_count_change`
  as they already did for a dataset.
- **#621**: `table` is added to table-level dataset records (`row_count_change`, `table_added`,
  `table_removed`); single-table records are unchanged.
- **#641**: a contract column with no rules is a required column (it is what `shape check`
  enforces for it), so every target expresses it.
- **#644**: refused with `ContractError` (exit 2) rather than listed in `not_expressed`: a NaN or
  infinite bound cannot be written to JSON at all.
- **#645**: raises `DecisionError` (the error the existing merge tests expect for a malformed
  contract to merge into), carrying `shape check`'s message.

## Commands and results

Run in this session (2026-10-04) on the lane head before this commit, in a fresh environment built
per plan §1 (`pip install -e '.[dev,streaming,advanced]'`, every plugin under `plugins/` installed
editable, `tests/demo/fabric/requirements.txt`, system `unixodbc` for that suite's ODBC import).

- Merge: `origin/build/main-plan` (contains INT-15), `origin/int/INT-15` and `origin/int/INT-17` are
  all ancestors of the lane head; nothing to merge.
- `ruff check` / `ruff format --check` on `src tests plugins benchmarks/vs_refengine`: clean (1416
  files). `mypy`: no issues (494 files). `compileall`, `vulture`, `lint-imports` (1 kept, 0
  broken), `check_requirements`, `check_secrets`, `check_user_facing` (clean),
  `check_shipped_data`, `check_plugin_skeletons`, `check_conformance_coverage`: pass.
- `make check` pytest step (`not emulator and not live and not heavy`, coverage): 9319 passed,
  1 failed, coverage 92.95% (gate 86%). The failure is
  `tests/streaming/emit/test_faults.py::test_emit_to_two_files`, already filed as **#732**
  (with `shape-healthcare-standards` installed every `--to file://` goes to the FHIR emitter). It
  passes with that plugin uninstalled (checked), which is the CI core job's environment; outside
  this lane's area, untouched by it.
- Remaining `make check` steps, run individually: `pytest -m heavy tests/kernel tests/profile
  tests/streaming` 42 passed; `SHAPE_KERNEL=python pytest tests/kernel` 265 passed; `cargo fmt
  --check`, `cargo clippy -D warnings`: clean; `cargo test`: 34 passed.
- Full `pytest -m "not emulator and not live"`, `SHAPE_KERNEL=rust`: 9633 passed, 1 failed (#732
  above), 18 skipped (optional `great_expectations`/`pandera` not installed).
- Same, `SHAPE_KERNEL=python` (run as three directory shards, 9634 tests in total, same set):
  9633 passed, 1 failed (#732), 18 skipped.
- Equivalence: `profile_1to1/verify.py --impl shape` (datasets from `datasets.py`) exits 0.
  `safe_profile_1to1/verify.py` exits 1 with 120 `widen_bounds ... pattern_contains_rates,
  pattern_rates` mismatches; the identical 120 lines and exit 1 come from the lane's base commit
  8683435d (run from a worktree), so it predates this lane. Already filed as **#529**; the code
  is in `privacy/` and the verifier, outside the area. No other verifier reads the changed
  modules (`capture`, `contracts/emit`, `drift`, `proposals`; the drift verifier path uses
  `shape.fidelity`, not `shape.drift`).
