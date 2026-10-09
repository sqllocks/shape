# Data quality scorecards: `shape scorecard`

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


`shape scorecard` runs the validation gates of [`shape verify`](VERIFY.md) and turns their
results into one score per data quality dimension, with the failing rows behind each score.
It adds no checks of its own: a score is computed from the checks that already exist.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

Exit `0` when the scorecard was produced and `2` on an input error (missing data, bad schema,
bad suppression file). The command reports quality; it does not fail a build, with one
exception: `--max-slice-gap G` exits `1` when a dimension's slice gap is above `G` (see
[FAIRNESS_AND_SKEW.md](FAIRNESS_AND_SKEW.md)). Use `shape verify` for a pass or fail exit code.

## The six dimensions and the checks behind them

Each gate scores exactly one dimension. A dimension with no check that ran has no score (`n/a`
in Markdown, `null` in JSON). The mapping is `GATE_DIMENSION` in `shape.quality.scorecard`,
and a test keeps this table equal to it. The last two gates run only when the verify
configuration has their rules (`CONFIG_GATE_DIMENSION`). The memorization and utility gates compare
generated data with its source and are not scored.

| Gate | Dimension | What it measures |
|---|---|---|
| `distribution` | accuracy | observed values against the declared distribution |
| `range_constraint` | accuracy | values outside the configured minimum and maximum |
| `null_constraint` | completeness | nulls in columns declared non-nullable |
| `file_format` | conformity | data files that are missing, empty or unreadable |
| `schema_conformance` | conformity | declared tables and columns that are missing or mistyped |
| `schema_drift` | conformity | tables and column types that differ from the baseline |
| `referential_integrity` | consistency | foreign-key values with no parent row |
| `temporal_consistency` | timeliness | dates outside the range, in the future, or ending before they start |
| `unique_constraint` | uniqueness | repeated primary-key values |
| `reconciliation` | consistency | a source and a target that differ in counts or aggregates (`reconcile` rules of the verify configuration; one pass/fail check) |
| `timeseries_quality` | timeliness | gaps, stuck values and daylight-saving errors (`timeseries` rules of the verify configuration; one pass/fail check) |

## How a score is computed

A *check* is one gate on one table and column set (for example `null_constraint` on
`customer.name`).

- A gate with a row-level form (`null_constraint`, `unique_constraint`, `referential_integrity`,
  `range_constraint` on numeric columns, `temporal_consistency`) scores each check
  `100 x (1 - failing rows / rows)`.
- Any other gate is one check: 100 when it passes, 0 when it fails.
- A dimension scores the mean of its checks, rounded to two decimals. The overall score is the
  mean of the scored dimensions.

## Column owners

Each failing check shows the owner of its column, read from the project file `shape.yml`
([PROJECT.md](PROJECT.md)): `sources.NAME.columns.COLUMN.owner`. The file is found from the working
folder upwards; `--project FILE` (or a folder holding `shape.yml`) names one, `--no-project` ignores
it. The source is `--source NAME`, else the only source, else the one named like the table; with none
of those, each source is searched in name order and the first owner found is shown. A column is
matched as `shape diff` matches it (`table.column`, then the column name, then a glob). With no
project file there are no owners and the column stays empty; an invalid project file is an error, as
with `shape project validate`.

## Trends

With `--history DIR --name NAME` the scorecard compares each dimension with the last scorecard
stored under `NAME` in that registry (a local registry, as `shape registry` uses) and reports
`improving`, `declining`, `steady` or `no data`. Add `--record` to store this scorecard for next
time. The registry keeps the scores only, never samples. With `--slice-by` the slice gaps are
compared as well (`widening`, `narrowing`, `steady`, `no data`).

## Snooze and suppress

Known issues go in a JSON file, passed with `--suppressions`:

```json
{
  "format": "shape-scorecard-suppressions",
  "version": 1,
  "entries": [
    {"action": "suppress", "check": "null_constraint", "table": "customer", "column": "name",
     "reason": "legacy import"},
    {"action": "snooze", "check": "unique_constraint", "until": "2027-01-31",
     "reason": "fix scheduled"}
  ]
}
```

- `check` is one of the gate names above. `table` and `column` are optional and match anything
  when left out. A `reason` is required.
- A `snooze` needs an `until` date (`YYYY-MM-DD`) and applies through that day; after it the
  check counts again. A `suppress` has no end.
- A hidden check is left out of the score but listed under `known_issues` with its reason and
  failing count. A check that passes is never hidden. A dimension whose checks are all hidden has
  no score.
- A file with a newer `version` than this release reads is refused.

## Failing-row samples

For every failing row-level check the scorecard shows up to `--samples N` failing rows (default
5, lowest row number first, row numbers start at 0): the gate, the table, the row and the value
of the columns that check looked at. It never shows other columns of the row.

Samples are safe by default. A column is *classified* when it is named with `--classified
TABLE.COLUMN`, when its name reads as personal data (`email`, `phone`, `ssn`, `address`,
`first_name`, ...), or when a sample of its values matches a personal-data pattern. A classified
column shows `[redacted]` for every value; a null stays visible as null. `--show-classified`
turns this off.

## Flag column

`--flag-output DIR` writes each table to `DIR` in its own format with an added boolean column
(`_shape_dq_failed`, or `--flag-column NAME`) that is true for every row failing a counted
check. Checks hidden as known issues do not flag rows. The input files are never changed, an
existing column of that name is an error, and the quarantine tables written by
`shape.quality.QuarantineManager` are untouched.

## Python

```python
from shape.quality import VerifyRunner, load_tables
from shape.quality.scorecard import build_scorecard

result = VerifyRunner(schema).run(tables)
card = build_scorecard(result, tables, schema=schema, samples=3)
print(card.to_markdown())
```

`record_scorecard(registry, name, card)` and `scorecard_trend(registry, name)` store and read the
history; `load_suppressions(path)` reads a known-issue file.

## Slices

`--slice-by COLUMN[,COLUMN]` scores every dimension per slice, reports each slice's share of
rows, outcome rates and null rates, and adds a `slices` object to the JSON. The measures, the
small-slice rule and their limits are in [FAIRNESS_AND_SKEW.md](FAIRNESS_AND_SKEW.md);
`build_scorecard(..., slice_by=, label=, reference=)` is the same from Python.

## Stored format

The JSON scorecard declares `"format": "shape-scorecard"` and an integer `"version"`.

- **Version 1** is a scorecard without slices, as before. Its JSON Schema is
  `src/shape/schemas/scorecard-v1.schema.json`.
- **Version 2** is version 1 plus the `slices` object, written only when `--slice-by` (or
  `slice_by=`) is given. Its JSON Schema is `src/shape/schemas/scorecard-v2.schema.json`.

A scorecard without slices is still written as version 1, byte for byte, so a stored trend
history keeps loading; this release reads both versions from `--history`, and refuses a newer
one with an error that says to upgrade Shape.
