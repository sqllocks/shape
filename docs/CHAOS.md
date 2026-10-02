# Chaos: deterministic data-quality fault injection

`shape.chaos` injects the faults real pipelines meet, on purpose and repeatably, into generated
data. It has six categories: **schema**, **value**, **file**, **referential**, **temporal** and
**volume**. A seeded engine decides when each fires; mutators decide what changes.

## Stable entry point: row-level anomalies (`--anomaly-fraction`)

```python
from shape.chaos import inject_anomalies

result = inject_anomalies(batch, fraction=0.02, seed=batch_seed, protect=["_shape_table", "_shape_seq"])
result.batch     # same schema and row count as the input; about 2% of the rows changed
result.rows      # positions of the rows that changed, ascending
result.kinds     # the anomaly kind of each changed row, aligned with rows
result.columns   # the column each change hit, aligned with rows
result.report    # a plugin-API ChaosReport (mutator="anomalies", rows_affected, details)
```

`inject_anomalies(batch: pa.RecordBatch, *, fraction: float, seed: int,
kinds: Sequence[str] | None = None, protect: Sequence[str] = ()) -> AnomalyResult`

- **Schema and row count are preserved**, so the batch can be emitted as it is.
- **`fraction`** is in [0, 1]. The number of rows targeted is `n * fraction`, rounded
  stochastically, so the expected rate is exact for any batch size, including batches smaller
  than `1 / fraction`.
- **Kinds** (`shape.chaos.KINDS`), one per changed row, chosen uniformly among the kinds the
  batch can take:

  | Kind | Change | Applies to |
  |---|---|---|
  | `null` | the cell becomes null | nullable fields |
  | `out_of_range` | 100 to 1000 times the column's largest magnitude (integers are clipped) | integer and float columns |
  | `negative` | the sign flips (zero is unchanged) | signed integer and float columns |
  | `future_date` | a date between 2031 and 2039 | timestamp and date columns |
  | `encoding` | a leading byte-order mark or a trailing Latin-1 character | string columns |

- **`protect`** names columns that are never touched. Pass the stream's key and event-time
  columns so that the idempotency key stays valid.
- **Determinism:** the output depends only on the batch, `fraction`, `seed` and `kinds`. Derive
  one seed per batch (from the run seed and the batch sequence number) and a replay, or a restart
  after a crash, produces the same anomalies.
- A row whose chosen cell cannot change (a null being negated, a null text) is left as it was and
  is not listed, so `len(result.rows)` is the number of rows actually changed.
- The input batch is never modified.

This function is the only API a stream emitter needs. Nothing else in `shape.chaos` is required
for `--anomaly-fraction`.

## The six categories

Each mutator is a class in `shape.chaos.categories` with
`apply(data, day, rng, intensity) -> (data, [MutationEvent])`. They work on Arrow tables
(`pa.Table`), raw bytes (file chaos) or a dict of tables (referential chaos), and never modify
their input. A *number* column is any integer or float column, a *text* column any string column,
and a *datetime* column any timestamp column without a time zone.

| Category | Mutations | How many per call |
|---|---|---|
| schema | `add_column`, `reorder`; from `breaking_change_day` also `drop_column`, `rename_column`, `retype_column` | 1 below intensity 2.0, else 2 |
| value | `inject_nulls`, `out_of_range`, `wrong_types`, `encoding_issues`, `future_dates`, `negative_amounts` | 1 to 3 |
| file | `truncate`, `corrupt_encoding`, `partial_write`, `zero_byte`, `garbage_header`, `wrong_delimiter`, `invalid_json_poison`, `bom_injection` | exactly 1 |
| referential | `orphan_fks`, `duplicate_pks` | exactly 1 |
| temporal | `late_arrivals`, `out_of_order`, `timezone_mismatch`, `dst_boundary` | 1 or 2 |
| volume | `spike` (30%), `empty` (30%), `single_row` (40%) | exactly 1 |

The share of rows a sub-mutation changes is a base fraction times the intensity multiplier
(`calm` 0.25, `moderate` 1, `stormy` 2.5, `hurricane` 5), capped at 0.8: nulls 5%, out-of-range
3%, junk text 2%, encoding 3%, future dates 3%, negated amounts 3%, orphan keys 5%, duplicate
keys 3%, late arrivals 5%, swapped timestamps 3% (as pairs), timezone shifts 5% (cap 50%),
daylight-saving values 2% (cap 30%). At least one row is always changed.

Differences that follow from Arrow's strict column types (a column has one type):

- `wrong_types` and `retype_column` turn the column into text: the other cells are rendered
  as the number's text.
- `out_of_range` and `negative_amounts` give a float64 column. Nulls stay null in integer columns.
- `orphan_fks` keeps an integer column's type when the orphan ids fit it.

### Scheduling

```python
from shape.chaos import ChaosConfig, ChaosEngine

engine = ChaosEngine(ChaosConfig(enabled=True, intensity="stormy", seed=99))
if engine.should_inject(day=12, category="value"):
    table = engine.corrupt_values(table, day=12)
result = engine.apply_all(table, day=12, tables=tables)   # every category that fires
```

`should_inject` is false when chaos is off, before `chaos_start_day`, or when the category is
off; an override for that day and category always fires; otherwise one draw is compared with
`weight * intensity * escalation` (capped at 1). Escalation is `gradual` (linear from 0 to 1 over
30 chaos days), `random` or `front-loaded` (1 decaying by 5% a day, floor 0.1).

`apply_all` runs schema, value, temporal, volume then referential. Its `ChaosResult` carries the
mutated `table`, the mutated `tables` (the referential result) and every event.
