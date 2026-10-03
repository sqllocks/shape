# Drift: `shape diff`, `ShapeMonitor` and planted drift

One engine answers "did the data change?": `shape.drift.engine`. `shape.diff` (two profiles),
`shape.drift.compare` (Shape models and captures), `ShapeMonitor` (rows arriving in a stream),
`ShapeTimeline.changes` and the stream profiler's windows all use its rules and thresholds, so the
same change is the same answer everywhere.

```python
import shape

d = shape.diff(shape.load("baseline.shape"), shape.profile("today.parquet"))
d.drifted            # bool
d.changes            # [{column, kind, baseline, current, severity, score, class, class_reason}, ...]
d.semver             # {"bump": "major", "breaking": 1, "additive": 0, "cosmetic": 3}
```

```
shape diff baseline.shape today.shape --fail-on-drift --ignore order_id --null-rate 0.02
```

A change is reported only when it passes its threshold, so a stable column produces no record.
`severity` is fixed per kind. `score` is the size of the change from 0 to 1: a structural change
(column added, dtype changed) scores 1, the others their natural distance (see the table).

## What is compared

| Kind | Reported when (default) | Severity | Score |
|---|---|---|---|
| `table_added`, `table_removed` | any | high | 1 |
| `column_added`, `column_removed` | any | high | 1 |
| `dtype_change` | any | high | 1 |
| `row_count_change` | the table's row count over baseline's > `row_count_ratio_max` = 2.0 or < `row_count_ratio_min` = 0.5 (not for a stream window) | medium | 1 - min(r, 1/r); 1 from an empty table |
| `null_rate_change` | null rate moved by more than `null_rate` = 0.05 (absolute) | medium | the change |
| `cardinality_change` | distinct values over baseline's > `cardinality_ratio_max` = 1.5 or < `cardinality_ratio_min` = 0.67 | medium | 1 - min(r, 1/r) |
| `mean_shift` | mean moved by more than `mean_shift_std` = 0.5 baseline standard deviations | medium | z / (1 + z) |
| `distribution_change` | the fitted family's name differs, and the two samples differ by more than two samples of their sizes do by chance (KS critical value, alpha = 0.001) | low | 0.2 |
| `new_categorical_values` | a category the baseline did not have | low | share of rows with it |
| `category_shift` | total variation distance of the category proportions > `category_tvd` = 0.10 | medium | the distance |
| `true_rate_change` | share of true values (booleans, 0/1 columns) moved by more than `true_rate` = 0.10 | medium | the change |
| `pattern_change` | the detected pattern differs (`email`, `ssn`, `uuid`, ...; none counts) | medium | 1 |
| `spread_change` | standard deviation ratio > `std_ratio_max` = 1.5 or < `std_ratio_min` = 0.67 | medium | 1 - min(r, 1/r) |
| `distribution_shift` | KS distance, read from the quantiles, > `ks_distance` = 0.10 | medium | the distance |
| `range_change` | the min or max moved past the baseline's by more than `range_margin_std` = 2 times the baseline's spread, and the 1% / 99% quantile moved with it | low | e / (1 + e) |
| `length_change` | mean string length moved by more than `length_ratio` = 25% | low | the relative change |
| `outlier_rate_change` | outlier rate moved by more than `outlier_rate` = 0.02 | low | the change |
| `uniqueness_change` | distinct values per row moved by more than `uniqueness_rate` = 0.05 (unique-like columns) | medium | the change |
| `dependency_broken` | an approximate functional dependency of the baseline (`zip -> city`) lost more than `dependency_confidence` = 0.02 of its confidence (never below the sampling noise); a baseline determinant that was unique counts as confidence 1 (`docs/JOINT.md`) | high | the drop |
| `placeholder_surge` | the share of rows holding a placeholder value (`00000`, `-1`, `N/A`, ...) rose by more than `placeholder_share` = 0.01 | medium | the rise |
| `implausible_rate_change` | the share of implausible rows rose by more than `implausible_rate` = 0.02 | medium | the rise |
| `association_shift` | an association measure (Cramer's V, Theil's U, correlation ratio, Pearson, Spearman) moved by more than `association_shift` = 0.2 | low | the change |
| `reference_match_change` | the share of rows whose columns are a real combination of a reference fell by more than `reference_match_rate` = 0.02 | high | the drop |
| `hour_of_day_change`, `day_of_week_change` | total variation distance of the mix > `temporal_tvd` = 0.20 (day of week: both columns span 14 days or more) | low | the distance |

The first five thresholds (`null_rate`, `cardinality_ratio_max`, `cardinality_ratio_min`,
`mean_shift_std`, `min_severity`) and the first seven kinds are the contract of `docs/plans`
section 12.3 and keep their values.

### Rules that keep a stable feed quiet

- **Sampling noise.** `category_tvd`, `true_rate`, `ks_distance`, `outlier_rate` and `temporal_tvd`
  never go below the noise two samples of that size show by themselves (about twice the expected
  distance, or four standard errors; the KS distance uses the critical value at alpha = 0.001). Two
  samples of one distribution do not drift, however tight the setting.
- **Small samples.** With fewer than `min_rows` = 30 non-null values a column gets no distribution
  comparison (category mix, spread, KS, range, outliers, true rate, hour of day).
- **Keys.** A primary key, or a dense unique integer column (a counter), has no `mean_shift`,
  `spread_change`, `distribution_shift`, `range_change`, `outlier_rate_change` or
  `distribution_change`: its values say how many rows were loaded, not how the data behaves.
- **Unique columns.** A column with as many distinct values as rows (95% or more) is compared by
  distinct values per row, not by count, so a Sunday file with 2,645 ids and a Tuesday file with
  3,997 are the same. When one side is unique-like and the other is not, a fall in uniqueness is
  reported unless the current sample is more than twice as large, and a rise unless it is less than
  half the size (a small sample is naturally more unique). When the two samples differ by more than
  1.5 times and either has more than half its rows distinct, the distinct count mostly tracks the
  sample size and `cardinality_change` is skipped.
- **Dates.** Date strings are temporal values, not categories: a column that holds one date per
  file does not report `new_categorical_values` every day.
- **Flags.** A boolean or 0/1 column is compared by its true rate (`true_rate_change`) instead of
  its mean, spread or category mix.
- **Windows.** A stream profiler's window and a stored `.shape` baseline are different profilers; a
  numeric column typed `integer` by one and `float` by the other is not a `dtype_change`, and the
  window's event-time column (`_shape_event_time`) is not a column the baseline lacks.
- The extremes of a heavy-tailed column vary a lot between two samples of one distribution, so such
  a column can show a `range_change` now and then. It is low severity: raise `range_margin_std`, or
  ignore the column.
- **Row counts.** `row_count_change` compares the exact row counts of a table, so there is no
  sampling noise to allow for; the defaults (more than double, fewer than half) let a 1.5 times
  larger extract of the same data pass. A stream window is not compared by size (its size is its
  width), and `ShapeMonitor` drops the kind (its buffer is a window of the stream). Set
  `row_count_ratio_max` to a large number to turn the kind off.
- **The fitted family's name** (`distribution_change`, low) flips between samples of one
  distribution (a normal column fit as log-normal). The name is reported only when the data also
  moved: the KS distance between the samples, read from the quantiles, must exceed the critical
  value for their sizes (alpha = 0.001, no 0.10 floor, so the bigger the samples, the smaller the
  move that counts). Without quantiles to compare, only samples of `min_rows` or more name a
  family. The size of a real distribution change is `distribution_shift`.

## Change classes

`severity` says how large a change is. The **class** says what it does to the people who read the
data: **breaking** (readers that worked yesterday can fail today), **additive** (something new,
nobody who read the old data is affected) or **cosmetic** (the schema and the constraints are
unchanged, values moved). Every change carries `class` and a one-line `class_reason`, and the
result summarises them as a version bump:

| Class | Kinds |
|---|---|
| breaking | `table_removed`, `column_removed`, `dtype_change`, `pattern_change`, `dependency_broken`, `reference_match_change`; `null_rate_change` when the baseline null rate is 0 and the current one is above 0; `uniqueness_change` when the baseline column was a primary key or had as many distinct values as non-null rows and the current one does not |
| additive | `table_added`, `column_added`, `new_categorical_values` |
| cosmetic | every other kind: `row_count_change`, `cardinality_change`, `mean_shift`, `spread_change`, `distribution_shift`, `distribution_change`, `category_shift`, `true_rate_change`, `range_change`, `length_change`, `outlier_rate_change`, `hour_of_day_change`, `day_of_week_change`, `placeholder_surge`, `implausible_rate_change`, `association_shift`; `null_rate_change` and `uniqueness_change` otherwise |

The table lives in `shape.drift.semver` (`DEFAULT_CLASSES`) and a test fails when a kind of the
table above has no default class, so a new kind cannot ship without one.
`shape.drift.semver.classify(change, classes=None)` returns the `Classification` (`class_` and
`reason`) of one change record.

**Widening.** A `dtype_change` whose current Arrow type can hold every value of the baseline type
carries `detail.widening: true`: a signed integer to a wider signed one, an unsigned integer to a
wider integer, `float32` to `float64`, an integer of 32 bits or fewer to `float64`, `string` to
`large_string`, `binary` to `large_binary`, a decimal with more precision and the same scale.
Widening is still **breaking** by default (readers see another type), so nothing that fails a gate
today passes; set the pseudo-kind `dtype_widening` to `additive` to class widening that way.
`int64` to `int32`, a different scale and every other type change are never widening. A profile
records a type family (`integer`, `float`, `string`), not a width, so the flag appears where the
Arrow types are known: the schema drift gate of `shape verify` (`int32` to `int64`) and any change
record that names Arrow types.

**Overrides.** The drift policy (`policy=` or `--policy POLICY.json`, a contract's `"drift"`
object, a source's `classes` in `shape.yml`) takes two more keys:

```json
{
  "classes": {"column_added": "cosmetic", "dtype_widening": "additive"},
  "column_classes": {"amount_*": {"mean_shift": "breaking"}, "orders.status": {"new_categorical_values": "breaking"}}
}
```

`classes` is `{kind: class}`; `column_classes` is `{pattern: {kind: class}}` with patterns as for
`column_thresholds`, the most specific winning (`*`, a glob, the column name, `table.column`). A
policy class replaces the default, in both branches of `null_rate_change` and `uniqueness_change`.
A planned-change entry (`docs/PLANNED_CHANGES.md`) may carry a `class`, which wins over the policy
for the changes it matches. An unknown kind or an unknown class (a severity such as `high` is not
one) raises `ValueError`, and exits 2 on the command line.

**The bump.** `semver` counts the *unplanned* changes: `major` when any is breaking, else `minor`
when any is additive, else `patch` when any is cosmetic, else `none`. Planned changes
(`docs/PLANNED_CHANGES.md`) are counted apart, under `semver.planned`, and never change the bump;
a `severity` entry that still counts is counted at its class. `--version-from X.Y.Z` (or
`semver.next_version(version, bump)`) adds `semver.next_version`: `2.0.0`, `1.5.0`, `1.4.3` and
`1.4.2` for the four bumps of `1.4.2`.

```
$ shape diff base.shape today.shape --fail-on breaking --version-from 1.4.2
shape: status: column_removed [breaking]
shape: tier: column_added [additive]
shape: amount: mean_shift [cosmetic]
version: 2.0.0
bump: major (1 breaking, 1 additive, 1 cosmetic)
```

**Failing on a class.** `shape diff --fail-on breaking|additive|cosmetic` (and
`shape.diff(..., fail_on=...)`, which sets `DiffResult.failed`) fails when an unplanned change of
that class or a stricter one (breaking, then additive, then cosmetic) is reported. `--fail-on-drift`
and `min_severity` keep their meaning, and both flags may be given: the run fails when either
fails. Planned changes with action `expect` never count. A removed column gives `bump: major` and
exit 1 with `--fail-on breaking`; an added column alone gives `minor`, exit 0 with
`--fail-on breaking` and exit 1 with `--fail-on additive`; a mean shift alone gives `patch`.
The schema drift gate of `shape verify` uses the same classes and takes its `fail_on` from the
verify configuration or from `shape.yml` (`docs/PROJECT.md`, `docs/VERIFY.md`); its default,
`breaking`, gives the results it always gave.

## Planned changes

A change you expect (a release adds a column, a migration changes a type) is listed in a
planned-change file with a window and a reason; inside the window `shape diff --fail-on-drift` and
the gates report it as planned instead of failing. See `docs/PLANNED_CHANGES.md`.

## The sweep: planted drift as a regression test

`tests/diff/test_drift_sweep.py` generates pairs of datasets with Shape's own generators, records
every planted change in an answer key (column, kind, size), runs `shape diff` at the default
thresholds and asserts two things:

- **Zero false negatives.** Every planted change is reported as one of the kinds that find it.
  The planted changes are the `DriftPlan` events (null rate, category weights, a new category, a
  scale of a log-normal, normal and uniform column, a boolean rate, a column added, dropped and
  retyped), the generation engine's row counts (2.5 times and 0.4 times the baseline) and the
  chaos generator's corruptions (case and whitespace, null creep, type change, negative amounts,
  duplicates, PII fill).
- **A bounded false-positive rate.** On pairs of one distribution (a fresh sample of the same
  size, and a fresh sample 1.5 times as large) at most **5%** of the pairs report any change, and
  at most **0.5%** of the (pair, column) comparisons do. `distribution_change` and
  `row_count_change` never fire on them.

`SHAPE_DRIFT_SWEEP=fast` (default; CI runs it in both kernel modes) uses 2,000 rows, 5 trials of
each planted case and 50 same-distribution pairs. `SHAPE_DRIFT_SWEEP=full` (the nightly job) uses
20,000 rows, 40 trials and 400 pairs. `python tests/diff/test_drift_sweep.py` prints the report.
Measured on the full size: 0 of 760 planted changes missed; 2 of 400 same-distribution pairs
reported a change (both a `range_change` of the heavy-tailed log-normal column, which the notes
above describe), against 203 of 400 (a `distribution_change` of a normal or uniform column) before
`distribution_change` respected sample size.

## Tuning

Thresholds, per-column thresholds, an ignore list and an `only` list work the same in Python and on
the command line.

```python
shape.diff(
    base, today,
    thresholds={"null_rate": 0.02},                       # every column
    column_thresholds={"order_total": {"mean_shift_std": 0.25}, "amount_*": {...}, "*": {...}},
    ignore_columns=["order_id", "customer.updated_at", "tmp_*"],
    only_columns=["order_total", "status"],               # leave out everything else
    policy="drift_policy.json",                           # or a dict: all four in one file
)
```

- A pattern is a column name, `table.column` or a glob (`*`, `?`). For a dataset the changes are
  named `table.column`; a bare name matches that column in every table.
- Per-column thresholds apply least specific first: `*`, a glob, the column name, `table.column`.
  `min_severity` can be set per column as well.
- An unknown threshold, a value that is not a number of 0 or more (`min_severity` is `low`,
  `medium` or `high`) or an unknown policy key raises `ValueError`.
- A policy file is `{"thresholds": {...}, "columns": {...}, "ignore": [...], "only": [...],
  "classes": {...}, "column_classes": {...}}` (the last two: "Change classes"). A
  contract may carry the same object as `"drift"`, so a team keeps one policy file:
  `shape diff a.shape b.shape --policy contract.json`. `shape check` ignores the `drift` key.

```
shape diff BASE.shape CURRENT.shape
    [--null-rate X] [--cardinality-ratio-max X] [--cardinality-ratio-min X]
    [--mean-shift-std X] [--min-severity low|medium|high]
    [--threshold KEY=VALUE]...           any threshold by name, e.g. category_tvd=0.2
    [--column-threshold COLUMN:KEY=VALUE]...
    [--ignore COL1,COL2] [--only COL1,COL2] [--policy POLICY.json]
    [--json RESULT.json] [--fail-on-drift] [--fail-on breaking|additive|cosmetic]
    [--version-from X.Y.Z]
```

Contract rules for flags: `"min_true_rate"` and `"max_true_rate"` on a column (a number from 0 to
1; a column that is not a boolean or 0/1 is a violation).

## Windows and streams

`shape.diff` takes a stream window (`WindowProfile`), its `.profile` document, or a profile-engine
document wherever it takes a `Profile`:

```python
closed = profiler.process(batch)
shape.diff(shape.load("baseline.shape"), closed[0]).drifted
```

`ShapeMonitor(reference, every=500)` compares the buffered rows with `reference` (a profile, a Shape
model or a capture) every `every` rows with the same engine and takes `shape.diff`'s `thresholds`,
`ignore_columns`, `column_thresholds`, `only_columns` and `policy`. A `MonitorEvent` has
`rows_seen`, `max_drift` (the largest score; 0 when nothing passed its threshold) and `drifts`:
objects with `column`, `kind`, `severity`, `score`, `path`, `before` and `after`; `event.changes`
gives them as `shape.diff` records. `shape.drift.compare(before, after)` and
`ShapeTimeline.changes()` return the same objects; `shape.drift.gate` checks their scores.

## Planting drift: `shape generate-drift`

`DriftPlan` (`shape.generation.drift_plan`) applies a list of events to a generation schema one day
at a time. Each day is generated by the ordinary engine from a copy of the schema with that day's
events applied, so every strategy, rule and relationship still holds, and the per-day schemas are a
diffable history. `shape time-travel` evolves rows (growth, churn); this changes what the data looks
like, and it writes the answer key.

| Event | Spec |
|---|---|
| `null_rate` | `{"to": 0.4}` |
| `category_weights` | `{"weights": {"completed": 40, "shipped": 40, "cancelled": 20}}` (a new key appears) |
| `new_category` | `{"value": "lost", "share": 0.02}` |
| `distribution` | `{"params": {"mean": {"add": 0.3365}, "sigma": {"factor": 1.5}, "probability": 0.95}}` or `{"scale": 1.4}` |
| `add_column` | `{"definition": {"type": "string", "generator": {...}}}` |
| `drop_column` | none |
| `type_change` | `{"to": {"type": "string", "generator": {...}}}` |

Every event names `table` and `column`, and `start` (an ISO date or a day number, day 0 being the
plan's start). `end` (exclusive) makes a window: the schema reverts. `ramp_days: N` builds the
change up in a straight line over N days, then holds (`add_column`, `drop_column` and `type_change`
are on or off). A parameter is a bare number (set), `{"set": v}`, `{"add": v}` or `{"factor": v}`.
`scale` multiplies the values: `log_normal` adds `ln(factor)` to `mean` (x1.4 is `mean += ln 1.4`),
`normal` scales `mean` and `std_dev`, `uniform` its `min` and `max`, `pareto` its `min`. A boolean
or 0/1 column is a `bernoulli` distribution, so its rate can drift too. A `new_category` is not in
the schema before its day.

```json
{"start": "2026-03-01", "days": 14, "events": [
  {"kind": "null_rate", "table": "orders", "column": "note", "start": "2026-03-04",
   "ramp_days": 4, "to": 0.4},
  {"kind": "new_category", "table": "orders", "column": "status", "start": "2026-03-06",
   "value": "lost", "share": 0.08},
  {"kind": "distribution", "table": "orders", "column": "total", "start": "2026-03-08", "scale": 1.4},
  {"kind": "type_change", "table": "orders", "column": "vip", "start": "2026-03-11",
   "end": "2026-03-13", "to": {"type": "string",
   "generator": {"strategy": "weighted_enum", "values": {"yes": 1, "no": 1}}}}
]}
```

```
shape generate-drift orders.gen.json plan.json -o feed/ --rows orders=4000 --format parquet
```

writes `feed/<date>/<table>.parquet`, `feed/_specs/<date>.json` (that day's schema) and
`feed/ground_truth.json`. The seed of day N is the schema's seed (or `--seed`) plus N: a rerun gives
the same files and each day is a fresh sample.

```python
from shape.generation.drift_plan import DriftPlan

plan = DriftPlan.load("plan.json")
plan.schema_at(schema, "2026-03-08")             # the schema of one day
result = plan.generate_day(schema, 7, row_counts={"orders": 4000})
plan.ground_truth()                              # the answer key
plan.expected_changes(0, 10)                     # what shape.diff should report between two days
```

The answer key (`ground_truth.json`, version 1) lists every event (`id`, `kind`, `table`,
`column`, `start`, `end`, `ramp_days`, `full_effect_from`, `shape`: `step`, `ramp`, `window` or
`ramp_window`, `spec`, and `detected_as`: the `shape.diff` kinds that find it) and, for every day,
which events had taken effect and how far (0 to 1). `expected_changes(a, b)` turns it into the
changes to expect between two days, so a test can plant drift, profile two days and check the diff:
small steps of a ramp stay under the thresholds, so compare days far enough apart.
