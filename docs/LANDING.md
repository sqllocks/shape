# Landing layout: one file per table per business date

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" LANDING
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for LANDING
    ```


A source system drops files: one per table per day, in a folder named for the date, each table in
its own format. `shape generate`, `shape continue` and `shape chaos` write that layout with three
options, and the file sinks take the same two path options.

```
landing/orders/ingest_date=2026-08-04/orders_20260804.parquet
landing/customers/ingest_date=2026-08-04/customers_20260804.csv
```

## How-to

[Run this example](#local-example-1).


writes `landing/customer/ingest_date=2026-08-04/customer_20260804.csv` and
`landing/order/ingest_date=2026-08-04/order_20260804.parquet` (and one file for every other table,
in `--format`).

| Option | Meaning |
|---|---|
| `--path-template T` | where each table's file goes under `-o`. Default `{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}` |
| `--batch-date YYYY-MM-DD` | the business date the date tokens take. It is never read from the clock, so a run is reproducible; a template with a date token and no `--batch-date` is an error |
| `--table-format TABLE=FORMAT` | the format of one table (repeatable); the other tables use `--format`. A table name that does not exist is an error |

Tokens: `{table}`, `{ext}` (the format's extension), `{date}` (`2026-08-04`), `{yyyymmdd}`, `{yyyy}`,
`{mm}`, `{dd}`; for rolling writers (`--roll-rows`, `--roll-seconds`, `shape stream`) also `{part}`
(the file's number in its table, five digits) and `{hhmmss}` (the time the file was opened, UTC).
A template must contain `{table}`, is relative to `-o` and cannot leave it. Other
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

- **Cloud targets** (ADLS Gen2, OneLake Files) are not written by the local file sinks above:
  `--to abfss://...` takes the same `--path-template`, `--batch-date` and `--table-format` and
  writes the layout there, publishing each file whole (`docs/SINKS.md`).
- Tables are written whole (a landing run generates them first), not streamed chunk by chunk.
- One date per run, one file per table: late arrivals, repeated drops, `_done` flags and manifests
  over a date range belong to scenario packs (`docs/SCENARIO_PACKS.md`).


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
shape generate retail --scale small --format parquet --table-format customer=csv \
    --batch-date 2026-08-04 -o landing/
```

??? info "Output (exit 0)"

    ```text {.expected}
    Landed 9 files under landing/: 21,750 rows in 9 tables (0.46s)
    ```
