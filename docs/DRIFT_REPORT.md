# Drift report: `shape publish-report`

`shape diff` answers "did this profile drift from that one?" with a JSON file. `shape publish-report`
answers "how has this feed drifted over time?": it diffs a history of profiles, each with the one
before it, and writes the changes as star tables plus a Power BI semantic model, so the history can
be sliced by run, column, kind of change and date.

```
shape publish-report orders-2026-03-01.shape orders-2026-03-06.shape orders-2026-03-11.shape -o report/
shape publish-report --registry orders --registry-root reg/ --since 2026-03-01 -o report/ --format csv
shape fabric publish-report ...     # the same command in the fabric group
```

It needs the `sqllocks-shape-fabric` plugin ([fabric-commands](plugins/fabric-commands.md)).

## The history

* **Files.** `PROFILE.shape...` in the order given, oldest first (a `shape profile export` JSON file
  works too). A run's date is the first `YYYY-MM-DD` in its file name (`orders-2026-03-06.shape`);
  a name with no date gives a run with no date. Files must be raw profiles too: a safe profile
  (`shape profile safe`) is refused with exit 2, naming the file.
* **Registry.** `--registry NAME --registry-root DIR` reads the commits of `NAME` in a
  [`shape registry`](REGISTRY.md), oldest first. A commit's date is its `business_date` (`--business-date`
  when it was committed), else the day it was committed (UTC). `--since DATE` keeps the runs dated
  that day or later, and the commit before them as the first run's baseline. The report diffs full
  profiles, so the commits must be raw profiles (`shape registry DIR commit NAME PROFILE --allow-raw`);
  a registry that holds only safe profiles is refused (exit 2), because a safe profile holds too
  little to compare.

Fewer than two profiles (counting a baseline) exits 2, as does giving files and `--registry`
together, or `--since` or `--registry-root` without `--registry`. Every profile is loaded and diffed
before anything is written.

The comparison is `shape diff`'s, with its options: `--null-rate`, `--cardinality-ratio-max`,
`--cardinality-ratio-min`, `--mean-shift-std`, `--min-severity`, `--threshold KEY=VALUE`,
`--column-threshold COLUMN:KEY=VALUE`, `--ignore`, `--only` and `--policy POLICY.json`, with the
defaults of [DRIFT.md](DRIFT.md). A bad value, an unknown threshold or a missing policy file exits 2.

## The tables

`-o DIR` gets `data/NAME.parquet` for each table (`--format csv` writes `NAME.csv`), `drift.bim` and
`report.json`. Keys are integers, assigned in a fixed order, so the same history gives the same
tables.

| table | one row per | columns |
|---|---|---|
| `fact_drift_change` | change that passed its threshold | `run_id`, `column_id`, `kind_id`, `size` (the diff's 0 to 1 score), `threshold` |
| `dim_run` | comparison (the later profile against the one before it), numbered from 1 | `run_id`, `run_label` (file name without its extension, or the first 12 characters of the content id), `baseline_label`, `profile` (the path given, or the content id), `run_date`, `date_key` |
| `dim_column` | column of any profile, plus any column a change names | `column_id`, `column_key` (`column`, or `table.column` for a profile of several tables), `table_name`, `column_name` (empty for a change of the table itself, whose `column_key` is the table's name, or `(table)` for a profile of one table) |
| `dim_kind` | kind of change `shape diff` can report, whether or not it occurred | `kind_id`, `kind`, `severity`, `threshold_key` |
| `dim_date` | day from the first run's date to the last | `date_key` (`YYYYMMDD`), `date`, `year`, `quarter`, `month`, `month_name`, `day`, `day_of_week` |

`threshold` is the setting the change passed, taken from the thresholds that applied to its column
(a `--column-threshold` counts): `null_rate` for `null_rate_change`, `mean_shift_std` for
`mean_shift`, and so on. A kind whose threshold is a ratio takes the upper bound when the value rose
and the lower one when it fell (`cardinality_ratio_max` or `_min`, `std_ratio_max` or `_min`,
`row_count_ratio_max` or `_min`; `dim_kind.threshold_key` names both as `a|b`). A kind with nothing to
pass (`column_added`, `column_removed`, `table_added`, `table_removed`, `dtype_change`,
`pattern_change`, `distribution_change`, `new_categorical_values`) has an empty `threshold`. A run with
no dated name has an empty `run_date` and `date_key`, and `dim_date` is empty when no run has a date.

## The model

`drift.bim` is a Tabular Object Model document (compatibility level 1604) written by the exporter
behind [`shape export-model`](plugins/fabric-commands.md#export-model). Relationships, all one-to-many
with the filter running from the dimension: `dim_run[run_id]`, `dim_column[column_id]` and
`dim_kind[kind_id]` to the same columns of `fact_drift_change`, and `dim_date[date_key]` to
`dim_run[date_key]` (a date filters the runs of that day, and through them the changes). The
measures are on `fact_drift_change`:

| measure | DAX | reads as |
|---|---|---|
| `Changes` | `COUNTROWS('fact_drift_change')` | changes (by run, kind, column or date) |
| `Runs` | `COUNTROWS('dim_run')` | runs in the filter |
| `Changes per Run` | `DIVIDE([Changes], [Runs])` | changes per run; sliced by run, that run's count |
| `Runs with Change` | `DISTINCTCOUNT('fact_drift_change'[run_id])` | runs with at least one change |
| `Share of Runs with Change` | `DIVIDE([Runs with Change], [Runs])` | the share of runs with any change |
| `Columns with Change` | `DISTINCTCOUNT('fact_drift_change'[column_id])` | columns that changed |
| `Kinds with Change` | `DISTINCTCOUNT('fact_drift_change'[kind_id])` | kinds that occurred |
| `Share of Changes by Kind` | `DIVIDE([Changes], CALCULATE([Changes], ALL('dim_kind')))` | each kind's share of the changes |
| `Share of Changes by Column` | `DIVIDE([Changes], CALCULATE([Changes], ALL('dim_column')))` | each column's share |
| `Average Change Size` | `AVERAGE('fact_drift_change'[size])` | mean score |
| `Largest Change Size` | `MAX('fact_drift_change'[size])` | highest score |

(In the file each name is written with its table, `'fact_drift_change'[Changes]`.) Each table's
partition is a Power Query expression that reads a Lakehouse table of the same name, with the
placeholders `{workspace_id}` and `{lakehouse_id}` for you to fill in, as `export-model` writes for
the `lakehouse` source. Load `data/*.parquet` (or the CSV files) into Lakehouse tables with those
names.

### Opening `drift.bim` in Power BI Desktop

Power BI Desktop does not open a `.bim` file itself. There are two ways to the same report:

1. **Tabular Editor.** Open `drift.bim` (File, Open, From File), fill in the two placeholders of the
   partitions (or replace each partition with the source you load the tables from), and deploy the
   model to a workspace through its XMLA endpoint, or to a local Analysis Services instance. Then
   connect to it from Power BI Desktop or build the report in the service.
2. **By hand in Power BI Desktop.** Get data, Parquet (or Text/CSV), and load the five tables from
   `data/`. In Model view, create the four relationships above (one-to-many, single direction,
   from the dimension), then add the measures of the table above with New measure.

A first report: a matrix of `dim_kind[kind]` by `dim_run[run_label]` with `Changes`; a line chart of
`Changes per Run` over `dim_date[date]`; a card for `Share of Runs with Change`.

## `report.json`

```json
{
  "format": "shape-drift-report",
  "version": 1,
  "source": {"kind": "files", "registry": null, "since": null},
  "inputs": [{"label": "orders-2026-03-01", "path": "orders-2026-03-01.shape",
              "content_id": null, "sha256": "...", "date": "2026-03-01"}],
  "thresholds": {"thresholds": {"null_rate": 0.05, "...": "..."}, "columns": {}, "ignore": [], "only": []},
  "format_of_tables": "parquet",
  "tables": {"dim_column": 6, "dim_date": 8, "dim_kind": 27, "dim_run": 3, "fact_drift_change": 10},
  "runs": 3,
  "changes": 10
}
```

`source.kind` is `files` or `registry` (then `registry` is the name and `since` the date given).
`inputs` lists the profiles in order: a file has its `path` and the `sha256` of its bytes; a registry
commit has its `content_id` (the same sha256) and no path. `thresholds` is the policy that was
applied: every threshold with its default filled in, the per-column overrides, and the ignore and only
lists. `tables` has each table's row count. A reader refuses a `version` newer than it knows, naming
both versions; the format is covered by a compatibility test over a frozen version 1 file.

The same inputs give byte-identical `drift.bim` and `report.json` and equal tables.

Exit codes: `0` written; `1` a file could not be written; `2` the input is wrong (see above).
