# Incremental data: `shape continue` and `shape time-travel`

Two commands that extend a dataset over time, for testing change-data-capture, slowly changing
dimensions, incremental loads and point-in-time reporting.

- `shape continue` takes data that already exists and writes the **next batch of changes**: new rows,
  changed rows and soft-deleted rows.
- `shape time-travel` generates a dataset and **evolves it month by month**, keeping a snapshot of
  every month: growth, seasonality, churn and updates.

Both are reproducible: the same data and the same seed give the same output.

## `shape continue`

```
shape generate retail --scale small --format parquet -o data/
shape continue retail --input data/ -o delta/ --format parquet --inserts 200 --seed 7
```

`--input DIR` holds one CSV, Parquet or JSON Lines file per table (the files `shape generate` writes).
The first argument is the domain or generation schema the data came from: it names the primary keys
and the relationships. For each table the delta contains

| Kind | Rows | Notes |
|---|---|---|
| `INSERT` | `--inserts N` (default 100) | clones of existing rows with fresh keys and slightly changed values |
| `UPDATE` | `--update-fraction F` of the existing rows (default 0.1) | the same key, about 30% of the other columns changed |
| `DELETE` | `--delete-fraction F` of the existing rows (default 0.02) | exact copies of existing rows, to be marked deleted |

Every row is tagged with `_shape_delta_type` (`INSERT`, `UPDATE` or `DELETE`) and
`_shape_delta_timestamp`, and each table's delta is written to `OUT/<table>.<format>` (formats `csv`,
`parquet`, `jsonl`; a table with no change gets no file). Column names, order and types are the input's.

What the rows look like:

- **Keys.** An integer primary key continues above the existing maximum. A table whose primary key has
  no integer column cannot get new keys, and the command stops with an error rather than repeat keys.
- **Foreign keys.** An inserted row's foreign key is drawn from the parent's keys (existing ones and
  the parent's new ones), never from a parent row the same delta deletes. An update never changes a
  key or a foreign key.
- **Values.** Numbers move by a factor between 0.9 and 1.1 (integers are rounded), dates by 1 to 30
  days, and other columns (text, categories) are shuffled among the chosen rows. Booleans stay as
  they are; nulls stay null.
- **Selection.** No row is both updated and deleted in one delta. A fraction of 0 changes nothing; any
  other fraction changes at least one row of each non-empty table.
- **State changes.** `--transitions FILE.json` gives, per column, how a changed row moves between
  states: `{"order.status": {"processing": {"shipped": 0.7, "cancelled": 0.3}}}`. A row moves one
  step; a state with no entry stays as it is. A table or column that does not exist, or weights that
  are negative or all zero, are errors.
- **Time.** `--as-of ISO` stamps every row with that time (default: now, UTC). `--seed` defaults to the
  schema's seed.

A `DELETE` row is a soft delete. Deleting a parent does not delete its children: if you apply the
delta with hard deletes, children of a deleted parent are left without one.

Python:

```python
from shape.generation.incremental import ContinueConfig, ContinueEngine

delta = ContinueEngine().continue_from(tables, schema, ContinueConfig(insert_count=200, seed=7))
delta.inserts["order"], delta.updates["order"], delta.deletes["order"], delta.combined["order"]
```

`tables` is a `GenerationResult` or a dict of Arrow tables. One engine keeps its key high-water marks, so
continuing the same snapshot twice with it issues disjoint keys.

## `shape time-travel`

```
shape time-travel retail --scale small --months 12 --seasonality 11=1.5,12=2.0 -o snapshots/
```

Month 0 is the domain generated at `--scale` with `--seed` (default 42). Each of the next `--months`
months (default 12), for every table with a key:

1. **Growth.** New rows, cloned from existing ones with fresh keys: `growth-rate` (default 0.05) times
   that month's `--seasonality` multiplier (`MONTH=MULTIPLIER,...`, calendar months 1 to 12, default
   1.0) of the table's rows, at least one when the rate is above 0 and none when it is 0.
2. **Churn.** `--churn-rate` (default 0.02) of the rows are removed.
3. **Updates.** `--update-fraction` (default 0.1) of the remaining rows have one number changed by a
   factor between 0.9 and 1.1: the first numeric column that is neither a key nor an `*_id` column
   (integers are rounded).

After every month, a child row whose parent was removed is pointed at a surviving parent, chosen in
proportion to the children it already has, so a snapshot never has a foreign key without a parent and
the skew of the relationship (a few popular products, many quiet ones) is kept. Row counts do not
depend on this repair.

Snapshot `i` is written to `OUT/month_i/<table>.<format>` (`parquet` default, or `csv`) and is dated
`--start-date` (default 2023-01-01) plus `i` calendar months. The number of rows per month is
deterministic given the rates: `rows(m) = rows(m-1) - int(rows(m-1) x churn) + max(1, int(rows(m-1) x
growth x seasonality(m)))`.

Python:

```python
from shape.generation.incremental import TimeTravelConfig, TimeTravelEngine

engine = TimeTravelEngine()
result = engine.generate(schema, TimeTravelConfig(months=6, seasonality={12: 2.0}), scale="small")
# or evolve data you already have: engine.generate_from(tables, config, schema=schema)
result.snapshots[3].tables["order"], result.to_partitioned_tables()
```

## Limits

- The dates inside cloned rows are not advanced: a row added in month 6 carries the dates of the row
  it was cloned from. Row counts, keys and relationships evolve; the calendar inside the data does not.
- Cloned values are resampled from existing rows, so the data stays inside the original value set but
  adds no new values.
- Parents with no children stay without children: a new row copies an existing row's foreign key.
