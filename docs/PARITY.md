# Environment parity: `shape parity`

After a development or test environment is wiped and reloaded, can anyone show that it still
looks like production? `shape parity A B` answers by **shape only**: it never compares rows or
values, so side A can be a share-safe profile that production hands over.

```
shape parity A B [--dataset] [--source NAME] [--project shape.yml] [--no-project]
                 [--scaled] [--row-tolerance F] [--tables T,...] [-o REPORT.json] [--json]
```

`A` is the reference (production), `B` the environment under test. Each is one of:

| Input | Kind in the report |
|---|---|
| data: a file, or a folder (`--dataset`: one table per file, as for `shape profile`) | `data` |
| a `.shape` profile (`shape profile`) | `profile` |
| a profile export (`shape profile export`) | `profile-export` |
| a share-safe profile (`shape profile safe`, see [PRIVACY_MODEL.md](PRIVACY_MODEL.md)) | `safe-profile` |

Two sides may be of different kinds. Two one-table sides are compared whatever the files are
called (`prod_orders.csv` against `dev_orders.csv`); otherwise tables pair by name.

## What is checked

| Category | A check per | Passes when |
|---|---|---|
| `tables` | table of either side | the table is on both sides |
| `columns` | column of a table on both sides | the column is on both sides |
| `types` | column on both sides | the types are equal |
| `keys` | table with a primary key detected on A | B detects the same key |
| `relationships` | relationship (foreign key) detected on A | B detects the same one (same child columns and parent) |
| `nulls` | column on both sides | the null rates differ by at most the `null_rate` threshold (default 0.05, absolute) |
| `distributions` | column on both sides | `shape diff`'s comparison finds no change, see below |
| `row_counts` | table on both sides | the row count is within `--row-tolerance` (default 0.1) |

The thresholds are the drift thresholds of [DRIFT.md](DRIFT.md). With `--source NAME` (or the
only source, or the source named like a profile) they come from `shape.yml`
([PROJECT.md](PROJECT.md)): the source's `thresholds`, per-column `thresholds` and `ignore`
lists. Ignored columns are left out of every column check. The column `owner` of `shape.yml` is
named on each check about that column. `shape.yml` is read, never extended: parity adds no key.

**Row counts.** By default, `|rows B - rows A| / rows A` must be at most the tolerance (an empty
A passes only an empty B). With `--scaled`, each table's *share* of the total row count is
compared instead, as an absolute difference of shares: a smaller environment with the same
proportions passes, one whose orders are half as large relative to its customers does not.
`--tables` restricts the comparison to the named tables (a name found on neither side exits 2).

**Distributions.** Between two samples of one distribution parity is expected, so the comparison
is the drift engine's: mean and spread for numbers, the distribution itself (KS distance), the
category mix, the share of true values, hour-of-day and day-of-week mixes, string length and
pattern. Differences from `shape diff`, each because it would flag a healthy environment:

- the fitted family's *name* flipping (`distribution_change`) is not a failure; its size is
  `distribution_shift`;
- an unseen category counts only when it carries more than the `category_tvd` share of the rows;
- a column whose values are ids (a key, a reference to one, or dense integers running up to the
  size of a table) ignores `new_categorical_values`: a new load brings new ids;
- with `--scaled`, a distribution check of such an id column is `not measured`, and distinct
  counts, new categories and extremes are ignored: a bigger sample sees more of them.

## `not measured`

A check the inputs cannot support is `not_measured`, never a pass, and does not fail parity:

- a safe profile has no top values and replaces numeric categories by a coarse histogram, so a
  column without quantiles, moments or categories on either side has no distribution to compare;
- a null rate unknown on a side (a table with no rows read);
- a side that records no keys or relationships;
- a column whose types differ (see `types`), and, with `--scaled`, id columns.

Categories of a safe profile (small ones folded into `__OTHER__`, odd ones hashed) are compared
with the other side's after the same folding and hashing.

## The report

`-o REPORT.json` writes it, `--json` prints it instead of the text. `shape-parity-report`
version 1 (JSON Schema: `src/shape/schemas/shape-parity-report-v1.schema.json`):

```json
{
  "format": "shape-parity-report", "version": 1,
  "inputs": {"a": {"kind": "safe-profile", "content_id": "9f2c...", "name": "prod", "path": "prod.safe.json", "tables": 9},
             "b": {"kind": "data", "content_id": "41ab...", "name": "dev", "path": "dev/", "tables": 9}},
  "options": {"scaled": true, "row_tolerance": 0.1, "tables": null, "source": "orders", "project": "shape.yml"},
  "summary": {"pass": 255, "fail": 1, "not_measured": 20, "by_category": {"tables": {"pass": 9, "fail": 0, "not_measured": 0}}},
  "checks": [
    {"category": "columns", "status": "fail", "table": "orders", "column": "note",
     "details": {"reason": "missing on B"}, "owner": "sales-data@example.com"}
  ],
  "parity": false
}
```

`content_id` identifies the input: the id `shape profile` gives the profile (for data, the id of
the profile it would write), or a hash of the JSON document. `parity` is true when nothing
failed. The text form lists failures first, then what could not be measured, then a count.

| Exit | Meaning |
|---|---|
| 0 | parity |
| 1 | a check failed |
| 2 | unusable input: a missing or unreadable side, JSON that is not a profile, a folder that is not one table without `--dataset`, an unknown `--source` or `--tables` name |

## Example: production hands over a safe profile

```bash
# in production
shape profile data/ --dataset -o prod.shape
shape profile safe prod.shape -o prod.safe.json

# in development, after a reload
shape parity prod.safe.json dev-data/ --dataset --scaled --source orders -o parity.json
```

Persisted-format compatibility: `tests/fixtures/parity/v1/report.json` is a frozen version 1
report that later Shapes must still read (`tests/parity/test_compat.py`).
