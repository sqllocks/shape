# Slices, representation and training-serving skew

A quality score for a whole table can hide a slice where the data is much worse: one region
with most of the nulls, one group whose records fail validation, or a group that is
under-represented compared with the population the data should describe. A model trained on one
dataset and served on another fails quietly when the serving features drift away from the
training features. This page covers the two tools for that:

- `shape scorecard --slice-by` (the scorecard of [SCORECARD.md](SCORECARD.md), per slice);
- `shape skew TRAIN SERVING` (training-serving skew, built on the drift engine of
  [DRIFT.md](DRIFT.md)).

**What these are not.** They are data checks. They never need, and never look at, a model's
predictions, so they are not a model fairness audit: equalized odds, calibration by group and
bias mitigation are out of scope. Shape does not infer sensitive attributes either: the slices
are the columns you name.

## Sliced scorecards

```bash
shape scorecard out/ --schema gates.json --slice-by region
shape scorecard out/ --schema gates.json --slice-by region,tier --min-slice-rows 50
shape scorecard out/ --schema gates.json --slice-by region --max-slice-gap 10   # exit 1 over 10
shape scorecard out/ --schema gates.json --slice-by region --reference population.csv --label churned
```

A *slice* is each distinct value, or value combination, of the slice columns, in each table that
holds all of them (other tables are listed under `skipped_tables`; if no table holds them the
command exits 2). Null is its own slice, `(null)`. With several columns a label reads
`region=north, tier=gold`.

### Dimension scores, gap and worst slice

Every dimension is scored per slice with the same row-level checks and formula as the whole-table
score ([SCORECARD.md](SCORECARD.md)): each check is computed on the slice's rows, a dimension is
the mean of its checks, and checks hidden as known issues stay hidden. For each dimension the
scorecard reports the slice scores, the **gap** (highest minus lowest slice score) and the
**worst slice**. Only checks that have a row-level form (nulls, repeated keys, orphan foreign
keys, ranges, dates) have a per-slice score; a dimension with none on that table has no gap
(`null`). A foreign-key check of a sliced child table looks up the whole parent table.

`--max-slice-gap G` exits 1 when any dimension's gap is more than `G` (a gap equal to `G` is
fine) and lists the offenders under `slices.exceeded`. Without it the scorecard exits 0 as it
always did.

### The small-slice rule

A slice with fewer than `--min-slice-rows` rows (default 30) is **never shown alone**: a handful of
rows says little, and a score or rate over a few records can identify them.

- Two or more small slices are pooled into one slice, `(small slices)`, with its own scores.
- One small slice has nothing to be pooled with, so it is left out; the table's `small_slices`
  records only that one slice of that many rows was left out.
- Exactly `--min-slice-rows` rows is enough to be shown.
- The pooled slice is left out of the disparity ratio below.

### Safe by default

When a slice column is classified (named with `--classified TABLE.COLUMN`, named like personal
data, or holding values that look like it, as for samples in [SCORECARD.md](SCORECARD.md)), the
slices are labelled `slice 1`, `slice 2`, ... (largest first) instead of by value, so the scorecard
does not leak the values. `--show-classified` shows them.

### Representation

`--slice-by` also reports each slice's **share** of the table's rows. With `--reference REF`, a
data file or folder, or a profile (a `.shape` artifact or a profile-engine JSON document) of the
population the data should describe, it adds the **reference share** and the **ratio**
(share over reference share; below 1 means under-represented). Notes:

- A reference profile gives category shares for one slice column only, and only when the profile
  has them (a column of few distinct values); otherwise the command exits 2.
- A value the reference lacks has reference share 0 and no ratio.
- A value in the reference but absent from the data is listed under `missing_from_data` (up to
  the 20 largest): the most under-represented group of all is one that is not there.
- The ratio is reported, not flagged: what counts as under-represented depends on the population.

### Outcome rates

`--label COLUMN` (boolean, or a column with exactly two values) reports each slice's **positive
rate** and the **disparity ratio**: the lowest slice rate over the highest. It is flagged when
below **0.8**, the four-fifths rule. That rule is a screening heuristic from employment
selection, not a legal test: a flag is a reason to look, not a finding. For a boolean the
positive value is `true`; for two numbers, the larger; for two words, the one of `true`, `yes`,
`y`, `t`, `1`, `positive` (the other must be `false`, `no`, `n`, `f`, `0`, `negative`); any other
pair exits 2 and asks you to recode the column. Nulls are left out of a rate. When every rate is
0 there is no ratio.

### Null rates

For every column other than the slice columns, each slice's null rate (nulls and NaN) is
reported next to the table's, and **flagged when a slice's rate is more than 0.1 above the
table's** (exactly 0.1 above is not flagged). The flags are under `null_rate_flags`.

### Trends and the format

`--history DIR --name NAME` (and `--record`) compare each slice gap with the last stored scorecard:
`widening`, `narrowing`, `steady` or `no data` (under `slices.trend`, keyed `table.dimension`).

A scorecard with a `slices` object is written as `"format": "shape-scorecard", "version": 2`; a
scorecard without one is still `version: 1`, byte for byte. The JSON Schemas are
`src/shape/schemas/scorecard-v1.schema.json` and `scorecard-v2.schema.json`.

```python
from shape.quality.scorecard import build_scorecard

card = build_scorecard(result, tables, schema=schema, slice_by=["region"],
                       label="churned", reference=population_tables, max_slice_gap=10)
card.to_dict()["slices"]["tables"]["customer"]["dimensions"]["completeness"]["worst_slice"]
```

## Training-serving skew: `shape skew`

```bash
shape skew train/ serving/                                     # Markdown
shape skew train.parquet serving.parquet --features x,price,country --label churned
shape skew train/ serving/ --slice-by country -o skew.json     # JSON report; exit 1 if flagged
shape skew train.shape serving.shape --json                    # two profiles
shape skew train/ serving/ --threshold psi=0.25 --threshold null_rate=0.02
shape skew train/ serving/ --project shape.yml --source orders
```

`TRAIN` and `SERVING` are data (a file, or a folder with one file per table) or profiles. A
single table on each side is paired whatever its name; with several, tables are paired by name
and a training table with no serving table is flagged (`missing_tables`). Exit **0** when
nothing is flagged, **1** when a feature, a slice's feature or a table is flagged, **2** for
unusable input.

### What is measured for each feature

Features are the training columns (`--features` limits them; the `--label` column is left out).

| Measure | What it shows | Flagged when |
|---|---|---|
| schema | the column is missing in serving, or its type class changed (integer, float, boolean, string, datetime) | status is not `ok` |
| null rate | training and serving null rate (nulls and NaN) and the difference | the difference, in either direction, is more than `null_rate` (default 0.05) |
| PSI | the population stability index, exactly that of `shape drift --psi` (10 equal-width bins for numbers and dates, category shares for text of at most 50 distinct values) | PSI is `psi` or more (default **0.2**, the documented drift threshold) |
| unseen category share | the share of serving rows (with a value) whose text value never occurs in training; not computed when training has more than 50 distinct values | the share is more than `unseen_category_share` (default 0.01) |
| out-of-range share | the share of serving values (numbers and dates) below the training minimum or above the training maximum | the share is more than `out_of_range_share` (default 0.01) |

Features are ranked by PSI, largest first; a feature with no PSI (an identifier-like column, a
type change, fewer than 10 values) comes last and `notes` says why. A column only in serving is
listed under `extra_in_serving` and is not flagged. The label's training and serving positive
rates and their difference are reported (`label`); the label is flagged only when you set
`label_rate_diff` (a serving set often has no labels yet, which is reported and not flagged).

With `--slice-by COL` every measure above is repeated for each value of `COL`, and a flagged
feature in any slice flags the report. Slices follow the same rules as the scorecard's: null is
`(null)`, a classified column is labelled `slice 1`, `slice 2`, ... (skew has no flag to show it),
and a slice needs at least `min_slice_rows` rows (default 30) on **both** sides to be shown;
smaller ones are pooled as `(small slices)` when there are two or more, and left out when there
is one.

### Profiles

A profile holds no values, so against a profile only the schema and null-rate measures are
computed; PSI, unseen categories and out-of-range are `null` with a note, and `--slice-by` exits
2. Data against a profile works the same way.

### Thresholds

Set with `--threshold KEY=VALUE` (repeatable) or in the project file. Keys: `psi`, `null_rate`,
`unseen_category_share`, `out_of_range_share`, `label_rate_diff` (no default), `min_slice_rows`.
`--project FILE|DIR` reads `sources.NAME.thresholds` of `shape.yml` (`--source NAME`, or the only
source; with several sources and none chosen a note is printed and the file's thresholds are not
applied); other keys in that mapping (the drift thresholds of [DRIFT.md](DRIFT.md)) are ignored.
Flags override the file. The thresholds used are in the report. Only `psi` has a documented
meaning outside this command; the other defaults are screening values to tune for your data.

### The report

`-o REPORT.json` writes the JSON report; `--json` prints it instead of Markdown. It declares
`"format": "shape-skew-report"` and `"version": 1`; the JSON Schema is
`src/shape/schemas/skew-report-v1.schema.json`.

```python
from shape.quality import skew

report = skew("train/", "serving/", label="churned", slice_by="country",
              thresholds={"psi": 0.25})
report.flagged            # True when anything is flagged
report.to_dict()          # the shape-skew-report document
print(report.to_markdown())
```

## Limits

- Everything here is computed from the data you give it. A score or rate over few rows is noisy:
  the small-slice rule is a floor, not a guarantee that a difference is real.
- A slice column that is a proxy for something else is still just a column; Shape does not tell
  you which slices to look at.
- The disparity ratio and its 0.8 line are a screening heuristic and say nothing about a model's
  decisions.
- PSI on 10 bins and the shares above are summaries; they cannot see a change in how features
  relate to each other (see the joint checks of [JOINT.md](JOINT.md)) or to the label.
- Skew is a batch comparison of two datasets, not online or streaming monitoring.
