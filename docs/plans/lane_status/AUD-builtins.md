# AUD-builtins: audit of the built-in generators, domains and providers

Lane `lane/AUD-builtins`, started from `int/INT-15` (f99563e). Brief: `docs/plans/AUDIT_BRIEF.md`.

Scope: `src/shape/builtins/` with the focus the area names (generators, domains, providers):
`strategies/` (and its pools), `distributions/`, `calendars/`, `_rng.py`, `catalog.py`,
`dimensional.py`, `detectors/`, `fitters/`, `chaos/` and `emitters/` were read in full and
probed. `sinks/`, `sources/`, `transforms/` and `reports/` are I/O, masking and reporting
code with their own docs (`SINKS.md`, `MASK.md`); they were not audited in depth here.

## Phase 1: findings

Reproductions use the engine on a one-table schema (`Engine(GenSchema.from_dict(doc))`); the
script is the regression test named in each fix commit.

| # | Sev | Where | Defect |
|---|---|---|---|
| 1 | high | `strategies/temporal.py:189-212` | `temporal` with `pattern: seasonal` (month, weekday or hour profiles) ignores the time of day of `start` and of an exclusive `end`: values fall before the start and after the end |
| 2 | medium | `distributions/families.py:796-813` | `truncated` fails for ordinary intervals (any table larger than about 1/(1-p)^64 rows), and its early "almost none" error depends on the chunk size |
| 3 | medium | `strategies/reference_hierarchy.py:69-88` | the hierarchy sampler cache is keyed by `id(dataset)`: after a dataset is re-registered, a reused id returns the old dataset's sampler (wrong records, or a subset of them) |
| 4 | medium | `strategies/numeric.py:139-143` | `empirical` with `interpolation: cubic` leaves the outermost anchors (doc: "values never leave the outermost anchors") |
| 5 | medium | `strategies/providers.py:328-333` | `native`/`faker` provider `digits` with `width` 16 to 18 is not uniform: the last digits come from a float with 53 bits (last digit only even for width 18) |
| 6 | medium | `strategies/temporal.py:229-238` | `hour_of_day` keys are matched as text: `"00"`, `"07"` are dropped silently and the weight goes to the other hours |
| 7 | low | `calendars/rules.py:27-37`, `:161-178` | `nth_weekday` with `n` past the month's last occurrence returns a day of the next month (5th Monday of Feb 2021 = 2021-03-01); `n` = 0 or < -1 silently means "last" |
| 8 | low | `calendars/effects.py:107-121` | `Payday(kind="monthly")` without `days` pays on the 1st and 15th; its docstring says the monthly default is the 28th |
| 9 | low | `strategies/formula.py:124-128`, `:146-169` | a formula that names a function as a value (`abs + x`) raises a bare `KeyError`; a deeply nested expression (`-` x 1500) raises `RecursionError`; neither names the column |
| 10 | low | several | spec errors that are not `StrategyError` and do not name the column: `constant` without `value`, `uniform`/`normal` without their keys (KeyError), `histogram`/`mixture` without keys (KeyError/TypeError), `lifecycle`/`weighted_enum`/`choice` with non-numeric or infinite weights (ValueError from the alias table), `digits` `width` not a number, `derived` `days` not a number, `address` unknown `field`, `pattern` width over the limit, `foreign_key` `fan_out` with list values (TypeError: unhashable) |
| 11 | low | `strategies/providers.py:357-372` | `faker` resolves any attribute of a Faker object as a provider: `seed_instance` silently yields nulls, dunder names are called |
| 12 | low | `distributions/families.py` `fit_family` | fitting a degenerate sample crashes with ZeroDivisionError/IndexError/numpy errors (gamma or beta on a constant sample, any family on an empty sample, triangular on one value) instead of `FamilyError` |
| 13 | low | `docs/GENERATION_STRATEGIES.md:174-176` | the `faker` section says every value is distinct while the table fits the pool; pool entries repeat (`color_name`: 135 distinct of 500) |
| 14 | low | `strategies/temporal.py:39-44` | a time-zone-aware `start`/`end` is converted to UTC through numpy's deprecated path (UserWarning "no explicit representation of timezones"); the conversion is not documented |
| 15 | low | `_rng.py` | dead code: `chunk_generator` is used nowhere and keys a stream by chunk, which the strategy rules forbid |
| 16 | low | `strategies/basic.py`, `__init__.py:35-40` | `sequence` overflows int64 silently (start 2**62, step 2**62 wraps to negative keys) |
| 17 | low | `calendars/effects.py:233-235` | `Trend(annual_growth=nan)` is accepted and gives NaN factors |
| 18 | low | `calendars/composite.py:95-99` | `RuleCalendar.factors(start, end)` with `end < start` raises numpy's "negative dimensions" (only `lift` checks the range) |
| 19 | low | `strategies/temporal.py:60-71` | unknown `month`/`day_of_week` profile keys (`January`) are ignored silently: the profile becomes uniform |
| 20 | medium | `strategies/_relational.py:44-58` | found while fixing 10: a `foreign_key` into a column of its own table that has no primary key recurses until `RecursionError` (building the column needs the foreign-key column first) |

Details:

1. `{"strategy": "temporal", "pattern": "seasonal", "start": "2024-01-01T12:00:00", "end":
   "2024-01-03T06:00:00", "profiles": {"month": {"Jan": 1}}}`, 1000 rows: min 2024-01-01
   00:02:51, max 2024-01-03 23:24:07. Expected: every value in [start, end). Same with only an
   `hour_of_day` profile (min 2024-01-01 10:00:09, max 2024-01-03 20:59:37). The `uniform`
   pattern is correct. `shape learn` writes exactly this form (`start`/`end` from the profile's
   min and max timestamps, seasonal with an hour profile), so learned schemas produce timestamps
   outside the observed range.
2. `{"distribution": "truncated", "base": "normal", "base_params": {"mean": 0, "std_dev": 1},
   "low": 1.3, "high": 10}` (9.7% of the mass) at 20,000 rows: `StrategyError: the truncation
   interval holds too little of the distribution`. Each row gets 64 attempts, so (1-p)^64 of the
   rows fail. With `chunk_rows=1`, 50 rows fail at attempt 16 ("almost none") because the check
   is "more than 90% of the chunk still pending". Expected: the exact truncated distribution
   (doc), the same result for any chunking.
3. Register a 1-row dataset, sample, unregister, register a 4-row dataset with the same name,
   repeat: after an `id()` is reused, the 4-row dataset draws only `S0` (reproduced at iteration
   155 of 300).
4. Quantiles 0 (p1..p90), 100 (p95), 101 (p99), cubic: values from -46.4 to 117.7.
5. 20,000 rows of `digits` width 18: last-digit counts `[3972, 15, 4009, 20, 3950, 11, 3960, 19,
   4027, 17]`; 435 distinct last-three-digit values of 1000.
6. `profiles.hour_of_day = {"00": 1, "5": 0.0001}`: every value at 05:xx.
7. `rule_from_spec({"month": 2, "weekday": "mon", "n": 5}).on(2021)` is 2021-03-01; expected no
   date that year (the rule has none) and a clear error for `n` outside 1..5 and -1.
8. `Payday(1.5, "monthly")._dates(2024-03-01, 2024-03-31)` gives the 1st and 15th.
9. `{"strategy": "formula", "expression": "abs + x"}`: `KeyError: 'abs'`.
10. Each raises the listed Python error; expected `StrategyError` naming `table.column`
    (strategy rule 4).
11. `{"strategy": "faker", "provider": "seed_instance"}` gives null rows; `__class__` builds a
    Faker. Expected: only public provider methods; anything else is "unknown faker provider".
12. `fit_family("gamma", [0.5] * 10)`: ZeroDivisionError.
16. Expected: a `StrategyError` when the last row's value does not fit int64.

## Phase 2: issues

| Finding | Issue |
|---|---|
| 1 | #129 |
| 2 | #130 |
| 3 | #131 |
| 4 | #132 |
| 5 | #133 |
| 6 | #134 |
| 7 | #135 |
| 8 | #136 |
| 9 | #137 |
| 10 | #138 |
| 11 | #140 |
| 12 | #144 |
| 13 | #146 |
| 14 | #147 |
| 15 | none (dead code: an improvement, not a defect) |
| 16 | #148 |
| 17, 18, 19 | #149 |
| 20 | #201 |

## Phase 3: fixes

Every fix has a regression test in `tests/generation/test_aud_builtins.py`, committed first
with its failing output in the commit message; then the fix. In severity order:

| Issue | Test commit | Fix commit | Fix |
|---|---|---|---|
| #129 | e44bf93 | dad22f3 | the partial first and last days of a day-weighted range weigh their share of the day and their values are mapped into the part inside the range; date ranges unchanged |
| #130 | 19af04e | 0dc301a | rows keep drawing (up to 4096 candidates, the last few rows one by one); the share check is a fixed probe of the base (at least 5%), not the share of the chunk |
| #131 | 8fac9ae | 31c4edd | the cache entry holds its dataset and is used only for the same object |
| #201 | c720b32 | 8dc973b | a whole column that needs itself is a circular-reference `StrategyError` |
| #132 | f347287 | 76b8c3b | cubic interpolation clipped to the outermost anchors |
| #133 | 97c4871 | a84823e | widths over 15 take their last nine digits from a second stream; widths up to 15 unchanged |
| #134 | 797265b | 8373ca0 | hour keys parsed as numbers |
| #135 | edde0dc | bcbd2b7 | `NthWeekday` has no date when the month lacks the weekday; `n` must be 1..5 or -1 |
| #136 | fcbff45 | f0bacb9 | monthly default `days` is (28,) |
| #137 | 40dabf8 | 25b88dc | function name as value and too-deep nesting are `StrategyError`s (no depth cap: expressions that worked still work) |
| #138 | 776f852 | 6f5dcaf | `StrategyError` naming the column for every spec in the issue (the test's fan_out case moved to a parent table in the fix commit, see #201) |
| #140 | d55ab78 | d9e043d | only methods of the locale's provider classes; never the Faker API or private names |
| #144 | 761e140 | cb0a855 | `fit_family` checks the sample and wraps arithmetic failures in `FamilyError` |
| #146 | 96486ad | da5d10a | docs and docstring corrected |
| #147 | be082ba | d30bcb2 | offsets converted to UTC explicitly, documented |
| #148 | 54c84b1 | 4736dbc | values past int64 are a `StrategyError` |
| #149 | 4afb53d | 93e1d3d | NaN growth, reversed `RuleCalendar.factors` range and unknown seasonal keys are errors |
| (15) | - | 6e0373a | dead `shape.builtins._rng` removed (test: the import raises `ModuleNotFoundError`) |

### Outputs the equivalence verifiers compare

No fix changes bytes those verifiers compare: every `strategy_1to1` and domain temporal case
uses date-only bounds (#129 leaves those untouched), no verifier case uses `truncated`, cubic
`empirical`, `digits` wider than 15, a monthly payday or a fifth weekday, and the hour-key
change gives the same weights for `"0"`..`"23"`. The in-repo strategy equivalence tests
(`tests/generation/test_strategies_p404{a,b,c,d}.py`, which apply T-21 (b)-(e) to the committed
baseline fixtures) pass. The verifiers that run the pinned baseline (`$SPINDLE_ROOT`) were not
run: there is no baseline checkout in this session.

## Left open

- `FastAddressPack.generate_columns` (`strategies/address.py:136-140`) indexes `searchsorted` of
  the cumulative scope weights without a bound: a draw above a cumulative sum that rounds below
  1 (probability about 1e-16 per row) raises `IndexError`. Not filed: practically unreachable;
  the engine's `address` strategy (`address_rows.py`) already bounds it.
- `sinks/`, `sources/`, `transforms/`, `reports/` were not audited in depth (see Scope).

## Commands and results

All in this session, on `f09bde7` (which already contains `origin/build/main-plan` at
`5c91ea5`: nothing to merge). Python 3.11.15, cargo 1.97.0, 4 cores.

Environment: `pip install -e ".[dev]"` and every first-party plugin (`plugins/shape-*`, including
`shape-fabric`) into `$SHAPE_VENV`; for the full suites also `tests/demo/fabric/requirements.txt`,
`scikit-learn`, `adlfs` (so no test skips for a missing module) and the system `unixodbc` package
(`test_udf.py` needs `libodbc.so.2`).

- `make check PYTHON=python`: **exit 0**. ruff check: all passed; ruff format: 1087 files already
  formatted; mypy: no issues in 435 files; compileall, vulture: clean; lint-imports: 1 kept;
  check_requirements (89), check_secrets, check_user_facing, check_shipped_data, plugin skeletons
  (7), conformance coverage (32/32): OK; coverage run: 6763 passed, 17 skipped, coverage 92.20%
  (gate 86%); heavy: 42 passed; `SHAPE_KERNEL=python pytest tests/kernel`: 265 passed; cargo fmt,
  clippy `-D warnings`, cargo test (34 passed): OK. The 17 skips were missing optional modules
  (`sklearn`, `adlfs`) at that point; both full runs below ran with them installed.
- The first `make check` attempt failed 2 tests: `test_every_skeleton_builds_a_pure_wheel` (my
  non-editable plugin install had left `plugins/*/build`; removed, plugins reinstalled with `-e`
  as `make bootstrap` does: passes) and `test_to_postgresql_routes_to_the_database_sink` (see
  below). The rerun passed every step.
- `python scripts/check_user_facing.py`: clean.
- `SHAPE_KERNEL=rust pytest -m "not emulator and not live"`: 7102 passed, 5 failed, 0 skipped
  (35 min).
- `SHAPE_KERNEL=python pytest -m "not emulator and not live"`: 7103 passed, 4 failed, 0 skipped
  (1 h 52 min).

No test was deselected beyond the markers, skipped or xfailed. Every failure also fails on
`origin/build/main-plan` (`5c91ea5`, a worktree run with the same venv, the same Rust kernel
build (no `rust/` diff) and `PYTHONPATH` on its `src` and plugin `src` trees), or passes on this
branch when run alone:

| Test | Kernels | Cause | Evidence |
|---|---|---|---|
| `iss_gaps/test_landing_and_batches.py::test_file_sinks_take_path_template_and_batch_date` | both | `fabric-user-data-functions` (from `tests/demo/fabric/requirements.txt`) pins `pyarrow<20` and downgraded pyarrow to 19.0.1; that reader adds the hive `ingest_date` column | fails on base with 19.0.1; passes on both kernels here with pyarrow 25.0.1 (what `[dev]` installs) |
| `kernel/test_hashing.py::test_rust_equals_reference_on_a_million_values[float16]` | both | same pyarrow 19.0.1: `if_else` has no halffloat kernel | as above |
| `kernel/test_hashing.py::test_one_and_one_point_zero_hash_equal` | both | same pyarrow 19.0.1: `Expected np.float16 instance` | as above |
| `security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references` | both | order-dependent: it checks the whole `sys.modules`, and `tests/demo/fabric/test_udf.py` (which `make check` ignores) imports `azure.functions` earlier in the same process | `pytest tests/demo/fabric/test_udf.py <this test>` fails on base; passes alone here |
| `profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows` | rust run only | RSS measurement under load: my base-branch stress loops ran at the same time (load average above 4) | passes alone here and on base; passed in the python-kernel full run |

`cli/test_generate_to.py::test_to_postgresql_routes_to_the_database_sink` (first `make check`
attempt only) is a race in the `shape_databases.testing.FakeServer` test fake, which this lane does
not touch (no `plugins/` diff): the engine writes tables from several threads to one server and
`begin()` deep-copies the shared `tables` dict while another connection adds to it (`RuntimeError:
dictionary changed size during iteration` in `testing.py:139`). A loop of the test body in one
process (300 runs): 5 failures on this branch, 3 on `origin/build/main-plan`. Not fixed here
(outside the audited area); the fake needs a lock or per-connection snapshots.

Not run: the equivalence verifiers against the pinned baseline (`benchmarks/vs_spindle`), as
recorded above (no baseline checkout in this session).
