# AUD-profile — profiling engine audit-and-fix (lane/AUD-profile)

Brief: `docs/plans/AUDIT_BRIEF.md`. Area: the profiling engine (`src/shape/profile/`, its docs and tests). No gate,
tolerance, D-xx or T-xx decision changed; §11 and §2.3 untouched; `.github/workflows` untouched; the pinned RefEngine
checkout only read; no force-push or rebase of pushed history.

Every fix has a regression test in `tests/profile/test_audit_profile.py` (both kernels where the kernel matters), committed
before its fix with the failing output in the commit message. Expected values that come from the pinned baseline
(pandas 3.0.6, pyarrow 25.0.1, RefEngine 3.0.1 at 422e78df) were produced by running it on the same input.

## Findings, issues and fixes

| # | Issue | Severity | Finding | Fix commit |
|---|---|---|---|---|
| 1 | #128 | critical | zoned timestamps in `s`, `ms`, `ns` units profiled wrong (unit factors swapped) or crash | 452ab5e |
| 2 | #150 | see issue | the joint sample repeats rows, so keys become false dependencies | 093f008 |
| 3 | #151 | see issue | ns timestamps and integers past 2**53 dropped from the joint analysis | 5afe90b |
| 4 | #167 | see issue | a CSV with duplicate or blank header names loses columns or crashes | 45a4715 |
| 5 | #216 | see issue | concurrent `profile()` calls mix up columns (shared fork-pool state) | a224661 |
| 6 | #221 | see issue | impossible ISO dates are typed datetime and rolled forward | 64b4580 |
| 7 | #224 | see issue | a text column holding `NaN` is inferred as float or crashes | 7f650a4 |
| 8 | #225 | see issue | zoned instants in the repeated DST hour merge into one value | ce7724d, d0e6481 |
| 9 | #228 | see issue | a pandas object column of mixed types crashes | ca0fd58 |
| 10 | #229 | see issue | duplicate column names in a table or DataFrame silently lose columns | ca0fd58 |
| 11 | #236 | see issue | integers wider than 64 bits typed integer where the baseline says float | e251b03 |
| 12 | #269 | see issue | zoned date text stops the profile with an error naming no column | 40d0cf3 |
| 13 | #270 | see issue | the date tokenizer is quadratic in the text length | 4d76b26 |
| 14 | #271 | see issue | a folder reads hidden/underscore folders and a nested Delta table's files | 72c8ae7 |
| 15 | #272 | see issue | a file whose name holds `[`, `*` or `?` cannot be profiled | 72c8ae7 |
| 16 | #302 | see issue | `reference_pairs` crashes on NaN/infinity, refuses a `Path`, unclear missing file | a9860e8 |
| 17 | #313 | see issue | the joint analysis counts NaN as a placeholder; warns on an all-infinite column | bda6ca2 |
| 18 | #315 | see issue | pearson, mutual information, conditional means mishandle NaN and numpy numbers | 630b6cd |
| 19 | #316 | see issue | `temporal_profile` / `holiday_lifts` crash on ns timestamps and an empty column | 630b6cd |
| 20 | #317 | see issue | binary (non-text) min/max stored whole, not cut to 256 characters | e217d59 |
| 21 | #318 | medium | ns timestamps lose their nanoseconds; distinct values merge | cf859bb |
| 22 | #319 | medium | a workbook source silently ignores `version`, `as_of`, `reference_pairs`, CSV options | b0e6467 |
| 23 | #320 | medium | CSVs the baseline reads are refused (trailing delimiter, header-only, > 38 digits) | b0e6467 |
| 24 | #321 | medium | `shape.profile(folder)` merges unrelated files; CLI check is order-sensitive, skips JSONL | b0e6467 |
| 25 | #322 | medium | engine document not strict JSON when float moments overflow; kernels disagree | b0e6467 (document part) |
| 26 | #323 | medium | `distribution_params` occasionally differ in the last digit between threaded runs | **open**, see below |
| 27 | #324 | low | unclear errors for unreadable inputs (encoding, empty file, zone, `PROFILE_THREADS`, `file://`, `~`, ...) | b0e6467 |
| 28 | #325 | low | Cramér's V empty categories, dictionary null/repeat, long durations, live `Profile.tables`, warning location, late `reference_pairs` errors | b0e6467 |
| 29 | #326 | low | `infer.py` ignores the zone for date/datetime; joint view cost grows with column count | b0e6467 |

Test commits: each fix commit above is preceded by its "regression test(s) for #N" commit (`git log --grep 'regression
test' lane/AUD-profile`); the last one is 1fc0841 (#319-#326, 59 failures before the fix, listed in its message).

## Left open, and why

- **#323** (nondeterministic last ulp of `distribution_params` in about 4% of threaded fresh-process runs): the cause was
  not found (serial runs, in-process repeats and isolated `fit_distribution` calls never differ), so there is nothing to
  fix with confidence. Left filed.
- **#322, kernel part**: the document is now strict JSON in both kernels (overflowed moments are `null`), but the two
  kernels still compute different moments for such a column (Rust: `mean` unknown; Python: `mean` 0.0 and an infinite or
  NaN `m2` depending on batching). That is in `shape.kernel`, outside this lane; said on the issue.
- **#336** (`infer_column_type` 5x slower than profiling on text columns) was filed by another lane; not taken here.

## Choices made where the issues left room

- **#320, trailing delimiter**: pandas reads a file whose first data row has one field more than the header with the
  first field as the row index (verified on the pinned baseline: `a,b\n1,2,\n3,4,\n` gives `a = [2, 4]`, `b` all
  null). Shape does the same. A file whose extra field appears only on a later row is still refused, as pandas refuses it.
- **#320, wide integers**: read up to 76 digits (Arrow's `decimal256(76, 0)`), typed `float` past 64 bits as before
  (#236); wider ones are refused with a message naming the column and the 76-digit limit.
- **#321**: `shape.profile(folder)` refuses only files with **no column in common** (overlapping files are read as
  before); `shape profile FOLDER` keeps its stricter documented rule (any difference in the column *set* is refused,
  `--dataset` suggested) so existing CLI behaviour and tests are unchanged; both now ignore column order and read JSONL.
- **#324, `PROFILE_THREADS`**: `0` keeps meaning "every core" (backward compatible); anything that is not a
  non-negative integer is refused by name. `PROFILE_THREADS=1` still holds pyarrow's process pools at one thread while
  `shape.profile` runs (the 1T benchmark mode depends on it) and restores their sizes when it returns.
- **#325, dictionary columns**: the baseline refuses a dictionary with a null value (pandas: "Categorical categories
  cannot be null") and a repeated value; Shape now profiles both correctly instead of miscounting.

## Commands and results

Environment: `$SHAPE_VENV` built per §1.1 (`pip install -e ".[dev]"`, Rust extension built by maturin), every
first-party plugin installed editable (`plugins/shape-{domains,simulation,kafka,eventhubs,sqlserver,databases,fabric}`),
`tests/demo/fabric/requirements.txt` installed, unixODBC from apt (the Fabric UDF tests import `pyodbc`), pinned RefEngine
per §1.2 (`benchmarks/vs_refengine/setup_refengine.sh`). Python 3.11.15, numpy 2.4.6, pyarrow 19.0.1. All on the merge of
`origin/build/main-plan` (5c91ea5) into this branch (526564c; clean merge).

| Command | Result |
|---|---|
| `pytest tests/profile/test_audit_profile.py` | 163 passed (both kernels via the `kernel` fixture); 59 of them failed on the tree before the fixes (1fc0841's message) |
| `pytest -m "not emulator and not live and not heavy" tests/profile tests/cli tests/excel tests/joint` | 839 passed |
| `datasets.py` then `verify.py --impl shape --refresh` (T-22 parity, Rust kernel) | exit 0, 49/49 PASS |
| `SHAPE_KERNEL=python verify.py --impl shape` | exit 0, 49/49 PASS |
| `make check`: ruff check, ruff format --check, mypy, compileall, vulture, lint-imports, check_requirements, check_secrets, check_user_facing, check_shipped_data, check_plugin_skeletons, check_conformance_coverage | all pass (mypy: no issues in 436 files) |
| `make check`: coverage step (`not heavy`, `--cov-fail-under=86`) | 3 failed, 6902 passed, 17 skipped; coverage 92.25% (gate 86% reached). The 3 failures are pre-existing, see below |
| `make check`: `pytest -m heavy tests/kernel tests/profile tests/streaming` | 1 failed (`test_hashing ... [float16]`, pre-existing), 41 passed |
| `make check`: `SHAPE_KERNEL=python pytest tests/kernel` | 2 failed (the two `test_hashing` ones, pre-existing), 263 passed |
| `make check`: `cargo fmt --check`, `cargo clippy --all-targets -D warnings`, `cargo test` | all pass (34 tests) |
| `python scripts/check_user_facing.py` | exit 0 |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | 4 failed, 7197 passed, 17 skipped (32 min) |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | 4 failed, 7197 passed, 17 skipped (1 h 52 min; `test_bounded_mode_memory_does_not_grow_with_rows` alone takes about 45 min on the Python kernel) |

### The failures are pre-existing (same environment, `origin/build/main-plan` 5c91ea5)

Run from a worktree of `origin/build/main-plan` with the same venv (`PYTHONPATH=<worktree>/src`), the `make check` test
step gives **the same 3 failures** (3 failed, 6739 passed), and the float16 test fails there too:

- `tests/iss_gaps/test_landing_and_batches.py::test_file_sinks_take_path_template_and_batch_date` and
  `tests/kernel/test_hashing.py::test_one_and_one_point_zero_hash_equal`,
  `::test_rust_equals_reference_on_a_million_values[float16]` (`ArrowTypeError: Expected np.float16 instance`): this
  environment resolves pyarrow 19.0.1, the case filed as #333.
- `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references`: with
  `fabric-user-data-functions` and unixODBC installed, an earlier test's import of `shape.integrations.fabric.udf` loads
  `azure.functions` into the test process, and this test asserts that no `azure*` module is loaded at all. It passes
  alone (on both trees). Not in this lane's area; recorded here for the lead (the test-suite issues #330-#335 cover
  similar order and environment dependencies).

No test was skipped, deselected or xfailed; the 13/55 deselected are the `emulator`/`live` (and, in the coverage step,
`heavy`) markers the commands select out.
