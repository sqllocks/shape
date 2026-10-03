# ISS-diff: diff and drift (issues #3, #4, #5, #14, #20, #34 diff side, #35)

Lane branch `lane/ISS-diff` (from `build/main-plan` 4051235). Builder notes; the lead merges. No
issue was commented on, labelled or closed. Docs: `docs/DRIFT.md`; changelog entry under
Unreleased.

## What was built

- **`shape.drift.engine`** (new): the one drift engine. Reads a *view* of each column from a
  reference `Profile`, a stream window (`WindowProfile` or its profile document), a profile-engine
  document, a Shape v2 model or a v1 capture; returns change records
  `{column, kind, baseline, current, severity, score}`; thresholds, per-column thresholds, ignore
  and only lists, policy files.
- **`shape.diff`** (`contracts/v1.py`) is now a thin front end (`diff_tables`); **`shape.drift.compare`**,
  **`ShapeMonitor`** (and `ShapeTimeline.changes`, which calls `compare`) are front ends for the same
  engine. `DEFAULT_THRESHOLDS` lives in the engine; `contracts.v1.DEFAULT_THRESHOLDS` is the same object.
- **`shape.generation.drift_plan`** + **`shape generate-drift`** (new): planted drift over time.
- Edited paths: `src/shape/drift/{engine,core}.py`, `src/shape/contracts/v1.py`,
  `src/shape/streaming/monitor.py`, `src/shape/generation/drift_plan.py`,
  `src/shape/cli/{main,drift_plan}.py` (`main.py`: the `diff` flags and two registration blocks),
  tests, `docs/DRIFT.md`, `demo/DRIFT.md`, `CHANGELOG.md`. No Rust, no profile code, no streaming
  runtime change (the lane ISS-stream owns the duration units).

## Per issue: reproduction on this branch (before), fix, regression tests

Every reproduction ran on `lane/ISS-diff` at 4051235 (reference profiler path, `pure` Python
profile; the issues were filed against `main`). The new tests are in
`tests/diff/test_drift_engine.py` (52 tests) and `tests/generation/test_drift_plan.py` (29 tests);
before the fix (`src/` of 4051235, new tests kept) the first file ran **38 failed, 14 passed** (the 14
are the no-drift controls); after, 52 passed.

### #3: diff does not compare pattern, proportions, spread, quantiles, min/max, length

Reproduces. Before: emails to SSNs `False []`; status 80/15/5 to 40/40/20 `False []`; spread
normal(100,10) to normal(100,40) `True [distribution_change]` (low, a label).
Fixed with documented defaults and severities (`docs/DRIFT.md`): `pattern_change`, `category_shift`
(TVD), `spread_change`, `distribution_shift` (KS from the quantiles), `range_change`,
`length_change`, `outlier_rate_change`. After: emails to SSNs `pattern_change email -> ssn` (+
`length_change`); the mix `category_shift`; the spread `spread_change` + `distribution_shift`.
Tests: `test_pattern_change_emails_to_ssns`, `test_category_proportions_shift_without_a_new_value`,
`test_spread_change_with_the_same_mean`, `test_quantiles_move_with_the_same_mean_and_spread`,
`test_range_change_when_the_tail_extends`, `test_a_single_stray_value_is_not_a_range_change`,
`test_string_length_change`, `test_outlier_rate_change`.
The new comparisons are guarded against false drift: `test_two_samples_of_one_distribution_do_not_drift`
(12 seeds x a categorical, a 40-value category, a normal, a log-normal, a flag and a 0/1 column; the
only change two samples may show is the existing low `distribution_change` label) and the noise floors
in `docs/DRIFT.md`.

### #4: false drift on keys, unique columns of different sizes, date strings

All three reproduce. Before: A `cardinality_change (2600 -> 4000)`; B `mean_shift (2000.5 -> 6000.5)`;
C `new_categorical_values (['2026-08-30'] -> ['2026-08-31'])`. After: all three `[]`. Rules: unique-like
columns (95% or more distinct per row) compare distinct values per row, not counts; a primary key or
a dense unique integer has no mean/spread/distribution comparison; date strings (profiled as
`datetime`, or with the `date` pattern) are not categories. A key that stops being unique is
reported (`uniqueness_change`). Tests: `test_unique_ids_with_a_different_row_count_are_not_a_cardinality_change`,
`test_a_key_that_stops_being_unique_is_reported`, `test_a_sequential_key_has_no_mean_shift`,
`test_a_constant_date_string_column_is_not_an_enum`, `test_a_non_key_measure_still_shifts_when_the_ids_do_not`,
`test_a_much_smaller_window_of_the_same_values_is_not_a_cardinality_change`.

### #5: ignore list, per-column thresholds, CLI flags

Reproduces: `thresholds={"ignore_columns": ...}` raises `ValueError: unknown thresholds`; `shape diff`
has only `--json`, `--fail-on-drift`, `--verify`. Fixed: `ignore_columns`, `column_thresholds`
(`{"order_total": {...}, "*": {...}}`, globs, `table.column`), `only_columns`, `policy` (dict or JSON
file; a contract's `"drift"` object works and `shape check` ignores it); CLI `--ignore`, `--only`,
`--policy`, `--threshold KEY=VALUE`, `--column-threshold COLUMN:KEY=VALUE` and a flag per global
threshold (`--null-rate`, `--cardinality-ratio-max`, `--cardinality-ratio-min`, `--mean-shift-std`,
`--min-severity`). Bad input exits 2. Tests: `test_ignore_columns`,
`test_ignore_and_only_with_table_dot_column`, `test_per_column_thresholds_and_the_star_default`,
`test_bad_policy_input_is_an_error`, `test_policy_dict_file_and_contract_drift_object`,
`test_cli_threshold_ignore_and_policy_flags`.

### #20: booleans

Reproduces as a missed drift: 50% to 95% true gave `False []`. On this branch the profile *does*
hold the proportions (`is_enum` true, `enum_values {'True': 0.5, 'False': 0.5}`), unlike the issue's
older `main` (`mean None, is_enum None`), so no profile field was added (the T-22 parity run and
the profile code are untouched); the engine reads the true rate from them. After: `true_rate_change
0.5 -> 0.95` (medium; default 0.10 absolute, never below 4 standard errors). 0/1 integer and float
flags (a `bernoulli` column) are treated the same. Contract rules `min_true_rate` / `max_true_rate`
added to the v1 contract (additive; see "Proposals" 4). Generation already accepts a rate
(`bernoulli`), and the drift plan moves it (`test_a_boolean_rate_can_drift_too`). Tests:
`test_a_flag_going_from_50_to_95_percent_true_is_drift`, `test_a_zero_one_integer_flag_is_compared_by_true_rate`,
`test_a_flag_that_barely_moves_is_not_drift`, `test_contract_rules_on_the_true_rate`.

### #34 (diff side): window profiles and `shape.diff`

Reproduces: `shape.diff(closed[0].profile, closed[1].profile)` raises `AttributeError: 'dict' object has
no attribute 'is_dataset'`. (The integer-duration part reproduces too: `TumblingProfiler(schema,
60_000)` over 6 minutes gave 359 windows against 5 with `timedelta(seconds=60)`; that is lane
ISS-stream's, its commit 4d36f4f refuses bare integers. I did not touch `streaming/runtime.py`, so
the lanes merge without overlap; `tests/diff/test_drift_engine.py` uses `timedelta`, which both
versions accept.) Fixed: `shape.diff` (and `compare`, `ShapeMonitor`) accept a `WindowProfile`, its
`.profile` document or a profile-engine document in place of a `Profile`, against a window or a
stored `.shape` baseline: the two profilers' dtype naming for whole-valued floats and date strings is
reconciled, the window's `_shape_event_time` column is not "added", and the engine's documents carry
no fitted family so `distribution_change` is skipped. Tests: `test_a_window_profile_can_be_passed_to_diff`,
`test_a_window_with_a_flipped_flag_drifts_against_the_baseline`.
Not built (see Proposals 5): `WindowProfile.to_profile()` and the stream CLI's `--baseline FILE.shape
--fail-on-drift`.

### #35: two drift engines

Reproduces (and worse than filed: with the capture-based monitor even the control reported drift).
2,000 reference rows (status 80/15/5, `amount` ~ N(80,20), a flag at 50%), 2,000 drifted rows,
`ShapeMonitor(every=500)` at the last check, against `shape.diff` of the same samples:

| change | monitor before | `shape.diff` before | monitor after | `shape.diff` after |
|---|---|---|---|---|
| control | max 0.55, 5 drifts | none | max 0, none | none |
| mix 80/15/5 to 40/40/20 | 0.37 | `distribution_change` only | `category_shift` 0.52 | `category_shift` |
| `amount` mean 80 to 120 | 0.75 | `mean_shift` | `mean_shift` 0.74 | `mean_shift` |
| `amount` spread 20 to 60 | 1.00 | `distribution_change` only | `spread_change` 0.89 | `spread_change` |
| flag 50% to 95% | 0.11 | none | `true_rate_change` 0.45 | `true_rate_change` |

One engine (`shape.drift.engine`), one set of defaults, one record shape with a `score` from 0 to 1
(per-kind meaning in `docs/DRIFT.md`). `MonitorEvent.drifts` are `Drift` objects that keep `path`,
`score`, `before`, `after`, `reason` and gain `column`, `kind`, `severity` (`event.changes` gives
`diff`'s dicts); `max_drift` is 0 until a change passes its threshold. The monitor compares a
profile reference with a profile of its buffer, and a capture or model with a capture of it.
Two existing tests pinned the old engine and were rewritten, not skipped:
`tests/drift/test_drift.py::test_drift_reads_v2_models_with_the_v2_metric_names` (the old rule flagged
any change of any metric; the fixture now has a realistic model and expects the thresholded kinds)
and `tests/demo/core/test_diff.py::test_min_severity_threshold_filters` (its fixture put a third of
the rows on a new value, now also a medium `category_shift`; it uses one new value in 101 rows).
`tests/demo/content/test_demo_data.py` lists `category_shift` and `range_change` as known side effects
of the planted +40% price step (`demo/DRIFT.md` updated). Tests: `test_monitor_and_diff_agree`
(5 changes x profile and capture references), `test_monitor_thresholds_and_ignore_are_the_diff_ones`,
`test_timeline_changes_use_the_engine`, `test_compare_gate_and_diff_share_the_defaults`.
`shape drift` (the PSI/KS tier on two data sets, `cli/tiers.py`) and `diff_models` (a typed field
delta) are different tools and were not merged into this engine.

### #14: engine-native drift over time

What generation already had: `ShapeTimeline` (interpolates evidence specs; not the engine),
`shape time-travel` / `shape continue` (P6-05: evolve *rows* with growth, churn, updates and
seasonality; the column distributions do not change), chaos (P6-02: corrupts data). None plants a
change in what columns look like, and none writes an answer key. Built in the natural place
(`shape.generation`): `DriftPlan` (`drift_plan.py`) applies step, ramp and window events to a
`GenSchema`, one day at a time (null rate, category weights, new category, distribution parameters or
a `scale`, added and dropped columns, type changes); each day is generated by the ordinary `Engine`
from a copy of the schema; `schema_at` is the diffable per-day spec, `ground_truth()` the answer key,
`expected_changes(day_a, day_b)` what `shape.diff` should report; `write()` / `shape generate-drift`
write `<date>/<table>.<fmt>`, `_specs/<date>.json` and `ground_truth.json`. Tests
(`tests/generation/test_drift_plan.py`) include the five events of the issue planted and recovered by
the real diff (`test_every_planted_event_is_found_by_the_diff`,
`test_expected_changes_match_what_the_diff_reports`), ramp/window shape, exact revert, a new category
that is absent from the schema until its day, scale of a log-normal (`mean += ln 1.4`), reproducible
days, the answer key, the CLI end to end, and every error path.

## Checks run in this session

On the final commit (see the end of this file for the suite results):
`ruff check` and `ruff format --check` (`src tests plugins benchmarks/vs_spindle`), `mypy` (strict, 310
files), `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80`, `lint-imports` (1 kept),
`python scripts/check_user_facing.py` (clean), `bandit -q -r src -ll` (no findings), START (`shape
--version`, median of 10): 45 ms (limit 300 ms). No Rust and no profile code changed, so cargo and
`profile_1to1/verify.py` were not re-run.

## Proposals and findings for the owner (nothing here changes a D-xx/T-xx decision or the §12 table)

1. **The §12.3 defaults are kept** (added/removed column, dtype change, null rate 0.05, cardinality
   ratio 1.5 / 0.67, mean shift 0.5 std, family change, new values) with the `thresholds` override.
   Two behaviours around them changed, both false-drift fixes: cardinality is not compared for a
   unique-like column (its count is the row count) and not at all when the samples differ by more than
   1.5x and either is mostly distinct; mean shift is not compared for keys.
2. **`distribution_change` (any change of the fitted family's name, low) is noisy**: on 40 pairs of
   samples of one normal distribution it fired 23 times (normal against log-normal). It is the §12.3
   default so it is unchanged, and it is the one change the no-drift test allows. Proposal: report it
   only when the fit scores differ clearly, or drop it to informational, since `distribution_shift`
   carries the size of a real change.
3. **No row-count or volume comparison.** A day that halves in size is not a change in any kind. It
   needs a documented default (a daily feed's volume varies), so it is not added. Proposal: a
   `row_count_change` kind with a ratio threshold, off for datasets of different sizes.
4. **Additive extensions of the §12 formats** (nothing existing changes): change records gain
   `score`; the contract format accepts `min_true_rate` / `max_true_rate` and a `drift` object
   (ignored by `check`); `shape.diff` gains keyword arguments and `shape diff` gains flags. If the
   owner prefers the §12.2/§12.3 text to stay literal, these are small to remove.
5. **Not built:** `WindowProfile.to_profile()` (the engine documents have no fitted distribution, no
   pattern field and no outlier statistics, so a faithful `Profile` cannot be made from them;
   `shape.diff` takes windows directly instead) and the stream CLI's `--baseline FILE.shape
   --fail-on-drift` (the stream CLI belongs to lane ISS-stream; the call is
   `shape.diff(baseline, window)` per window).
6. **`range_change` and heavy tails.** The extremes of a heavy-tailed column vary a lot between
   samples of one distribution; `range_margin_std` defaults to 2.0 of the baseline's spread (or tail
   reach) and the 1%/99% quantile must move too. One of 40 log-normal(sigma 0.8) pairs still fired in
   a trial; it is low severity.
7. The KS distance is read from the profile's quantiles (11 levels plus min and max), so it
   underestimates for very sharp shapes (two tight humps against a normal: 0.14 where the exact
   distance is about 0.3).
8. Merge note for the lead: `lane/ISS-cli` also edits `cli/main.py` (parser and dispatch regions,
   not `_cmd_diff`); a textual conflict is possible near the `diff` parser, where this lane adds
   `_diff_policy_arguments(d)` and, above `_cmd_diff`, `_diff_options`.

## Suite results (final commit)

`pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric` (the project and
`plugins/shape-domains` installed editable with `.[advanced]`): `SHAPE_KERNEL=rust` 4584 passed, 46
deselected (the run started before the last added test); `SHAPE_KERNEL=python` 4585 passed, 46
deselected. `tests/demo/fabric` needs `nbformat` and unixODBC and was not run.
