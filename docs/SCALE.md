# Generating at scale

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


`shape generate DOMAIN --scale-mode MODE` runs a generation through the **scale router**, into one
or more **sinks**, as a **job** that can be inspected, cancelled and resumed.

| mode | what it does |
|---|---|
| `local_single` | generation on one thread; the reference for what a run produces |
| `local_mp` | generation on every core (`--max-workers N` or `SHAPE_THREADS` limits them); the output is the same as `local_single`'s |
| `fabric_spark` | submits the run to a Fabric Spark notebook that writes Delta tables to a Lakehouse |

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

Row counts are exact in every mode: a table has the rows of the scale preset, and every foreign key
points at a key that exists in the full parent table, whichever chunk it is in. Seed for seed, the
two local modes write the same rows.

## Skew rehearsal

A scale test with uniform keys misses the hot keys production has. `shape skew-rehearsal` generates
a schema at scale with the key skew a profile measured, and says whether the generated data kept it:

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

`SCHEMA` is a domain or generation schema file, `S` one of its scale presets (local generation; Spark
and Fabric modes are not offered). The tables are written to `DIR` as Parquet files, with
`DIR/skew_report.json`.

**Which columns.** With `--columns`, those: each must be a `foreign_key` column of the schema and
have frequency data in the profile, or the command exits 2. Without it, every `foreign_key` column
of the schema that the profile has frequency data for; the others are listed under `skipped` in the
report with the reason (no frequency data, or `sample_rate` / `constrained_by`, which draw keys
without the fan-out).

**Concentration.** From the profile column's distinct count `c` and its most frequent values and
their frequencies, the top is `k = min(listed, max(1, round(0.2 * c)))` keys (a fraction of `k / c`,
0.2 when the profile lists enough values) and the profile's top share is their summed frequency. The
generated column is drawn with `fan_out` (`shape.generation.fanout.concentration_weights`) so that
the same fraction of its parents holds the same share, whatever the scale (power shape; `shape` in
the report).

**The report** (`shape-skew-report`, version 1) states, per column, the parent count, the row count,
the top fraction, the profile's top share, the generated top share
(`shape.generation.fanout.top_share_of`), their absolute difference, and whether it is within the
tolerance, which the report states (default 0.02, `--tolerance`, between 0 and 1).

| exit | meaning |
|---|---|
| 0 | every column is within tolerance |
| 1 | a column is outside it |
| 2 | the profile has no frequency data for a requested column, no column qualifies, or other bad input |

The generated share is measured on the realized counts, so a nearly flat column with few rows per
key reads higher than the fraction (the heaviest 20% of random counts hold more than 20%): allow
for it with `--tolerance`, or rehearse at a scale with more rows per key.

## Sinks

`--sink NAME` (repeatable) with settings as `--sink-config NAME.KEY=VALUE`. With only `-o DIR` the
sink is `parquet`; with nothing, `memory`.

| sink | settings | writes |
|---|---|---|
| `memory` | `max_memory_gb` | keeps the tables in memory (library use: `MemorySink.result()`) |
| `parquet` | `output_dir`, `chunk_rows`, `writer_threads` | `<dir>/<table>/part-NNNNNN.parquet`, part *i* holding rows `i*chunk` to `(i+1)*chunk`, then `_COMPLETE`; parts an earlier run left beyond these are removed |
| `lakehouse` | `base_path` (local folder, `abfss://` or `onelake://`), `format` (`parquet`, `csv`, `jsonl`) | `<base_path>/<table>/part-0001.<format>` |
| `warehouse` | `connection_string` (or `warehouse://host/db`), `staging_path`, `schema_name`, `write_mode`, `chunk_size` | `COPY INTO` from Parquet staged at `staging_path` |
| `sql_database` | `connection_string` (or `sql-database://host/db`), `schema_name`, `write_mode`, `batch_size` | inserts |
| `kql` | `cluster_uri`, `database`, `table_prefix`, `write_mode` | Eventhouse ingestion into `<table_prefix><table>` |

`write_mode` is `create` (the default: an existing table is an error), `append`, `truncate` or
`replace`. A database table is created with the primary key and column types of the generation
schema. Sign-in is the connection string's (or a `credential` given to the writer in library use);
the `--auth` modes come with the Fabric auth work package.

The Fabric sinks use the writers of the `shape-fabric` plugin (`pip install sqllocks-shape-fabric`). A table flows into its writer as it
is generated (a few batches in flight), not held until the end. Every sink gets every chunk;
a failing sink fails the run, after the others have finished the same chunk, and every sink that opened is closed. A run that stops early (a cancel or an error) calls an optional `abort()` on the sinks first: the Parquet sink then writes no `_COMPLETE` marker for the table it was writing, and a Fabric sink's writer gets an error instead of a clean end for a table that was cut short.

## Chunk size and processes

`--chunk-size N` (default 500,000) is the rows per Parquet part file. `--processes N` makes the
part files in N worker processes instead of threads (the Parquet sink alone, and a schema with no
post-pass: a `computed` column, rule repair or correlation changes a table only once it is whole,
so such a schema falls back to threads, with a warning). Workers write their own files, so no rows
cross a process boundary, and a stopped run resumes at a chunk.

## Jobs

Every scale run is a job in the job store (`$SHAPE_JOBS_DIR`, default `~/.shape/jobs`; one JSON file
per job, readable only by its owner, declaring `format: shape-job` and an integer `version`; a record from a newer release fails naming the release that reads it, and its unknown fields are kept on rewrite). A record holds what was asked, with secrets masked (sink settings, and the secrets of the `auth` block unless they are credential references), local output folders as absolute paths, and never
a token.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

* **Local jobs.** `cancel` stops the run between chunks, also when the run is in another process
  (it reads its record as it reports progress). `resume` runs the stored request again and
  skips the part files already written whole (a part counts when it is readable and has the right
  number of rows); a run killed outright is `failed` or still `running` with nothing behind it, and
  `cancel` then marks it cancelled. A secret in the request is masked in the record: give it again
  with `shape jobs resume JOB --sink-config SINK.KEY=VALUE`.
* **`fabric_spark` jobs.** The token comes from `SHAPE_FABRIC_TOKEN` (the Fabric API) and, for
  OneLake, `SHAPE_FABRIC_STORAGE_TOKEN` (or `azure-identity`). `resume` re-attaches to a run that is
  still active and submits a run that ended again, under the same job id.

## `fabric_spark`

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

The command writes the job spec (the schema, seed, row counts and chunk size; no credentials) to
OneLake `Files/shape_jobs/<run>.json`, finds the `shape_spark_worker` notebook in the workspace or
creates it from the bundled template, and starts it. The notebook installs this version of Shape,
reads the spec, and writes each table as Delta to `Tables/<prefix><table>`: a table with more rows
than a chunk and no post-pass is generated by the executors, one partition per chunk; the others
are generated on the driver (table by table, so the driver never makes the executors' tables, unless
the schema has a post-pass). A run fails (and records no success) unless every table has exactly
the rows the spec asked for, counted by reading each Delta table back once it is written.

## Library

```python
from shape.scale.api import scale_generate
from shape.scale.jobs import Jobs

result = scale_generate({"domain": "retail", "scale": "medium", "scale_mode": "local_mp",
                         "sinks": ["parquet"], "sink_config": {"parquet": {"output_dir": "out"}}},
                        jobs=Jobs())
```

`shape.scale.router.ScaleRouter(engine, sinks, mode=...)` runs an engine into sink objects;
`shape.scale.chunked.ChunkedGenerator` streams the large tables of a schema chunk by chunk; and
`shape.io.multi_store.MultiStoreWriter` writes finished tables to several writers at once.
