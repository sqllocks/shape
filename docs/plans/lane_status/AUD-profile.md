# AUD-profile — profiling engine audit-and-fix (lane/AUD-profile)

Brief: `docs/plans/AUDIT_BRIEF.md`. Area: the profiling engine (`src/shape/profile/`, its docs and tests). No gate,
tolerance, D-xx or T-xx decision changed; §11 and §2.3 untouched; `.github/workflows` untouched; the pinned Spindle
checkout only read; no force-push or rebase of pushed history.

Every fix has a regression test in `tests/profile/test_audit_profile.py` (both kernels where the kernel matters), committed
before its fix with the failing output in the commit message. Expected values that come from the pinned baseline
(pandas 3.0.6, pyarrow 25.0.1, Spindle 3.0.1 at 422e78df) were produced by running it on the same input.

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

In progress: the parity verifier, `make check` and the full suite in both kernels are running; results follow in the next commit.
