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

## Targeted corruptions and the ground-truth log (`shape chaos`)

The categories below are randomised: a scheduler decides what fires and each mutator picks its
own rows. To corrupt a table in a known way and score a quality check against the answer, use named
corruptions, each with a rate, and the **ground-truth log** they write.

```
shape chaos retail --scale small --seed 7 -o corrupted/ \
    --corrupt duplicates=0.02@order \
    --corrupt orphan_keys=0.01@order.customer_id \
    --corrupt date_shift=0.03@order.order_date:days=14 \
    --corrupt negative_amounts=0.02@order.order_total \
    --corrupt case_whitespace=0.05@order.status \
    --corrupt pii_fill=0.05@customer.email \
    --corrupt type_change=1@order.shipping_address_id \
    --corrupt null_creep=0.02@order.promotion_id:step=0.01
```

writes the corrupted tables to `corrupted/` and the log to `corrupted/_chaos_ground_truth.jsonl`.
The tables are generated, or read from `--input DIR` (the files `shape generate` writes); the same
landing options as `shape generate` (`--path-template`, `--batch-date`, `--table-format`, see
[LANDING.md](LANDING.md)) write them in a dated layout, and then the log is
`_chaos_ground_truth_YYYYMMDD.jsonl`. `--start-date D0 --batch-date D` sets the batch number to the
days between them (or give `--batch N`), which is what a daily job does.

A corruption is `KIND[=RATE][@TABLE[.COLUMN]][:OPT=V,...]`. `RATE` is the share of the table's rows
changed, exactly `round(rows x rate)` (fewer when fewer rows can change). Without `@TABLE` it
applies to every table it fits; without a column, to the columns it picks itself.

| Kind | What it models | Column when not named | Options |
|---|---|---|---|
| `duplicates` | rows delivered twice (at-least-once delivery): copies are appended at the end | the table | `fuzz` (0 to 1, default none: the chance that a text, number or date cell of a copy is damaged, which makes near-duplicates; keys are left alone) |
| `orphan_keys` | foreign keys that match no parent row | the table's foreign keys | |
| `date_shift` | late-arriving or wrongly dated rows | every date and timestamp column | `days` (up to, default 7), `direction` (`both`, `late`, `early`) |
| `negative_amounts` | sign flips of positive amounts | number columns that are not keys | |
| `case_whitespace` | inconsistent categories: upper, lower, leading or trailing blanks | text columns with at most 50 distinct values | |
| `pii_fill` | a free-text column filled with SSN-format values (area 9xx, never issued) | needs a column | `pii` (`ssn`, `email`, `phone`) |
| `type_change` | a number or date column delivered as text | needs a column | |
| `null_creep` | a null rate that ramps up from batch to batch | needs a column | `step` (added to the rate per batch) |

Every corruption also takes `from` and `to`, the first and last batch in which it is active
(`type_change=1@order.amount:from=5,to=7` is a few days of numbers as strings). A corruption that
cannot apply to what it is aimed at (a column that is not there, text where a date is needed) is an
error, not a silent no-op.

**The log** is JSON Lines. The first line is the run (`record: "run"`: the seed, the batch, the
corruptions, rows in and out per table). Every other line is one change (`record: "change"`):

| Field | Meaning |
|---|---|
| `table`, `kind`, `seed`, `batch` | what, where and with which seed |
| `scope` | `row` (one cell) or `column` (`type_change`: the whole column) |
| `row` | the position of the row in the **output** table, 0-based. Rows are never reordered or removed; duplicates are appended |
| `key` | the row's key (the schema's primary key, else the first column), so a check that reorders can still join |
| `column`, `before`, `after` | the cell before and after (dates as ISO text); for `type_change` the type names |
| `source_row` | duplicates: the row copied |
| `fuzz` | duplicates with the `fuzz` option: the chance a cell of the copy was damaged (absent otherwise) |
| `days`, `pii`, `rate` | the shift of a date, the PII kind, the null rate of that batch |

The log is complete: a cell that is not in it is untouched. To score a data-quality check, compare
the rows it flags with the `row` (or `key`) of the changes of one `kind`: precision is the share of
flagged rows that are in the log, recall the share of the log that is flagged.

**Determinism.** The same seed gives the same tables and the same log bytes. Each corruption has its
own random stream, keyed by the seed, the batch, its kind, table and column, so adding a corruption
of another kind does not change the others. Python:

```python
from shape.chaos.groundtruth import Corruption, corrupt_tables, write_ground_truth

outcome = corrupt_tables(tables, [Corruption.parse("duplicates=0.02@order")], seed=7, batch=0)
outcome.tables, outcome.records           # the corrupted tables and the change records
write_ground_truth("ground_truth.jsonl", outcome)
```

**True duplicate clusters.** `duplicate_clusters(records)` returns, per table, the clusters of
duplicates of one run: an original row with all its copies (a copy of a copy joins the same
cluster), as sorted output row positions. It reads `outcome.records` or the records of a log read
back with `read_ground_truth`, so a log file is enough to score a deduplication. Adding the
`fuzz` option, or the clusters, does not change the tables or the log of a run that does not use
`fuzz`. See [RESOLVE.md](RESOLVE.md) for scoring entity resolution against them.

These corruptions are separate from the six categories: the categories keep their own scheduler
and random order, and the targeted form has an exact rate and a log.

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

### As plugins

The six mutators are registered built-ins of the `shape.chaos` plugin group (`schema`, `value`,
`file`, `referential`, `temporal`, `volume`). A plugin's `mutate(batch, seed)` takes one
`RecordBatch` and returns a batch and a `ChaosReport`, so it uses the category defaults
(`moderate`, a day past the breaking-change day). `ChaosReport.rows_affected` is the sum of the
rows of the mutations performed. `schema`, `value` (`wrong_types`) and `volume` change the
schema or the row count by design, `file` returns the corrupted bytes of the batch's CSV
rendering as a single `payload` column, and `referential` sees one table, so only duplicate keys
can fire (call `ReferentialChaosMutator` with a dict of tables for orphan keys).
