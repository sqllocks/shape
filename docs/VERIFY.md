# Verifying data: gates, quarantine and `shape verify`

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


`shape verify` checks tables you generated or received against a schema, with a set of
validation gates, and writes a report. The same gates, and a quarantine for what fails them,
are available from Python as `shape.quality`.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

A directory may mix Parquet, CSV and JSONL files: with `--format auto` (the default) every
file is loaded as a table named after its file stem, each by its own extension. A table that
exists in two formats (`orders.csv` and `orders.parquet`) is refused with exit `2`. With an
explicit `--format csv` only CSV files load, and each file of another format is named in a
warning (`skipped orders.parquet: format parquet differs from the requested csv; ...`).

Exit codes: `0` every gate passed, `1` a gate failed (or a warning under `--strict`), `2` the
input could not be read (missing path, no data files, unreadable schema).

Without `--schema` only row counts are reported. With one, `shape verify` runs:

| Gate | Fails (error) | Warns |
|---|---|---|
| `schema_conformance` | a declared table or column is missing | extra columns; a column type that does not fit the declared type |
| `null_constraint` | nulls (or NaN) in a column declared non-nullable | |
| `unique_constraint` | duplicates in the primary key | |
| `referential_integrity` | foreign-key values with no parent key (a composite key as a whole: each child combination must be a parent's; a key with a null part is not checked); parent and child column lists of different lengths | a relationship whose table is absent |
| `distribution` (`--statistical`) | never | KS test (`distribution`) or chi-squared test (`enum`) with p below 0.05 |

`distribution` needs scipy (`pip install "sqllocks-shape[scipy]"`) and is skipped with a
warning when it is not installed.

## The verify configuration: range, temporal, drift and file gates

`--config CONFIG.json` runs the four gates that the schema does not drive, with the same
settings as `ValidationContext.config` in Python. It is a document of its own, separate from
the gate schema and the contract format, with its own `format` and `version`:

```json
{
  "format": "shape-verify-config",
  "version": 1,
  "ranges": {"orders.amount": {"min": 0}},
  "date_range": {"start": "2026-09-30T00:00:00", "end": "2026-10-01T23:59:59"},
  "no_future": ["orders.placed_at"],
  "ordering": [{"table": "orders", "start": "placed_at", "end": "shipped_at"}],
  "baseline": {"orders": {"columns": {"order_id": "int64", "amount": "float64"}}},
  "file_paths": ["out/orders.parquet"],
  "check_data_files": true,
  "distribution_alpha": 0.05
}
```

| Key | Gate it turns on | Meaning |
|---|---|---|
| `ranges` | `range_constraint` | `"table.column"` stays within `min` / `max` |
| `date_range`, `no_future`, `ordering` | `temporal_consistency` | timestamp columns lie in `start`..`end` (ISO 8601); `table.column` has no future dates; `end >= start` per rule |
| `baseline` | `schema_drift` | removed tables or columns and changed types fail; new ones warn (dtype names: `int64`, `float64`, `str`, `bool`, `datetime64[us]`, `object`) |
| `fail_on`, `classes`, `column_classes` | `schema_drift` | optional. Every change has a class (`docs/DRIFT.md`, "Change classes"); a change of class `fail_on` (`breaking` by default, `additive` or `cosmetic`) or a stricter one fails, the others warn. `classes` and `column_classes` are the drift policy's overrides (`int32` to `int64` is breaking, unless `{"dtype_widening": "additive"}`). The gate's details list `breaking`, `additive` and `cosmetic` changes, and each gate in the JSON report carries its `fail_on` (`null` for a gate that does not compare with a baseline). `shape.yml` sets the same keys (`docs/PROJECT.md`); this file beats it |
| `timeseries` | `timeseries_quality` | gaps, stuck values and daylight-saving transitions of a time series (below) |
| `reconcile` | `reconciliation` | a source and a target compared on counts, partitions and aggregates (below) |
| `file_paths`, `check_data_files` | `file_format` | the listed files (and, with `check_data_files`, every data file that was loaded) exist, are non-empty and read in full |
| `distribution_alpha` | `distribution` (`--statistical`) | the p-value below which it warns (default 0.05) |
| `classifications`, `memorization` | `memorization` (`--source`) | column classifications, and the gate's options (below) |
| `utility` | `utility` (`--source`) | the model target and the minimum retention (below) |
| `privacy` | none (`shape report-card` only) | `max_membership_auc` (0 to 1, default 0.6) and `seed` (default 0) of the membership-inference test of [REPORT_CARD.md](REPORT_CARD.md); `shape verify` accepts and ignores it |

Each gate runs only when its keys are present, with or without `--schema`; the report lists
the gates that ran. Every key is checked when the file is read: an unknown key, a wrong type,
a bad date, a NaN bound, a `min` above its `max` or a `start` after its `end` is refused with
exit `2` and a message naming the key, so a misspelt rule cannot be skipped silently; a `ranges`, `no_future` or `ordering` entry that names a table or column
the data does not have is a warning in the report. `date_range` and `no_future` apply to columns whose type is a timestamp
(Parquet timestamps; CSV and JSONL text columns are strings, so convert them first).

## Checking generated data against the data it came from: `--source`

Two gates compare generated tables with the real tables they were made from. `--source DATA`
loads the real data (same layout and `--format` as the generated data; tables are matched by
name) and turns them on. They are in the report like any other gate, and exit `1` when one fails.

### The memorization gate

Runs whenever `--source` is given. For each table that both sides have it reports the exact-match
rate and the nearest-neighbour distance between generated rows and source rows, and **fails on any
reproduced source row in a column classified `CONFIDENTIAL` or above**. Classifications are the
ones of `docs/PRIVACY_MODEL.md` (`PUBLIC < INTERNAL < CONFIDENTIAL < SECRET < TOP_SECRET`; `PII` and
`SENSITIVE` rank as `CONFIDENTIAL`), given in the verify configuration:

```json
{
  "format": "shape-verify-config",
  "version": 1,
  "classifications": {"people.name": "CONFIDENTIAL", "people.email": "SECRET", "people.age": "INTERNAL"},
  "memorization": {"fail_at": "CONFIDENTIAL", "min_nn_distance": 0.05, "max_rows": 5000}
}
```

* `classifications`: `"table.column"` to level. A column that is not listed is `PUBLIC`.
* `memorization.fail_at` (default `CONFIDENTIAL`): the lowest level at which a reproduced row fails.
* `memorization.min_nn_distance` (default none): also fail when a generated row is closer to a
  source row than this, in the standardised distance below.
* `memorization.max_rows` (default 5000): the cap of the nearest-neighbour search only.

How a row is judged:

* The **key** is the table's restricted columns (at or above `fail_at`) that both tables have. Text,
  integer, date and other non-numeric columns form the key; numeric columns join it only when no
  restricted column is non-numeric. This is deliberate: a copy with noise added to its numbers (the
  `bootstrap` strategy jitters by default) has different numbers and the same text, and is still a
  copy. Numbers always count in the nearest-neighbour distance.
* A generated row is **reproduced** when its key equals the key of a source row. Null and NaN never
  match. A key that more than one source row has (two people called the same) identifies nobody, so
  it does not count.
* With no restricted column in a table, the key is every shared column and the result is only
  informational: the gate cannot fail on that table, and says so in a warning (`--strict` turns it
  into a failure). Classify your columns.
* The **nearest-neighbour distance** is the Euclidean distance over the numeric columns both tables
  share, each divided by the source's standard deviation (nulls take the source median). The report
  gives `min`, `p05` and `median`; a copy is at `0.0`.

The report names the table, the columns and the **index of each reproduced row in the generated
table** (up to 1,000 of them), and never prints a value:

```
ERROR [memorization]: people: 100 of 100 generated rows reproduce a source row on the CONFIDENTIAL+ columns [email, name] (rows 0, 1, 2, ...)
```

A generator that copies source rows fails it (the negative control in `tests/quality/test_memorization.py`),
and so does the `bootstrap` strategy at `CONFIDENTIAL` and above: bootstrapping reproduces the source,
as `docs/FIDELITY_TIERS.md` says.

### The utility gate (train on synthetic, test on real)

Needs scikit-learn: `pip install "sqllocks-shape[advanced]"` (without it the command stops with exit
`2` and says so). It runs when the configuration has a `utility` section, and `--source` is given:

```json
{"format": "shape-verify-config", "version": 1,
 "utility": {"table": "customers", "target": "churned", "min_retention": 0.8}}
```

It holds out part of the real table, trains one model on the rest of the real table and one on the
generated table, scores both on the held-out real rows, and fails when `synthetic score / real score`
(the retention) is below `min_retention`. Keys: `table` and `target` (required), `task` (`auto`,
`classification`, `regression`), `min_retention` (default 0.8; 0 < x <= 1), `test_fraction` (default
0.3), `seed` (default 0), `max_rows` (default 20,000).

* The task is classification when the target is text, boolean, or an integer with at most 10
  distinct values; otherwise regression.
* The metric is **balanced accuracy** (classification) or **R squared** (regression; a negative R
  squared counts as 0).
* The model is a random forest of 100 trees. Both models learn from the same number of rows (the
  generated table is subsampled evenly to the size of the real training part), and the generated
  table never sees the held-out rows.
* Features: the columns both tables share, except the target, constant columns and identifier-like
  text columns (more than 50 distinct values in over half the rows).
* If the model trained on real data does not beat chance (by 0.05 balanced accuracy, or 0.05 R
  squared), the target cannot be predicted from the other columns and the gate fails saying so, since
  a retention would mean nothing.

The report's `details` carry `real_score`, `synthetic_score`, `retention`, the row counts and the
features used. A generated table whose columns lost the signal fails (the negative control in
`tests/quality/test_utility.py`).

## Time-series quality: gaps, stuck values, daylight saving

`"timeseries"` is a list of rules. Each names a table and its time column (a timestamp, or ISO 8601
text) and any of three checks:

```json
{
  "format": "shape-verify-config",
  "version": 1,
  "timeseries": [
    {"table": "readings", "time": "ts", "by": ["sensor"], "every": "1h",
     "gaps":  {"max_missing": 0},
     "stuck": {"column": "value", "max_run": 5},
     "dst":   {"time_zone": "Europe/Berlin", "fall_back": "repeat"}}
  ]
}
```

| Key | Meaning |
|---|---|
| `table`, `time` | the table and its time column (required) |
| `by` | columns that identify one series (a sensor, a meter); every check runs per series, in time order, whatever the row order |
| `every` | the regular step: a whole number and a unit, `us`, `ms`, `s`, `min`, `h`, `d` or `w` (`"15min"`). A calendar month has no fixed length and is not accepted |
| `gaps` | fails when more than `max_missing` (default `0`) steps are missing: between two consecutive times, a distance that is a whole number of steps larger than one counts the steps in between. Repeated times and distances that are not a multiple of `every` are warnings, not gaps |
| `stuck` | fails when a series repeats the same value in more than `max_run` consecutive rows (`column`, `max_run`); a null or NaN breaks a run, and a run of exactly `max_run` is allowed |
| `dst` | checks the clock changes of `time_zone` (an IANA name, always given explicitly) that fall inside the data. The data are wall-clock times: naive timestamps as they are, aware ones converted to the zone |

`gaps` and `dst` need `every`. The daylight-saving check reports two things per transition:
rows at **local times that do not exist** (the hour the clocks skip in spring is an error if any
row falls in it), and the **repeated local hour** in autumn. With `"fall_back": "repeat"` (the
default) each step of the repeated hour must occur twice in a series that crosses it, with
`"once"` exactly once (data that were disambiguated). A series that does not cover the repeated
hour is not checked, and with naive times and a zone the missing hour is not counted as a gap.
Rows with no time are skipped with a warning.

From Python:

```python
from shape.quality import check_timeseries

findings = check_timeseries(tables, [{"table": "readings", "time": "ts", "every": "1h", "gaps": {}}])
```

Each finding has `rule` (`timeseries.gaps`, `.stuck`, `.dst`, `.duplicates`, `.off_grid`,
`.null_times`, `.time_column`, `.table_exists`, `.column_exists`), `severity` (`error` or
`warning`), `table`, `column`, `message`, `expected` and `observed` (with the gaps, runs or
transitions, up to 20 of each).

## Reconciliation: source against target

`"reconcile"` is a list of rules, each comparing two tables. A side is a path read through the
same source layer as `shape profile` (CSV, Parquet, JSONL, globs, folders, Delta tables), or
`{"table": "name"}` for a table of the data being verified:

```json
{
  "format": "shape-verify-config",
  "version": 1,
  "reconcile": [
    {"name": "orders vs warehouse",
     "source": {"table": "orders"},
     "target": "warehouse/orders.parquet",
     "partition_by": ["order_date"],
     "key": ["customer_id"],
     "aggregates": [{"column": "amount", "agg": "sum", "tolerance": {"abs": 0.01}},
                    {"column": "amount", "target_column": "amt", "agg": "max"}],
     "tolerance": {"rel": 0.001},
     "count_tolerance": {"abs": 0}}
  ]
}
```

| Key | Meaning |
|---|---|
| `source`, `target` | the two sides (required) |
| `name` | a label for the reports (default: the two sides) |
| `partition_by` | row counts per value combination of these columns; a partition on one side only counts as `0` on the other |
| `key` | row counts per key, keys that exist on one side only, and each aggregate per key |
| `aggregates` | `column`, `agg` (`sum`, `mean`, `min`, `max`, `count` of non-null values, `count_distinct`), optional `target_column` and `tolerance`. Without `key` they cover the whole table. Non-numeric values compare by equality |
| `tolerance` | `{"abs": x, "rel": y}`, the default for counts and aggregates |
| `count_tolerance` | overrides `tolerance` for counts |

A difference `d` is within tolerance when `abs(d) <= abs + rel * max(abs(source), abs(target))`.
Both default to `0`, an exact match: give a tolerance for floating-point sums. The row counts of
the two sides are always compared. A missing column is a `reconcile.column_exists` error; a side
that cannot be read fails the gate with the reason.

```python
from shape.quality import reconcile

result = reconcile("orders.parquet", "warehouse/orders.parquet", key=["customer_id"],
                   aggregates=[{"column": "amount", "agg": "sum"}], tolerance={"abs": 0.01})
result.passed, result.findings    # rules: reconcile.count, .partition, .key, .key_count, .aggregate
```

## In a contract (`shape check`)

Contract v1 accepts the same two lists as optional top-level rules, `"timeseries"` and
`"reconcile"` (an older Shape refuses them as unknown keys). They check data, not a profile, so
`shape check` takes the data too, and a contract with these rules and no data exits `2`:

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

```python
shape.check(profile, "contract.json", data=tables_or_path)
```

Each error becomes a violation with `column` (`table.column`), `rule` (`timeseries.gaps`, ...),
`expected` and `observed`; reconcile violations add `reconciliation` (the rule's name). Warnings
are not violations. The rules go at the top level of the contract (they name their own tables),
not inside a table's contract.

## The gate schema

The schema is a JSON document of Shape's own:

```json
{
  "format": "shape-gates",
  "version": 1,
  "tables": {
    "customer": {
      "primary_key": ["customer_id"],
      "columns": {
        "customer_id": {"type": "integer"},
        "gender": {"type": "string", "nullable": true, "enum": {"M": 0.49, "F": 0.51}},
        "spend": {"type": "float", "distribution": {"name": "lognorm", "params": {"s": 1.0, "scale": 20.0}}}
      }
    },
    "order": {"primary_key": ["order_id"], "columns": {"order_id": {"type": "integer"}, "customer_id": {"type": "integer"}}}
  },
  "relationships": [
    {"name": "placed_by", "parent": "customer", "child": "order",
     "parent_columns": ["customer_id"], "child_columns": ["customer_id"]}
  ]
}
```

Types: `integer`, `bigint`, `string`, `float`, `decimal`, `date`, `datetime`, `boolean`, `uuid`
(any other type is not type-checked). Columns are non-nullable unless `"nullable": true`.
`distribution.name` is a `scipy.stats` distribution and `params` its parameters. An `enum`'s keys
are text (JSON object keys), so an integer or boolean column is matched by the text of its values
(`"1"`, `"true"` or `"True"`).
Every value is checked when the schema is read: `nullable` must be `true` or `false`,
`primary_key`, `parent_columns` and `child_columns` lists of column names, `enum` an object of
weights and `distribution` an object with a `name`; anything else is refused with exit `2`.

`--schema` also accepts a profile made by `shape profile` (a `.shape` file, or its JSON): a
column is nullable if the profile saw nulls in it, and the primary keys and foreign keys the
profile detected are checked. Profile real data, then verify what you generated from it.

## From Python

```python
from shape.quality import GateRunner, ValidationContext, load_gate_schema, load_tables

tables = load_tables("out/")                       # {"customer": pa.Table, ...}
ctx = ValidationContext(tables=tables, schema=load_gate_schema("gates.json"),
                        config={"ranges": {"order.total": {"min": 0}}})
results = GateRunner().run_all(ctx)                # all nine gates
print(GateRunner.summary(results))
```

Four more gates are driven by `ValidationContext.config` rather than the schema (from the CLI,
by `--config`, above):
`range_constraint` (`ranges`), `temporal_consistency` (`date_range`, `no_future`, `ordering`),
`schema_drift` (`baseline`: `{"table": {"columns": {"col": "int64"}}}`) and `file_format`
(`file_paths`: output files must exist, be non-empty and read in full). Register your own
with `GateRunner.register_gate(name, factory)`.

## Quarantine

```python
from shape.quality import QuarantineManager

qm = QuarantineManager(domain="retail")
qm.quarantine_table(tables["order"], "quarantine/", run_id="2026-10-01", table_name="order",
                    reason="orphan foreign keys", gate_name="referential_integrity")
qm.quarantine_file("out/order.parquet", "quarantine/", "2026-10-01", "failed verify")
qm.get_quarantine_report("quarantine/", "2026-10-01")
```

Each artifact lands in `quarantine/<domain>/<run_id>/` next to a `.._quarantine_meta.json`
(reason, gate, UTC time, original path). `domain`, `run_id` and table names are used as path
components, so Shape refuses any that is not a plain name (letters, digits, `.`, `_`, `-`).

## One command, two inputs

`shape verify` also checks signatures (see `docs/SIGNING.md`): when its argument is a `.shape`
file it verifies that artifact against `--key PUBLIC.pub`, and for any other path it runs the
gates above.

## Scores by dimension and failing rows

`shape scorecard` runs these gates and scores accuracy, completeness, conformity, consistency,
timeliness and uniqueness from them, with failing-row samples and an optional flag column. See
[SCORECARD.md](SCORECARD.md). `--slice-by` scores them per slice; `shape skew` compares serving
data with training data ([FAIRNESS_AND_SKEW.md](FAIRNESS_AND_SKEW.md)).
