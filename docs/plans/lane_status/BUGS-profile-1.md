# BUGS-profile-1 — issues #286, #296, #336, #323 — status

Branch `lane/BUGS-profile-1` (from `build/main-plan` at 5c91ea5). Every issue was reproduced on
this branch first by a regression test committed before its fix: each fix commit is preceded by a
`regression tests for #N` commit whose message shows the failing run. No D-xx/T-xx decision, gate
or tolerance was touched; nothing is escalated under §0.4; §11 and §2.3 are unedited;
`$REFENGINE_ROOT` is untouched; `.github/workflows` is unedited (no workflow change is needed). The
issues were not commented on, labelled or closed.

## #296 Azure account name spliced into a DuckDB connection string — reproduces; fixed

- Repro: the DuckDB Delta fallback took the account name from the `abfss://` host and wrote it
  into `AccountName={account};...` unchecked, so `acct;EndpointSuffix=attacker-host` added keys.
- Fix (`src/shape/profile/reference/delta_fallback.py`): the name is lower-cased and refused with
  a `ValueError` naming the expected form unless it is a storage account name (`[a-z0-9]{3,24}`).
  Without credentials no secret is built and the host is not inspected, as before.
- Tests: `tests/profile/test_delta_fallback.py` (22 failed before the fix).

## #286 Parquet decompression bomb, no input budget — reproduces; fixed

- Repro: a few-KB Parquet file of 2M constant int64 rows is read whole by every reader.
- Fix: new `src/shape/io/budget.py`. Optional `SHAPE_MAX_INPUT_ROWS` / `SHAPE_MAX_INPUT_BYTES`
  (unset or blank: no check and no footer read; anything but a positive integer is refused by
  name). Before any data page is read, the rows and decoded size the footers declare are summed
  over the files of one source; over either budget raises `InputBudgetError` (a `ValueError`,
  CLI exit 2). Checked in `shape.io.open_source` (engine), the reference profiler's sources
  (`shape.profile`, `load_columns`) and table readers (`profile_parquet`, `profile_dataset`).
- Docs: `docs/PROFILING_NOTES.md` "Input budget for untrusted files"; `docs/THREAT_MODEL.md` row
  and residual risk R8 (budgets off by default; compressed CSV/JSONL and dictionary string
  columns are bounded by the process memory limit).
- Tests: `tests/io/test_input_budget.py` (32 failed before the fix).

## #323 fits differ between fresh threaded processes — reproduces; fixed

- Root cause: the native kernel marked its numpy `exp`/`log` hooks installed before
  `fit::set_scalar_hooks` ran, with Python calls in between; a thread whose first fit started in
  that window fitted with libm `exp`/`ln` (one ulp off numpy's SIMD loops for some inputs), and
  the lognormal fit amplified it.
- Fix (`rust/shape-kernel/src/lib.rs`): an `AtomicBool` published (Release) only after the hooks
  are set and checked (Acquire) on entry; a thread that finds it unset installs them itself; no
  lock is held across the Python calls; every `OnceLock` keeps its first value.
- Tests: `tests/kernel/test_fit_determinism.py` (fresh processes with a tiny switch interval;
  2 failed before the fix).

## #336 `infer_column_type` parses every distinct text value — reproduces; fixed

- Fix (`src/shape/profile/infer.py`): follows the profiler's order — ISO text parsed in bulk by
  Arrow, anything else by `_all_parse_datetime` on the distinct values, which stops at the first
  value that does not parse.
- Equivalence first: old and new agree on all 922 columns of the 26 T-22 files (0 mismatches),
  then timing (earlier session, fresh process per run, median of 3, under `bench.lock`):
  inference over every column 24.22 s → 0.91 s.
- Tests: `tests/profile/test_infer_cost.py` (counts parser calls rather than timing; 4 failed
  before the fix).

CHANGELOG: four `### Fixed` entries under Unreleased.

## Results (final tree; run in this session)

Fresh container, Python 3.11.15, `pip install -e '.[dev,streaming,advanced]'` with the
domains, kafka, eventhubs, sqlserver and fabric plugins (the native kernel built in release from
this tree), `TMPDIR` set to a private directory.

- `make check`: exit 0. ruff, ruff format, mypy (437 files), compileall, vulture, import-linter
  (1 kept, 0 broken), check_requirements, check_secrets, `check_user_facing: clean`,
  check_shipped_data, check_plugin_skeletons, check_conformance_coverage; main suite 6879 passed,
  2 skipped, coverage over the 86% gate; heavy 42 passed; `SHAPE_KERNEL=python` tests/kernel 269
  passed; cargo fmt --check, clippy `-D warnings` clean; cargo test 34 passed.
- Full suite, `pytest -m "not emulator and not live" --ignore=tests/demo/fabric
  --ignore=tests/demo/content` (heavy included):
  - `SHAPE_KERNEL=rust`: 6921 passed, 2 skipped (`shape_databases` not installed), 0 failed.
  - `SHAPE_KERNEL=python`: 6921 passed, 2 skipped, 0 failed.
- `benchmarks/vs_refengine/profile_1to1/verify.py --impl shape` on every T-22 dataset (generated
  with `datasets.py`, pinned baseline at 422e78d set up by `setup_refengine.sh`): exit 0 with
  `SHAPE_KERNEL=rust` and with `SHAPE_KERNEL=python`.
- Not run: the Fabric demo suites (`tests/demo/fabric`, `tests/demo/content`, excluded by
  `make check` too; `tests/demo/fabric` needs `nbformat`), emulator and live tests. No timing was
  re-taken in this session; the #336 timings above are from the session that made the fix, taken
  after its equivalence check.

## Left for the lead / owner

- `make check`'s full-suite step needs `plugins/shape-fabric` (and so `shape-eventhubs`, which it
  pins) installed for two `tests/demo_cmd/test_notebook_and_outputs.py` tests; `make bootstrap`
  installs only `shape-domains`, so a fresh `make bootstrap && make check` fails those two. This
  is outside the lane (no demo code touched) and is not changed here.
