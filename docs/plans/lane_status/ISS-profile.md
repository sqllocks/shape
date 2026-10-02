# ISS-profile — profiling issues #2, #21, #22, #23, #24, #37 (lane/ISS-profile)

Status: **built; awaiting lead verification.** No gate, tolerance, D-xx or T-xx decision was changed; §11 and §2.3
untouched; the pinned Spindle checkout was only read. Issues were not commented on, labelled or closed. Branched from
`build/main-plan` (4051235); `origin/build/main-plan` had not moved when this was written (merge when it does).

Every issue was reproduced first on this branch (scripts kept in the session scratchpad, results below), fixed with
regression tests in `tests/profile/test_profile_issues.py` (64 tests, both kernels), and the tests were run against the
pre-fix tree: 54 of the 62 first-batch tests failed there (the other 8 pin behaviour that must not change).

Where the fix lives: `shape.profile` / `shape profile` is the reference profiler (`src/shape/profile/reference/`) running
on either kernel; the fused engine (`src/shape/profile/engine.py`) already counted NaN and infinity apart and gave
`variance_sample = None` for one value, so #22 was a reference-profiler bug only. No Rust changed.

## Per issue

| # | Reproduced on this branch? | Fix | Test |
|---|---|---|---|
| 2 | **Yes**, both kernels: `pattern` is `None` at 5%, 50%, 80% SSN share, `ssn` at 89%+, `None` for SSNs embedded in text | New per-column `pattern_rates` (share of non-null values that are wholly an SSN, email, IP, IBAN or the detected pattern) and `pattern_contains_rates` (share that contain an SSN, an email address or a Luhn-valid card number), measured on every distinct value weighted by count (an evenly spaced sample of the distinct values above 50,000); whole-value rates cover SSN, email, IP, IBAN and the detected pattern. `pattern` keeps its 90%/sample rule (parity). The safe profile now treats a column as pattern-only when any of SSN, email, card, IP, IBAN reaches `pii_pattern_floor` (0.001) as a whole-value or contained rate, and carries both rate dicts (aggregates, no values) | sparse 1/10/50%, embedded SSN/email/card, clean text, many distinct values, sampled estimate, safe profile pattern-only + `validate --safe` clean |
| 21 | **Yes**, when the system has no time-zone database (`PYTHONTZPATH` pointing at an empty folder, `tzdata` absent: the same condition as Windows): `ArrowInvalid: The zoneinfo module or pytz package must be installed.` for `UTC` and `America/New_York`. On this Linux box with a database it does not reproduce | The profiler no longer converts zoned values through pyarrow's Python path. UTC and fixed offsets (`+05:30`) are plain integer arithmetic, so the common case works with no database. A named zone still needs one: it now raises a `ValueError` that names the zone and says `pip install tzdata`. Output is unchanged where a database exists (parity covers the Parquet tz columns) | subprocess with no database: UTC and `+05:30` profile, named zone gives the clear error; wall clock and offset unchanged |
| 22 | **Yes**, both kernels: `null_rate 0.2`, `mean inf`, `std 'NaN'`; single value `std 'NaN'` | Float columns: `null_count` counts only nulls; `nan_count` and `inf_count` are new fields; NaN and infinity are out of cardinality, min, max, mean, std, quantiles and the fit; `is_unique` is false when any are present; `std` is `null` below two values (ints too); `json.dumps(..., allow_nan=False)` passes. Applies to every source, including CSV files (see the allow-list) | counts apart from nulls, nulls beside NaN, single value, CSV with infinity, strict JSON |
| 23 | **Yes**: one column `a;b`, exit 0, no warning; the CLI had no `--delimiter` | `shape.io.sniff_delimiter` picks comma, semicolon, tab or pipe (the one that splits the first 100 rows into the same number, at least two, of fields; quotes respected; comma wins). `shape.profile(path, delimiter=, encoding=, quotechar=, header=)` and `shape profile --delimiter --encoding --quotechar --header/--no-header` set them; the fused engine (`shape.io.CsvOptions`) sniffs too and gained `encoding`/`quotechar`. A one-column CSV whose name holds a likely delimiter warns | sniffed `;` `\|` tab `,`, quoted commas, explicit options, no-header names, warning, CLI flags, engine |
| 24 (profile side) | **Yes**: `dtype: float`, no precision or scale | Decimal columns carry `precision` and `scale` (in `to_dict()` and the `.shape`; `summary()` keeps its specified key set); `dtype` stays `float`, see Proposals. The from-ddl/generation side is lane ISS-gen's | precision/scale recorded; scale change visible |
| 37 | **Yes**: 2,000 rows of unique 20 KB text: raw `.shape` 19.9 MB (it held the first 500 documents whole, in `value_counts_ext`, plus the minimum and maximum); 5,000 x 2,000 int columns: `.shape` 104 MB, safe JSON **110 MB** | See below | see below |

### #37 in detail

The wide-table size was not the per-column value lists: at 2,000 columns the safe JSON is 110 MB because the table's
**correlation matrix grows with the square** of the numeric columns (4 million entries); the safe profile copies it. Fixed by
keeping, past 256 numeric columns, each column's 25 strongest partners (a pair stays when either column keeps it), marked
`correlation_truncated: true` in the profile and the safe profile. Measured before and after on the same inputs:

| Input | Before | After |
|---|---|---|
| 2,000 rows, 1 column of unique 20 KB text: `.shape` | 19,856,299 B | 1,814 B |
| 5,000 x 2,000 int columns: `.shape` | 104,500,739 B | 46,244,892 B |
| same, `shape profile safe` JSON | 109,961,589 B | 4,329,166 B |
| same, `--compact --exclude 'c1*'` | n/a | 746,766 B |

Also: a text column with more than 500 distinct values, at least 95% of its non-null count, lists no `value_counts_ext`;
a stored text value (count key, `enum_values`, minimum, maximum) is cut to 256 characters with an ellipsis (keys that
become equal add their shares); `shape profile safe` has `--compact` (one line, no null fields; reads back the same),
`--columns` and `--exclude` (names or `*` patterns). The 46 MB that remains for the wide table is the 1,000-value
`enum_values` + 500-value `value_counts_ext` of each integer column (about 23 KB per column), which the enum rule keeps by
design; safe JSON is about 2 KB per column.

The safe profile already carried no unique text (its cardinality backstop reduces such a column to pattern and length), so
the verbatim exposure was in the raw `.shape` only; that is closed. `shape profile validate --safe` is clean on all outputs
above.

## Parity (T-22) and the allow-list

`verify.py --impl shape`, both kernels: exit 0, 49/49 PASS. New narrow, named allow-list entries, each explained in
`benchmarks/vs_spindle/profile_1to1/README.md`:

* `NONFINITE_RULE`: `edge/x_csv_inf.csv` only. The baseline raises on it; Shape must succeed with `inf_count > 0`.
* `long_text_rule_baseline`: `value_counts_ext` and `value_counts_ext_order` for string columns with more than 500
  distinct values and at least 95% distinct (38 columns in the full run); a full run fails if the rule never fires.
* Not allow-listed because no dataset triggers them: `std = null` for a single value, 256-character cuts, the
  correlation cap (no dataset has more than 256 numeric columns). New fields are additive and not compared.

## Existing tests changed (intentional behaviour changes, no test skipped, xfailed or loosened elsewhere)

* `tests/demo/core/test_artifact.py::test_nan_and_infinity_round_trip` pinned `std == "NaN"` and `min == -inf` for a column
  with NaN and infinity (#22 removes both). It still round-trips the profile through a `.shape`, now asserting the counts
  and the finite statistics; the codec's own NaN/infinity encoding stays covered by the next test in that file.
* `tests/profile/test_enum_rule.py::test_top_values_are_kept_for_every_column`: a unique 700-value *text* column no longer
  lists its top 500 (#37); the same check on a unique numeric column keeps asserting 500.
* `tests/generation/test_fit.py::test_the_plan_covers_every_field_of_every_column` (unchanged) needed the new column fields
  in `generation/fit.py` (`COLUMN_FIELDS`, each "not modelled" with a reason) and `generation/learn.py` (they are carried
  into the loaded profile). ISS-gen may touch the `precision`/`scale` plan lines when generation honours decimals.
* `summary()` keeps its specified key set (a test pins it): the new fields are in `to_dict()` and the `.shape` only.

## Checks run in this session (final tree, after merging `origin/build/main-plan`, which only changed CI workflows)

* `ruff check` and `ruff format --check` (src, tests, plugins, benchmarks/vs_spindle): clean. `mypy` (strict): no issues in
  307 files. `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80`: clean. `lint-imports`: 1 kept, 0 broken.
  `python scripts/check_user_facing.py`: clean. `bandit -q -r src -ll`: no findings (only the existing `nosec` notes).
* Rust: no Rust file changed, so `cargo fmt/clippy/test` were not re-run.
* START: median of 10 runs of `shape --version`, 45 ms (limit 300 ms).
* Profile parity `verify.py --impl shape`: exit 0, 49/49 PASS under `SHAPE_KERNEL=rust` and under `SHAPE_KERNEL=python`.
* `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`: 4,561 passed under each kernel
  (`.[advanced]` and `-e plugins/shape-domains` installed first).
* `pytest -m heavy tests/kernel tests/profile`: 40 passed under `SHAPE_KERNEL=rust` (3 min 23 s). Under `SHAPE_KERNEL=python`
  39 of the 40 passed and the last, `test_bounded_mode_memory_does_not_grow_with_rows` (profiles a CSV of tens of millions of
  rows in bounded mode through the engine), was still running after more than 40 minutes, so it was stopped: not a pass and
  not a failure. That test goes through `shape.profile.engine`, which this lane changed only to let `CsvOptions` sniff the
  delimiter (reads the first 64 KB of the file); the Rust-kernel run covers it.
* Cost of the new per-column work (in-process, same file, with and without `pattern_rates`, best of 3, machine loaded by
  another run): d1.csv 0.09 s to 0.13 s, d2.csv 2.17 s to 2.35 s, d4.parquet unchanged within noise. The first version of
  the rates cost 3x on d1; the substring prefilters and the 50,000-distinct cap brought it here. The tracked G1 benchmarks
  (T-19, PROF-IN/PROF-CLI) were **not** re-run in this session: the lead should run them on the merged tree before G1 is
  re-judged.

## Proposals for the lead / owner (not built)

1. **#21 `tzdata` dependency.** Declaring `tzdata; sys_platform == "win32"` is a change to T-07 (core dependencies are
   exactly `numpy` and `pyarrow`), so it was not made. What was built needs no database for UTC and fixed offsets; a named
   zone on Windows without `tzdata` gets a clear error. If the owner wants named zones to work out of the box on Windows,
   amend T-07 (one line in `pyproject.toml`). The issue's "Windows CI case" and the `shape doctor` check belong to the CI
   and CLI lanes.
2. **#24 `dtype: "decimal"`.** T-22 compares `dtype` exactly and every consumer (`generation/learn.py` maps `float` to
   decimal, `quality/gates.py`, the report) reads the current vocabulary, so a decimal column is still `dtype: float` with
   `precision` and `scale` beside it. Changing the dtype is a vocabulary change for the owner; `diff` comparing `scale`,
   and a contract rule on it, are the diff and contracts lanes' (the field is now there to compare).
3. **#2 contract and diff.** `min_valid_rate` / pattern-rate contract rules and `diff` comparing `pattern_rates` are the
   contracts and diff lanes' (the rates are now in the profile). The HTML report does not show them yet.
4. **Found, not fixed:** a text column of digits long enough to overflow a float (a 20,000-digit string) makes
   `shape profile` fail with "Cannot convert non-finite values (NA or inf) to integer" (`_require_finite` on the numeric
   text probe). The baseline fails the same way, so parity pins it; fixing it means the column stays text instead.
5. `stream-profile` and the Fabric notebooks (#23's last line) were not touched: `stream-profile` goes through the engine
   (`CsvOptions` now sniffs), the notebooks belong to the demo lanes.
