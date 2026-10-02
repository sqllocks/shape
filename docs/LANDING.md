# Landing layout: one file per table per business date

A source system drops files: one per table per day, in a folder named for the date, each table in
its own format. `shape generate`, `shape continue` and `shape chaos` write that layout with three
options, and the file sinks take the same two path options.

```
landing/orders/ingest_date=2026-08-04/orders_20260804.parquet
landing/customers/ingest_date=2026-08-04/customers_20260804.csv
```

## How-to

```
shape generate retail --scale small --format parquet --table-format customer=csv \
    --batch-date 2026-08-04 -o landing/
```

writes `landing/customer/ingest_date=2026-08-04/customer_20260804.csv` and
`landing/order/ingest_date=2026-08-04/order_20260804.parquet` (and one file for every other table,
in `--format`).

| Option | Meaning |
|---|---|
| `--path-template T` | where each table's file goes under `-o`. Default `{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}` |
| `--batch-date YYYY-MM-DD` | the business date the date tokens take. It is never read from the clock, so a run is reproducible; a template with a date token and no `--batch-date` is an error |
| `--table-format TABLE=FORMAT` | the format of one table (repeatable); the other tables use `--format`. A table name that does not exist is an error |

Tokens: `{table}`, `{ext}` (the format's extension), `{date}` (`2026-08-04`), `{yyyymmdd}`, `{yyyy}`,
`{mm}`, `{dd}`. A template must contain `{table}`, is relative to `-o` and cannot leave it. Other
layouts are only a template away: `{table}/{yyyy}/{mm}/{dd}/part-000000.{ext}` is a
Hive-like date tree, and `table={table}/date={date}/part-000000.{ext}` is a Hive partition path.

Formats: `csv`, `tsv`, `jsonl`, `parquet`, `excel`, `sql` (`delta` is a directory of files, not one
file, so it has no place in this layout). The bytes of a file are the bytes the plain writer
produces: only the path differs.

One batch per run: a daily job passes today's date; a backfill passes a range
(`shape continue ... --batch-date D --end-date E`, see `docs/INCREMENTAL.md`).

## In Python

```python
from shape.generation.landing import write_landing

write_landing(tables, "landing/", default_format="parquet", batch_date="2026-08-04",
              formats={"customer": "csv"})            # -> list of LandedFile(table, format, path, rows)
```

or through a sink directly, which treats the URI as the landing root:

```python
from shape.builtins.sinks import ParquetSink

ParquetSink().write("landing/", "orders", batches,
                    path_template="{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}",
                    batch_date="2026-08-04")
```

## What this does not do

- **Cloud targets** (ADLS Gen2, OneLake paths) are not written by these sinks: the layout is
  local. Point a landing run at a mounted or synchronised path, or copy the folder.
- Tables are written whole (a landing run generates them first), not streamed chunk by chunk.
- One date per run, one file per table: late arrivals, repeated drops, `_done` flags and manifests
  over a date range belong to scenario packs (`docs/SCENARIO_PACKS.md`).
