# Sinks: where generated data goes

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" SINKS
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for SINKS
    ```


Every output is a `shape.sinks` plugin: `Sink.write(uri, table, batches, **options) -> rows`. The
file sinks are chosen by `--format`; a **URI** with a scheme is routed to the sink that registered
it. `shape generate --to URI` writes the tables; `shape emit --to URI` and `shape stream --to URI`
write a stream (`docs/EMIT.md`). `--to` is repeatable: every target gets every table, generated
once.

| Target | Sink (name) | From | Notes |
|---|---|---|---|
| local files | `csv` `tsv` `jsonl` `parquet` `ipc` `excel` `delta` `sql` | core | `-o DIR`, `--format`. `sql` writes a **script** (`--format sql`) |
| `abfss://container@acct.dfs.core.windows.net/folder` | `abfss` | core, extra `[azure]` | ADLS Gen2 and OneLake files |
| `abfss://workspace@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Files/folder` | `abfss` | core, extra `[azure]` | OneLake Files |
| `delta+abfss://.../lh.Lakehouse/Tables` | `delta` | core, extra `[azure]` | Delta tables in OneLake or ADLS Gen2 |
| `mssql://host/db` | `sqlserver` | `sqllocks-shape-fabric[sqlserver]` | SQL Server, Azure SQL, Fabric SQL database |
| `warehouse://...` | `warehouse` | `sqllocks-shape-fabric[sqlserver]` | Fabric Warehouse, `COPY INTO` from a staging path |
| `synapse://workspace.sql.azuresynapse.net/pool` | `synapse` | `sqllocks-shape-fabric[sqlserver]` | Synapse dedicated SQL pool, Parquet staged in ADLS Gen2 (`staging_path`), one `COPY INTO`, row count checked; `distribution`, `index` |
| `postgresql://host/db` | `postgres` | `sqllocks-shape-databases[postgres]` | `COPY ... FROM STDIN` |
| `mysql://host/db` | `mysql` | `sqllocks-shape-databases[mysql]` | batched multi-row `INSERT` |
| `snowflake://user@account/db/schema` | `snowflake` | `sqllocks-shape-databases[snowflake]` | Parquet `PUT` to the table stage, one `COPY INTO`, row count checked |
| `databricks://host/http_path?catalog=C&schema=S` | `databricks` | `sqllocks-shape-databases[databricks]` | Delta tables in Unity Catalog, batched bound multi-row `INSERT` |
| `kafka://`, `eventhubs://`, `eventstream://`, `eventhouse://` | emitters | their plugins | streaming sinks (`shape.emitters`), `shape emit` only |

The `postgres` and `mysql` sinks verify TLS for any host that is not loopback (`sslmode=verify-full`;
MySQL with the system CA store or `ssl_ca`). Opt out in the URI with `sslmode=disable`/`prefer`
(PostgreSQL) or `ssl=false`/`ssl-mode=DISABLED` (MySQL); see `plugins/shape-databases/README.md`.

An unknown scheme is an error that lists the schemes installed.

## OneLake and ADLS Gen2 (`abfss://`, `delta+abfss://`)

<!-- example: 0 -->

**Needs a Fabric account. Not run in CI.**

Recorded by a manual `workflow_dispatch` run against the Fabric test workspace; the angle-bracket values stand for that tenant's identifiers. A write to a remote target needs `--yes` or `SHAPE_CONFIRM_REMOTE=1`; the recorded run set the variable. The test tenant has no ADLS Gen2 account, so the recorded run wrote the first two commands to a OneLake Lakehouse instead (`ws` is the workspace, `lh` the Lakehouse, folders `Files/shape_docs/raw` and `Files/shape_docs/live`); the output below shows those OneLake paths. The third command, `delta+abfss://…/Tables`, ran as shown.

```bash
shape generate retail --scale small --seed 7 \
  --to abfss://landing@myacct.dfs.core.windows.net/raw \
  --table-format store=csv --batch-date 2026-10-02
# raw/order/ingest_date=2026-10-02/order_20261002.parquet, raw/store/.../store_20261002.csv, ...

shape stream retail -t order --to abfss://landing@myacct.dfs.core.windows.net/live \
  --roll-seconds 30 --realtime --rate 200 --checkpoint-seconds 30
shape generate retail --to delta+abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables
```

??? info "Output (exit 0)"

    ```text {.expected}
    Wrote 21,750 rows in 9 tables to abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Files/shape_docs/raw (12.65s)
    shape stream: 5,000 events delivered, offset 5,000 of 5,000, complete, 210 events/s
    Wrote 21,750 rows in 9 tables to delta+abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables (32.39s)
    ```

Evidence: [run 38398883944, ubuntu-latest, `5fb3694b5da5`](evidence/transcripts/docs-transcripts-38398883944-5fb3694b5da5-ubuntu-latest-sinks-onelake.json).


* **Layout.** `--path-template` (default `{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}`;
  with rolling `..._{part}.{ext}`): Hive-style date partitions. Tokens: `{table} {ext} {date}
  {yyyymmdd} {yyyy} {mm} {dd}`, and for rolling `{part}` and `{hhmmss}`. `{date}` is
  `--batch-date`, or today (UTC) when none is given. `--format` and `--table-format TABLE=FORMAT`
  pick the format per table (`parquet`, `csv`, `tsv`, `jsonl`, `ipc`).
* **Atomic publish.** A file is written under a temporary name (`_shape_tmp/`, which readers skip)
  and renamed to its final name when complete, so a Fabric pipeline or a storage-event trigger
  never sees a partial file. On a hierarchical namespace the rename is atomic; on a flat blob
  namespace (Azurite) it is a server-side copy and delete, and the final blob still appears whole.
  `--manifest` writes `_SUCCESS` in each folder after its files.
  On OneLake the sink checks that the Lakehouse exists by listing the workspace and looking the item
  up by name, not by probing the item: OneLake refuses a HEAD request on the item folder and on its
  `Files` and `Tables` folders (`OperationNotAllowedOnThePath`) and allows HEAD on the workspace
  ([OneLake API parity](https://learn.microsoft.com/fabric/onelake/onelake-api-parity)). An item
  given by GUID cannot be found in a listing, so it is not checked up front and a missing one is
  reported by the first write.
* **Rolling.** `--roll-rows N` and `--roll-seconds S` start a new numbered file; rows-based rolling
  is exact and deterministic. In a stream, every checkpoint also completes the file, so
  `--checkpoint-every` / `--checkpoint-seconds` set how often readers see new rows.
* **Delta.** One commit at the end, or `--commit-rows N`; in a stream, one commit per checkpoint.
  `--table-prefix` changes the physical Delta table names. Other supported database/Fabric
  `--to` sinks receive the same prefix; outputs that cannot honour it refuse the option.
  The first commit applies `--write-mode overwrite|append`, later ones append. `overwrite` replaces
  the table including its schema (a regenerated model may have other columns); `append` keeps the
  table's schema and refuses data that does not fit.
* **Modes.** `--write-mode overwrite` (the default: a file of the same name is replaced), `append`
  (new numbered files after the existing ones), `fail` (error if the first file exists).
* **Retries and errors.** An upload that fails with a dropped connection, a timeout, throttling or
  a 5xx is repeated with a doubling pause (`retries`, default 3). A 401/403 is
  "not authorized to write ... check the credential and the *Storage Blob Data Contributor* role";
  a missing container or workspace says so.
* **Sign-in** is the source's (`docs/plugins/cloud-sources.md`): managed identity, a service
  principal in the environment, `az login`, the Fabric notebook identity
  (`DefaultAzureCredential`), or a storage key. Put a key in the environment
  (`AZURE_STORAGE_ACCOUNT_KEY`, `AZURE_STORAGE_SAS_TOKEN`, `AZURE_STORAGE_CONNECTION_STRING`).
  From Python pass `credential=`, `token=`, `account_key=`, `sas_token=` or `connection_string=`.
* **Hosts.** A credential is sent only to Azure Storage (`<account>.dfs.core.windows.net`,
  `<account>.blob.core.windows.net`, and the `core.usgovcloudapi.net` and `core.chinacloudapi.cn`
  forms) and OneLake (`onelake.dfs.fabric.microsoft.com`, `onelake.blob.fabric.microsoft.com` and
  their `<region>-onelake` forms). Any other host is refused before a credential is attached, so a
  URI in a pack, a config file or a bridge request cannot send your key or token elsewhere (#276).
  The same holds for the `abfss` source and `delta+abfss://`.

### OneLake targets

Before the first byte is written, the `abfss` sink checks a OneLake target (host
`onelake.dfs.fabric.microsoft.com`, or its regional form `<region>-onelake.dfs.fabric.microsoft.com`)
and stops with one line that names the fix. The command exits 2 as `shape: error: <the line>` and
nothing is written. An ADLS Gen2 host (`*.dfs.core.windows.net`) is not checked: the sink behaves as
before and makes no extra storage call.

Accepted forms (the folder is optional, and may be nested):

```
abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<name>.Lakehouse/Files/<folder>
abfss://<workspace-id>@onelake.dfs.fabric.microsoft.com/<item-id>/Files/<folder>
```

The first is the name form: the item is `<name>.<ItemType>` (`Lakehouse`, `Warehouse`, `Notebook`,
`SemanticModel`, `KQLDatabase`, and the other types OneLake exposes by name). The second is the GUID
form: the workspace GUID and the item GUID, with no type suffix. Use the GUID form whenever a
workspace or item name contains a space (OneLake addresses by such names are fragile); copy both
IDs from the item's URL in Fabric. Both forms are checked by the same rules.

| Error | Fix |
|---|---|
| `OneLake workspace or item name "<name>" contains a space; use the workspace and item IDs instead: abfss://<workspace-id>@onelake.dfs.fabric.microsoft.com/<item-id>/Files/<folder>` | Use the GUID form, or rename the workspace or item. A literal space and `%20` are both caught. |
| `OneLake item "<segment>" is not <name>.<ItemType> (for example lh.Lakehouse) or an item ID; ...` | Write the first path segment as `<name>.Lakehouse` (or another item type), or as the item GUID. |
| `OneLake target has no item; ...` | Add the item after the workspace. |
| `OneLake only accepts files below <item>/Files/ (or Delta tables below <item>/Tables/ with the delta sink)` | Write below `<item>/Files/` (not at the item root, and not in another folder). |
| `OneLake item "<item>" does not exist in workspace "<workspace>"; the storage API cannot create Fabric items: create the Lakehouse in Fabric first (or with shape fabric setup)` | Create the item in Fabric first. The storage API cannot create items. |
| `Warehouse tables are written through T-SQL, not OneLake storage: use the Fabric warehouse writer (the shape-fabric warehouse target)` | A Warehouse is not written through storage; use the `shape-fabric` warehouse target. |
| `files under Tables/ are not tables: write Delta with delta+abfss://... (the delta sink), or write files below Files/` | Plain files under a Lakehouse's `Tables/` are not tables: write Delta with `delta+abfss://...`, or write files below `Files/`. |

The item check asks the filesystem for the item folder once per target; a sign-in failure at that
point is the usual "not authorized" message. No message contains a credential, a token or a SAS
query.

## Databases

<!-- example: 2 -->

This bounded example uses the workflow's disposable PostgreSQL service. The `stream_` prefix keeps its tables separate from the seed example. Passwords come from `SHAPE_POSTGRES_PASSWORD`; none belong in a URI. It stops after 20 events.

<!-- owner-example: postgres-sink -->

```bash {.runnable-owner}
shape stream retail -t order --to postgresql://shape@localhost:5432/shape \
  --commit-rows 500 --realtime --rate 100 --max-events 20 \
  --sink-config postgres.table_prefix=stream_ --fresh
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape stream: 20 events delivered, offset 20 of 5,000, max-events, 52 events/s
    ```

Evidence: [run 38273696299, ubuntu-latest, `7e0aa725e228`](evidence/transcripts/docs-transcripts-38273696299-7e0aa725e228-ubuntu-latest-postgres-sink.json).


`--write-mode` is `create` (the default: never touches an existing table), `append`, `truncate`
or `replace` (`mssql://` and `duckdb://` also take `upsert`, below); `--commit-rows N` commits every N rows so readers see rows during the run (a stream
commits every batch). Tables are created from the schema (types, primary key) with the same type
mapping as the `sql` script sink. Identifiers are quoted and values are parameters. Passwords are
never in a URI or on a command line: use the environment (`SHAPE_POSTGRES_PASSWORD`/`PGPASSWORD`,
`SHAPE_MYSQL_PASSWORD`/`MYSQL_PWD`, `SNOWFLAKE_PASSWORD`, `DATABRICKS_TOKEN`), a credential object
(Entra token for SQL Server), or a credential reference. Snowflake loads Parquet files from the
table's stage with one `COPY INTO`; Databricks writes Delta tables with bound multi-row `INSERT`; a
Synapse dedicated SQL pool loads Parquet staged in ADLS Gen2 (`--sink-config synapse.staging_path=`);
each checks the loaded row count against the rows sent. See `docs/plugins/fabric-writers.md` and the
README of `sqllocks-shape-databases`.

**NaN and infinity become NULL.** The `warehouse://`, SQL database and `synapse://` writers store a
floating-point `NaN` or `±Infinity` as `NULL`: a T-SQL `float` holds only finite values
([float and real](https://learn.microsoft.com/sql/t-sql/data-types/float-and-real-transact-sql)),
and the writer makes the value `NULL` before staging or inserting it, so a pool, Warehouse or
database never receives one. Reading the table back gives `NULL` where `NaN` was written, and the
dataset id of the read-back differs from the NaN-bearing input for that reason alone.

### Generated SQL constraints

For `shape generate`, `--sql-constraints keys` is the default: primary keys, foreign keys
and declared unique keys are created before loading rows. `all` also derives supported
CHECK and literal DEFAULT clauses from the generation schema and `--contract FILE`
(JSON or YAML). `none` omits derived constraints; `keep` is its compatibility alias.
The existing `disable` option retains the SQL Server loading behavior described below.
These generation defaults do not change `emit`, seed or scale workflows.

| Destination | Constraint behavior |
|---|---|
| PostgreSQL, MySQL, DuckDB, SQL Server and Fabric SQL database | Enforced foreign keys, UNIQUE and CHECK constraints; literal DEFAULT clauses |
| Fabric Warehouse | Informational foreign keys and UNIQUE constraints (`NOT ENFORCED`); no CHECK or DEFAULT |
| Snowflake | Informational foreign keys and UNIQUE constraints; DEFAULT; no CHECK |
| Databricks Unity Catalog | Informational primary/foreign keys; CHECK through `ALTER TABLE`; no UNIQUE or DEFAULT |

Unsupported declarations produce a warning and a `-- NOTE:` in SQL scripts. Checks use
structured comparisons, conjunctions, membership and null tests; free-form SQL, regular
expression checks, triggers, sequences and deferred constraints are outside this feature.
Identifiers are quoted and row values remain bound parameters. `--dry-run` shows the
resolved DDL without opening a connection or writing data artifacts.

Shape prepares every table before loading and writes parents before children. DuckDB alone
uses parent-first inline foreign keys because it cannot add them afterward. Constraints
remain enforced. An existing destination must match the requested declarations before an
append or truncate; PostgreSQL/MySQL CHECK and DEFAULT verification also needs permission to
create temporary tables.

For a dataset whose referenced tables all belong to the run, PostgreSQL, MySQL and SQL
Server coordinate `truncate` in one transaction; PostgreSQL and SQL Server also coordinate
`replace`. A later batch failure restores the previous dataset. MySQL `replace`, DuckDB
reruns that cannot preserve enforced references, external incoming foreign keys and
incompatible `--commit-rows` combinations are refused before mutation, naming the
constraint. Other writes retain their existing transaction and committed-row accounting.
Failures name the constraint and include row details when the database supplies them.

## Confirming a non-local target

Writing to a database, OneLake, Event Hubs or Kafka is harmless on synthetic data and harmful on
the wrong target, so Shape asks first. Every command that writes to a **non-local** target needs a
confirmation, before it connects or signs in:

- `--yes` on `shape generate`, `shape emit`, `shape stream` (and `generate --scale-mode`);
- `SHAPE_CONFIRM_REMOTE=1` in the environment, for notebooks and pipelines (any other value, such
  as `0` or `true`, does not confirm);
- or, when stdin and stderr are a terminal, `y` at the prompt
  `Write to 2 non-local targets: postgresql://db.example/shape, abfss://...? [y/N]` (passwords in
  the URIs are hidden).

Without one the command exits 2:

```
shape: error: refusing to write to non-local target postgresql://db.example/shape without confirmation; pass --yes or set SHAPE_CONFIRM_REMOTE=1
```

Local is a path, `file://`, `jsonl://`, `duckdb://` (a DuckDB file), `console`, and a URI whose host is `localhost`,
`127.0.0.1` or `::1` (emulators). It applies to each `--to URI`, to `emit --sink URI` other than
`console`, `file` and `file://`/`jsonl://`, and to a `--scale-mode` `--sink` that is not `memory`,
`parquet` or a `lakehouse` with a local `base_path` (`warehouse`, `sql_database` and `kql` always
need it). `--dry-run` needs none. Reading from a remote source never asks.

From Python, `shape.cli.to.run_to(args, engine, started, confirm_remote=True)` and
`shape.io.targets.confirm_remote_targets(targets, confirm=True)` give a notebook the same
refusal unless it passes `True` or sets the variable. Plugin commands such as
`shape fabric publish` do not use this check yet.

### SQL Server: identity columns, constraints, reruns (`mssql://`)

* **Identity columns.** A generation-schema column with `"identity": true` (an `integer` column of
  the `sequence` strategy; any other type or strategy is a schema error naming the column) is
  created as `BIGINT IDENTITY(start, step)` with the sequence's `start` and `step`, and the inserts
  are wrapped in `SET IDENTITY_INSERT [schema].[table] ON` / `OFF`, so the keys that child foreign
  keys reference are kept. This is `identity=keep`, the default. `--sink-config mssql.identity=server`
  (or `?identity=server` in the URI) leaves the column out and lets the server number the rows; it
  is refused with exit 2 when a foreign key references the column
  (`identity=server would break the foreign key <child>.<column> -> <table>.<column>`), before
  anything is written. `shape from-ddl` writes `"identity": true` for `IDENTITY`, `SERIAL`,
  `BIGSERIAL` and `AUTO_INCREMENT` columns. A schema with no `identity` produces the DDL and
  scripts it always did. Fabric Warehouse has no identity semantics: it ignores the key.
* **Constraints.** Generation supports `--sql-constraints keys|all|none` as described above.
  The low-level sink option `constraints=keep|disable` retains its default `keep`, and
  `emit` retains its existing `keep|disable` behavior. `disable` runs
  `ALTER TABLE ... NOCHECK CONSTRAINT ALL` on every
  table the run writes that already exists, loads, then `ALTER TABLE ... WITH CHECK CHECK
  CONSTRAINT ALL`. When a constraint does not hold the rows stay, the run **exits 1** and names
  each table and constraint (`dbo.order.FK_order_customer`) and says it was **left disabled**; the
  other constraints of the table are enabled again. A failed load puts checking back on. A table
  the run creates has nothing to disable. `write_mode=truncate` on a table that a foreign key
  references uses `DELETE` (SQL Server refuses `TRUNCATE` there; the delete fails while rows of a
  child table still point at it), so the identity seed is not reset.
* **Idempotent reruns.** `--write-mode upsert`: each batch goes into a session temporary table
  and is merged into the target with `MERGE` on the primary key (non-key columns updated, missing
  rows inserted; an identity column is never updated). A table without a primary key is refused
  with exit 2 (`upsert needs a primary key on <table>`), and so is `identity=server` where the
  identity column is the key. Rerunning the same command (same seed and scale) leaves the same
  rows, and rerunning after a run killed mid-table (use `--commit-rows`) completes it without
  duplicates. `upsert` is for `mssql://` and `duckdb://`; PostgreSQL, MySQL and Warehouse refuse it.
* **Windows authentication from Linux.** `--auth kerberos --keytab REF --principal NAME@REALM`
  (`docs/plugins/fabric-auth.md`).

### DuckDB (`duckdb://`)

[Run this example](#local-example-4).


`pip install 'sqllocks-shape[duckdb]'` (the `sqllocks-shape-databases[duckdb]` extra; DuckDB is
never a core dependency). `duckdb:///PATH.duckdb` is relative to the working directory and
`duckdb:////abs/path.duckdb` absolute; `?schema=main` names the DuckDB schema (created when
missing). In the default native mode, Arrow batches are scanned directly, without per-row
conversion. Tables are
created from the schema:

| Arrow type | DuckDB column |
|---|---|
| bool | `BOOLEAN` |
| int8 / 16 / 32 / 64, uint8 / 16 / 32 / 64 | `TINYINT` / `SMALLINT` / `INTEGER` / `BIGINT`, `UTINYINT` / `USMALLINT` / `UINTEGER` / `UBIGINT` |
| float32 / float64 | `FLOAT` / `DOUBLE` |
| decimal128(p, s) | `DECIMAL(p,s)` |
| string (large, dictionary) | `VARCHAR`; `UUID` for a column typed `uuid` in the schema |
| binary | `BLOB` |
| date, time | `DATE`, `TIME` |
| timestamp (no zone, µs / ns / ms / s) | `TIMESTAMP` / `TIMESTAMP_NS` / `TIMESTAMP_MS` / `TIMESTAMP_S` |
| timestamp with a zone | `TIMESTAMPTZ` |
| list (including large/fixed-size), struct, map | `T[]`, `STRUCT("field" T, ...)`, `MAP(K, V)`, recursively |

The schema's primary key is a `PRIMARY KEY`. A type DuckDB cannot hold (for example durations)
is refused with the column named. `--write-mode` takes `create` (default), `append`, `truncate`,
`replace` and `upsert` (`INSERT OR REPLACE` on the primary key; without one:
`upsert needs a primary key on <table>`, exit 2). One table is one transaction; `--commit-rows N`
commits every N rows so another connection sees them. A database file locked by another process is
exit 2 with DuckDB's message. Reading the tables back gives the same `shape.repro.dataset_id` as
the generated tables.

The matching `shape.sources` DuckDB reader from `sqllocks-shape-integrations` opens the file
read-only: `duckdb:///abs/path.duckdb?table=main.NAME`. Its path follows that source's URI
syntax (three slashes for an absolute path). In Python,
`DuckDbSource().read(uri, batch_size=65536)` yields the stored Arrow types and values;
`schema(uri)` reads the table schema. It uses UTC for timestamp instants. A missing file
is refused without creating it; writer-only URI parameters are refused before opening.

### Native nested database values

The database writers and Delta accept `nested=native` (default) or `nested=json`. Database
writers also accept it as a URI parameter. On the command line use
`--sink-config postgres.nested=json`, substituting the sink name (including `delta`).
This option does not change the `sql` script sink or SQL Server/Fabric targets.

| Arrow container | PostgreSQL | MySQL | Databricks | DuckDB | Snowflake |
|---|---|---|---|---|---|
| scalar list | matching `T[]` | `JSON` | `ARRAY<T>` | `T[]` | `VARIANT` (`ARRAY` with `typed`) |
| struct | `JSONB` | `JSON` | `STRUCT<field: T, ...>` | `STRUCT(field T, ...)` | `VARIANT` (`OBJECT` with `typed`) |
| list of structs | `JSONB` | `JSON` | `ARRAY<STRUCT<...>>` | `STRUCT(...)[]` | `VARIANT` (`ARRAY` with `typed`) |
| map | `JSONB` | `JSON` | `MAP<STRING, V>` | `MAP(K,V)` | `VARIANT` (`OBJECT` with `typed`) |

PostgreSQL arrays are text-format COPY data with array quoting, not SQL expressions. Native
Databricks values are bound JSON passed to `from_json` with the resolved recursive schema and
`FAILFAST` parsing. Its JSON transport requires string map keys; non-string map keys and
unsupported leaves fail before connecting, naming the column and `nested=json` alternative.
Duplicate Databricks map keys are refused rather than collapsed; an invalid later streaming
batch reports how many earlier rows were committed. PostgreSQL arrays of lists are refused
before connecting; `nested=json` stores their JSON text instead.

Snowflake keeps `VARIANT` by default; `nested=typed` selects unstructured `ARRAY`/`OBJECT`.
Native/typed Parquet COPY explicitly uses the vectorized scanner and logical types so null
array entries retain their positions. Empty structs and non-string map keys cannot use this
Parquet object transport; errors name the column and suggest adding fields or preencoding a
JSON string. A manually preencoded Arrow string writes as `VARCHAR`; it does not restore a
native VARIANT/OBJECT automatically. Unsupported Parquet leaves and invalid nested decimal
precision/scale are also refused before connecting. Duplicate object map keys are refused.
Nested binary requires manual base64 encoding into Arrow strings before writing: a lossless
Parquet-to-VARIANT binary read-back mapping has not been verified. This applies to typed
and legacy modes too; top-level binary columns keep their scalar mapping.
Live connector validation remains an Owner action.

The explicit `json` mode keeps the earlier text/VARIANT paths: PostgreSQL `TEXT`, MySQL
`LONGTEXT`, Databricks `STRING`, Snowflake's existing VARIANT staging; DuckDB uses `VARCHAR`
JSON text. Delta's explicit `json` mode stores nested columns as UTF-8 JSON text too. Native
Delta maps retain key/value entries. Native Delta timestamps at every nesting depth read
back in UTC with microsecond precision, matching its scalar and list behavior; submicrosecond
precision is not retained. For example, timestamp nanoseconds `1767323045123456789` read back
as timestamp microseconds `1767323045123456`, including inside map values. Compare native
Delta values after this explicit UTC/microsecond normalization. Delta uses the existing
table protocol defaults. Unsupported nested Delta leaves name the
column and the `nested=json` alternative before storage is accessed.
Native writes preserve SQL NULL versus empty containers. PostgreSQL/MySQL JSON values and
Databricks JSON parameters encode decimals as exact text, temporal values as ISO text and binary values as base64; map
entries use key/value pairs where the target permits duplicates, and Databricks maps use JSON
objects. Non-finite numbers are refused by the native JSON transport.
Nanosecond Arrow time fields cannot cross the PostgreSQL/MySQL Python JSON or array
transport exactly; these shapes are refused before connecting. Preencode exact time values
as Arrow strings before writing. The JSON fallback also uses Python time values and does
not recover their discarded nanoseconds. Databricks nested timestamp values with nonzero
submicroseconds are refused before binding; use JSON text storage to keep their digits.
PostgreSQL native timestamp arrays likewise require at most microsecond precision;
nanosecond Arrow timestamp array schemas are refused with the JSON text alternative.

PostgreSQL scalar arrays, Databricks containers, DuckDB and Delta have native Arrow read-back
types. Databricks child types come from `full_data_type` catalogs, without child profiling.
JSON-backed
columns may read as JSON text; restoring their Arrow types requires the original Arrow schema
from the caller. Automatic child-schema recovery is not promised. Compare parsed values with
that schema after normalizing map/JSON key order, decimal scale and temporal instants. Corrupt
or schema-mismatched JSON must be refused, rather than coerced. Parse the source JSON exactly
once: a quoted JSON string is not an object or array, duplicate object fields are ambiguous,
and JSON-looking string children remain strings. Native DuckDB lists read back
as variable lists; nested time and zoned timestamps use the documented scalar microsecond
normalization, with UTC for instant comparisons. Exact duplicate struct field names must be
renamed; they cannot be represented unambiguously by the JSON fallback either.

`shape_databases.nested_readback.restore_json_column(raw_values, original_field)` performs
that strict schema-guided JSON restoration. It preserves exact decimal values and nanosecond
timestamps/times where the original type can hold them, and refuses precision loss. This
helper applies to JSON-backed physical columns; native Databricks catalog child types and
PostgreSQL array element types must be read directly rather than reconstructed from JSON.

## Secrets

A secret is never a command-line value (it would be in the process list and the shell history).
`--sink-config SINK.KEY=VALUE` takes options such as `abfss.account_name=acct`; a secret-looking key
must be a **credential reference** (`env://NAME`, `file://PATH` (mode 600), `kv://VAULT/NAME`); a
literal is refused. Errors and logs never contain a password, key, token or SAS signature.

`--auth cli|msi|spn|sql|device-code|fabric|kerberos`, `--tenant-id`, `--client-id`, `--client-secret REF`,
`--sql-user`, `--sql-password REF`, `--keytab REF`, `--principal NAME@REALM` and `--connection-string STR|REF` (the same options as
`shape emit` to Fabric, `docs/plugins/fabric-auth.md`) sign in to `abfss://`, `delta+abfss://`,
`mssql://`, `warehouse://` and `synapse://` targets of `shape generate --to` and
`shape emit/stream --to`; `--auth sql` needs `--connection-string`. PostgreSQL, MySQL, Snowflake and
Databricks sign in with their own secrets (a password environment variable or `password` reference;
for Snowflake a key pair, `--sink-config snowflake.private_key=file://...`; for Databricks a token,
`DATABRICKS_TOKEN`, or a client id and secret), and refuse `--auth`. References are resolved by
one resolver in core, `shape.security.credrefs`.

## Writing a sink

A sink takes `write(uri, table, batches, **options)` and consumes `batches` incrementally. Add
`schemes = ("myscheme",)` and it is routed. For streams add `open_table(uri, table, schema,
**options)` returning an object with `write_batch`, `flush` (make everything so far visible and
durable), `close` and `abort`; without it the stream drives `write` on a thread and relies on its
own commit option. Check it with `shape.plugins.kit.check_sink` and `shape conformance`.

### Apache Iceberg

Install `pip install 'sqllocks-shape[iceberg]'`. The optional extra uses
`pyiceberg[pyarrow,sql-sqlite,pyiceberg-core]>=0.9`; it loads only when Iceberg is used.
`iceberg+file:///warehouse/ns/items` creates a local SQLite catalog at
`/warehouse/catalog.db` on first write. `iceberg://catalog/ns/items` uses a named
catalog from `~/.pyiceberg.yaml`, or options `catalog_type` (`rest`, `sql`, `glue`,
`hive`), `uri` and `warehouse`. Glue and Hive option acceptance is untested against
services. `credential` and `token` are references (`env://NAME`, private `file://PATH`
or `kv://VAULT/NAME`); credentials and query parameters are refused in URIs.

`IcebergSink.write(uri, table, batches, **options)` writes the table named by the
URI. A table URI accepts one generated table per target. Default mode is
`overwrite`; `append` preserves rows. Format version is 2; `format_version=1` is
accepted. Schema differences refuse the write naming the column, with no evolution.
`partition_by` accepts `id`, `day(ts)`, `month(ts)`, `bucket(16, id)` and
`truncate(4, s)`; types and positive widths are checked before catalog writes.
`commit_rows` and `commit_seconds` commit one snapshot per micro-batch; the first
snapshot applies the mode, later snapshots append. `fingerprint=True` (or a run
context) sets snapshot summary `shape.fingerprint` for that batch.

| Arrow | Iceberg |
| --- | --- |
| int8, int16, int32 | int |
| int64, uint32 | long |
| uint64 | decimal(20,0) |
| float32, float64 | float, double |
| decimal precision <= 38 | decimal(p,s) |
| string, binary, bool, date | string, binary, boolean, date |
| time64 | time (microseconds) |
| zoned / zone-less timestamp | timestamptz (UTC) / timestamp (microseconds) |
| binary field with `uuid=true` metadata | uuid |
| list, struct, map | native list, struct, map |

Original widths and field metadata are recorded in field documentation and a
`shape.iceberg.arrow-schema` format/version-1 table property. The source restores
widths recursively; temporal values normalize to microseconds and zoned timestamps
to UTC. Nanosecond timestamps/time refuse the column unless `truncate_ns=True`
explicitly allows truncation. Decimal precision above 38 is refused.

`IcebergSource.read` scans Arrow batches. `snapshot_id` (integer) or `as_of`
(ISO timestamp), mutually exclusive, selects an earlier snapshot. Profile provenance
records catalog, table identifier, snapshot id and snapshot summary `total-records`.
A namespace URI (`iceberg://catalog/ns` or `iceberg+file:///warehouse/ns`) profiles
all its tables. Nested columns follow opaque profiling semantics. Row-level deletes,
MERGE, format 3, branches/tags, evolution and maintenance are unsupported.

UUID logical fields may use `uuid=true` metadata on 16-byte binary or
`shape.type=uuid` metadata on strings; native Arrow UUID fields are accepted too.
String UUIDs read back in canonical hyphenated form. Invalid UUID values refuse the
column without quoting its data. Decimal scale must be between zero and precision,
as required by Iceberg; unsupported scales are rejected before catalog writes.


## Delta table features (opt-in)

The default Delta write still uses reader **1** / writer **2**, no table features,
and no configuration. Zone-less timestamps are rewritten to UTC, preserving their
wall-clock value. Existing golden files and benchmark fixtures are unchanged.
`DeltaSink.write` and `open_table` accept these options. Opt-ins require the
verified `deltalake>=1.6.6` release and their public APIs; refusals name both the
installed and required versions before writing. The published `deltalake>=0.17`
range remains available for default writes:

The Delta sink's read-only `feature_plan(options)` method returns the minimum
protocol and feature lists without importing the native writer or opening storage.
The command-line dry run uses this method through the installed sink plugin.

Custom `table_properties` keys outside the `delta.` namespace are allowed, except
Shape's reserved fingerprint property. Engine properties are restricted to these
verified protocol-neutral keys: `delta.appendOnly`, `delta.checkpointInterval`,
`delta.checkpoint.writeStatsAsJson`, `delta.checkpoint.writeStatsAsStruct`,
`delta.deletedFileRetentionDuration`, `delta.logRetentionDuration`,
`delta.setTransactionRetentionDuration`, `delta.dataSkippingNumIndexedCols`,
`delta.dataSkippingStatsColumns`, `delta.enableExpiredLogCleanup`,
`delta.targetFileSize`, `delta.randomizeFilePrefixes`, `delta.randomPrefixLength`,
and `delta.isolationLevel`. The native writer validates their values before a
requested property reaches the destination. Other engine properties are refused
before storage opens: use the supported feature options below. This keeps the
offline protocol plan accurate and prevents properties from enabling unsupported
change data feed, row tracking, type widening, v2 checkpoints or Iceberg features.

Declare `generated_columns` when creating a new table. Appends retain and enforce
its existing expressions. Adding or changing generated expressions on an existing
table is table maintenance and is refused before its version or rows change;
create a new table with the requested generated columns instead.

| Option | Value and effect | Required protocol |
|---|---|---|
| `table_properties` | String-to-string object passed as `configuration` on creation; changed properties are committed after a successful append. Custom properties compose with `shape.fingerprint`. Feature/protocol and fingerprint properties are reserved for their dedicated options. | 1 / 2 for ordinary properties |
| `constraints` | `not_null` is a list of column names; `check` maps names to the structured expressions below. Raw SQL checks are refused. `true` derives not-null checks from nonnullable Arrow fields and `columns` metadata. Checks are added after the first data commit. | reader 1 / writer 3; `checkConstraints` |
| `checks` | A list of `{name, expression}` objects produced by the shared generation-schema and contract derivation. Uses the same structured-expression validation as `constraints.check`. | reader 1 / writer 3; `checkConstraints` |
| `column_mapping` | `"name"` creates a mapped table with stable physical names and positive ids in each field's metadata. Conversion of an existing unmapped table and renaming are table maintenance by another engine. | reader 2 / writer 5; `columnMapping` |
| `deletion_vectors` | `true` enables `delta.enableDeletionVectors` and its reader/writer feature. This opt-in adds no deletion-vector actions, deletes, merges or upserts. Another engine may later update the table. | reader 3 / writer 7; `deletionVectors` |
| `generated_columns` | `{"day": "CAST(ts AS DATE)"}` writes `delta.generationExpression`. The batch must supply the column with exactly the expression's result, including null semantics; disagreement is refused naming the column. | reader 1 / writer 4; `generatedColumns` |
| `timestamp_ntz` | `true` retains zone-less timestamps; zoned timestamps keep their zone-aware instant. False retains the default UTC rewrite. | reader 3 / writer 7; `timestampNtz` |

Native `map<timestamp_ntz>` with `column_mapping=name` is refused before storage access: no verified deltalake release supports this combination (verified feature minimum 1.6.6). Use `timestamp_ntz=false` for native UTC values or `nested=json`. Unmapped native timestamp maps are supported; `delta.appendOnly=true` is applied to the empty initialized table before user rows are written.

Protocols compose by taking the highest reader/writer version; writer 7 advertises
feature lists. Delta-rs may add supporting features to the stored protocol. The
installed version's public APIs are checked before writing. The implementation is
verified with **deltalake 1.6.6**; this is a tested version, not a new dependency
floor. A missing API produces an error naming the option, installed version and
required capability, with 1.6.6 as the verified release. The `[delta]` extra's
`deltalake>=0.17` floor is unchanged. No additional core dependency is introduced.

Checks and generated expressions are validated before touching the destination;
a rejected append keeps its previous table version and properties. Adding checks
also validates existing rows. Opt-in validation materializes pending batches and
uses a temporary local Delta table; adding checks to an existing table also reads
its current rows. Choose appropriate micro-batch thresholds for large inputs.
Existing constraints and generated expressions continue to apply on an append
even when their options are omitted. Errors name rules/columns and omit native
error bodies, which can contain row values or sign-in information.

```python
from shape.builtins.sinks.delta import DeltaSink

DeltaSink().write(
    "lake", "events", batches,
    generated_columns={"day": "CAST(ts AS DATE)"},
    partition_by=["day"],
    constraints={"not_null": ["ts"]},
    table_properties={"owner": "analytics"},
)
```

Checks use the same constrained expression vocabulary as SQL sink derivation.
An operand is `{"column": "amount"}` or `{"literal": 0}`. Comparisons have
`op` (`=`, `<`, `<=`, `>` or `>=`), `left` and `right`; `and` has an `args` list;
`in` has `left` and scalar `values`; `is_null` has `left`. Literal values are
strings, booleans, null or finite numbers. Unknown columns, extra members, compound
literals, trees deeper than 32 levels and trees over 1,024 nodes are refused.
Column identifiers and literals are quoted separately before native validation.

For example, an amount bound is structured data:

```python
positive = {"op": ">=", "left": {"column": "amount"}, "right": {"literal": 0}}
DeltaSink().write("lake", "orders", batches, constraints={"check": {"positive": positive}})
```

The existing `sql_options` derivation can supply schema business rules and a
contract's numeric bounds or accepted values without accepting SQL text:

```python
from shape.generation.output import sql_options

derived = sql_options(schema, "events", constraints="all", contract=contract)
DeltaSink().write(
    "lake", "events", batches,
    checks=derived["checks"], columns=derived["columns"], constraints=True,
)
```

Use existing `--sink-config` options without new command-line flags:

```sh
shape generate retail --format delta -o lake --dry-run --json \
  --sink-config delta.column_mapping=name \
  --sink-config delta.deletion_vectors=true
shape generate retail --format delta -o lake \
  --sink-config 'delta.table_properties={"owner":"analytics"}'
shape generate retail --format delta -o lake --sql-constraints all --contract contract.json \
  --sink-config delta.constraints=true
```

The CLI accepts `delta.constraints=true`, `delta.constraints=false` or a JSON
object containing `not_null` and structured `check` entries. These feature values
compose with the separate `--sql-constraints` policy. False adds no automatic
named not-null checks; schema and contract checks selected by `all` still apply.
Dry-run uses the same per-table derived options as the write and reports their
protocol union for schema, fitted-profile and Delta URI destinations.

`--dry-run` reports `delta_protocol` (minimum reader/writer versions and the feature
union), resolves no sign-in, and creates no table. `shape doctor --delta-table
lake/events --json` reports the actual reader/writer versions and both stored
feature lists, while retaining its PyArrow-reader compatibility warning. For
correct native readback of mapped or advertised-DV tables with deltalake 1.6.6,
use its public DataFusion reader:

```python
from deltalake import DeltaTable, QueryBuilder
import pyarrow as pa

table = DeltaTable("lake/events")
rows = pa.table(QueryBuilder().register("events", table).execute("SELECT * FROM events").read_all())
```

The native PyArrow scanner can return nulls for mapped columns or refuse the DV
feature; Shape's existing W2-03 read fallback handles those cases through DuckDB.

## SQL type fidelity on write

Requirement: SHAPE-SINK-001.

Arrow types resolve directly to SQL DDL; integer widths and decimal precision are not
inferred from values. The script and live PostgreSQL/MySQL maps are shared.

| Arrow | PostgreSQL | MySQL | T-SQL | Fabric Warehouse | Snowflake | Databricks |
|---|---|---|---|---|---|---|
| int8 / int16 / int32 / int64 | SMALLINT / SMALLINT / INTEGER / BIGINT | TINYINT / SMALLINT / INT / BIGINT | SMALLINT / SMALLINT / INT / BIGINT | SMALLINT / SMALLINT / INT / BIGINT | NUMBER(3,0) / NUMBER(5,0) / NUMBER(10,0) / NUMBER(19,0) | TINYINT / SMALLINT / INT / BIGINT |
| uint8 / uint16 / uint32 | next signed width | next signed width | TINYINT / INT / BIGINT | SMALLINT / INT / BIGINT | NUMBER(5,0) / NUMBER(10,0) / NUMBER(19,0) | SMALLINT / INT / BIGINT |
| uint64 | NUMERIC(20,0) | DECIMAL(20,0) | DECIMAL(20,0) | DECIMAL(20,0) | DECIMAL(20,0) | DECIMAL(20,0) |
| float32 | REAL | FLOAT | REAL | REAL | FLOAT(24) | FLOAT |
| zoned timestamp | TIMESTAMPTZ(p) | TIMESTAMP(p) | DATETIMEOFFSET(p) | DATETIME2(6) | TIMESTAMP_TZ(p) | TIMESTAMP |
| zone-less timestamp | TIMESTAMP(p) | DATETIME(p) | DATETIME2(p) | DATETIME2(p) | TIMESTAMP_NTZ(p) | TIMESTAMP_NTZ |
| time32 / time64 | TIME(p) | TIME(p) | TIME(p) | TIME(p) | TIME(p) | TIME(6) |
| uuid metadata | UUID | CHAR(36) | UNIQUEIDENTIFIER | VARCHAR(36) | VARCHAR(36) | VARCHAR(36) |

Unsigned values use the next signed width; uint64 needs 20 decimal digits. T-SQL int8
uses SMALLINT because TINYINT is unsigned. Timestamp/time units s, ms, us and ns mean
0, 3, 6 and 9 fractional digits, capped at the dialect's limit (7 for T-SQL, 6 for
PostgreSQL, MySQL and Fabric Warehouse). Snowflake supports 9; Databricks timestamps
store microseconds. A zoned timestamp keeps its instant to the microsecond. Writes
normalize to UTC, with +00:00 in offset-capable types; MySQL sets its write session to
UTC, and Warehouse stores UTC in DATETIME2(6). Read-back comparisons normalize to
UTC-aware values. The original zone name is not preserved. Zone-less values stay zone-less.

Decimals always use the Arrow precision and scale, otherwise `columns.precision` and
`columns.scale`; incomplete or invalid dimensions are refused with the column name.
PostgreSQL accepts Arrow decimal256 precision and negative/excess scales; negative
or greater-than-precision scales require PostgreSQL 15 or later. The other dialects
refuse those scales rather than changing the value.
UUID is selected by `columns.type="uuid"` or Arrow field metadata `type=uuid`.
String lengths come from `columns.max_length` (generation-schema declared widths are
forwarded there). Unspecified strings are unbounded: TEXT for PostgreSQL/MySQL,
NVARCHAR(MAX) for T-SQL, VARCHAR(MAX) in script Warehouse DDL, VARCHAR for Snowflake,
and STRING for Databricks. The live Warehouse writer also uses VARCHAR(MAX) (the service stores up to 16 MB);
T-SQL and Warehouse string key columns require explicit `max_length`; an undeclared
width is refused before any connection, naming the column. Shape never guesses a key width. Synapse retains its separate columnstore-compatible
8000-character limit. The previous VARCHAR(255) default
and first-batch string sizing are removed; a short first batch cannot truncate a later one.

T-SQL declared strings beyond 4000 characters and Warehouse declared strings beyond
8000 characters use their unbounded `MAX` types. Such widths on key columns are
refused before connecting because `MAX` columns cannot be indexed. Declared widths
are preserved without clamping; backend limits on actual index values still apply.

`shape generate schema.json --to postgresql://user@host/db --dry-run` prints the
resolved CREATE TABLE statements without generating rows, signing in, connecting or
writing. `--json` adds a `ddl` list to the generation plan, each with `target`, `table`
and `sql`. SQL database sink adapters expose the optional offline
`ddl(uri, table, arrow_schema, **options)` hook; values and credentials never enter DDL.

## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-4"></a>

### Example 5

<!-- example: 4 -->

```bash {.runnable-reference}
shape generate retail --scale small --to duckdb:///out/retail.duckdb
shape emit retail --to duckdb:///out/retail.duckdb?schema=raw --write-mode append --max-events 10000
```

??? info "Output (exit 0)"

    ```text {.expected}
    Wrote 21,750 rows in 9 tables to duckdb:///out/retail.duckdb (0.52s)
    shape emit: 10,000 events delivered, offset 10,000 of 21,750, max-events, 40,123 events/s
    ```

## BigQuery

The experimental `bigquery` sink accepts `bigquery://PROJECT/DATASET?location=US` through
`shape generate --to`. Install `sqllocks-shape-databases[bigquery]` and configure Google ADC
or a credentials reference. Writes use bounded Parquet load jobs; `commit_rows` makes load
results visible while a stream runs. The default mode refuses an existing table. Query
jobs are dry-run checked against `max_bytes_billed` (default 1 GiB). See the
[database plugin README](../plugins/shape-databases/README.md#bigquery-bigquery) for sign-in,
types, write modes and partial failure behavior. Real account execution needs owner validation.

## Oracle

The `oracle` sink in `sqllocks-shape-databases[oracle]` writes Oracle 19c+ through thin-mode
bound array DML. Use `oracle://user@host:1521/service?schema=S`, or `tcps://` for TLS, with
`ORACLE_PASSWORD` or a referenced password. Remote hosts verify TLS by default. `batch_rows`
defaults to 50000. Empty strings are refused; `empty_string=null` permits conversion only
for nullable non-key columns. Oracle DDL commits implicitly, including replace and truncate.
See the plugin README's Oracle section for types, wallets, transaction limits and the source.

UUID fields read from a source use Arrow field metadata `shape.type=uuid`; writers also accept the earlier `type=uuid` metadata key.
