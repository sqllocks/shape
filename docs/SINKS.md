# Sinks: where generated data goes

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
| `mssql://host/db` | `sqlserver` | `sqllocks-shape-fabric` (needs `sqllocks-shape-sqlserver`) | SQL Server, Azure SQL, Fabric SQL database |
| `warehouse://...` | `warehouse` | `sqllocks-shape-fabric` | Fabric Warehouse, `COPY INTO` from a staging path |
| `synapse://workspace.sql.azuresynapse.net/pool` | `synapse` | `sqllocks-shape-fabric` | Synapse dedicated SQL pool, Parquet staged in ADLS Gen2 (`staging_path`), one `COPY INTO`, row count checked; `distribution`, `index` |
| `postgresql://host/db` | `postgres` | `sqllocks-shape-databases[postgres]` | `COPY ... FROM STDIN` |
| `mysql://host/db` | `mysql` | `sqllocks-shape-databases[mysql]` | batched multi-row `INSERT` |
| `snowflake://user@account/db/schema` | `snowflake` | `sqllocks-shape-databases[snowflake]` | Parquet `PUT` to the table stage, one `COPY INTO`, row count checked |
| `databricks://host/http_path?catalog=C&schema=S` | `databricks` | `sqllocks-shape-databases[databricks]` | Delta tables in Unity Catalog, batched bound multi-row `INSERT` |
| `kafka://`, `eventhubs://`, `eventstream://`, `eventhouse://` | emitters | their plugins | streaming sinks (`shape.emitters`), `shape emit` only |

An unknown scheme is an error that lists the schemes installed.

## OneLake and ADLS Gen2 (`abfss://`, `delta+abfss://`)

```bash
shape generate retail --scale small --seed 7 \
  --to abfss://landing@myacct.dfs.core.windows.net/raw \
  --table-format store=csv --batch-date 2026-10-02
# raw/order/ingest_date=2026-10-02/order_20261002.parquet, raw/store/.../store_20261002.csv, ...

shape stream retail -t order --to abfss://landing@myacct.dfs.core.windows.net/live \
  --roll-seconds 30 --realtime --rate 200 --checkpoint-seconds 30
shape generate retail --to delta+abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables
```

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
* **Rolling.** `--roll-rows N` and `--roll-seconds S` start a new numbered file; rows-based rolling
  is exact and deterministic. In a stream, every checkpoint also completes the file, so
  `--checkpoint-every` / `--checkpoint-seconds` set how often readers see new rows.
* **Delta.** One commit at the end, or `--commit-rows N`; in a stream, one commit per checkpoint.
  The first commit applies `--write-mode overwrite|append`, later ones append.
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

```bash
shape generate retail --scale small --to mssql://myserver.database.windows.net/mydb \
  --write-mode create
shape stream retail -t order --to postgresql://me@dbhost/shape --commit-rows 500 --realtime --rate 100
```

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
* **Constraints.** `--sql-constraints keep|disable` (`generate` and `emit`; the sink option
  `constraints`, default `keep`). `disable` runs `ALTER TABLE ... NOCHECK CONSTRAINT ALL` on every
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

```bash
shape generate retail --scale small --to duckdb:///out/retail.duckdb
shape emit retail --to duckdb:///out/retail.duckdb?schema=raw --write-mode append --max-events 10000
```

`pip install 'sqllocks-shape[duckdb]'` (the `sqllocks-shape-databases[duckdb]` extra; DuckDB is
never a core dependency). `duckdb:///PATH.duckdb` is relative to the working directory and
`duckdb:////abs/path.duckdb` absolute; `?schema=main` names the DuckDB schema (created when
missing). Arrow batches are scanned by DuckDB directly, with no per-row conversion. Tables are
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

The schema's primary key is a `PRIMARY KEY`. A type DuckDB cannot hold (nested values, durations)
is refused with the column named. `--write-mode` takes `create` (default), `append`, `truncate`,
`replace` and `upsert` (`INSERT OR REPLACE` on the primary key; without one:
`upsert needs a primary key on <table>`, exit 2). One table is one transaction; `--commit-rows N`
commits every N rows so another connection sees them. A database file locked by another process is
exit 2 with DuckDB's message. Reading the tables back gives the same `shape.repro.dataset_id` as
the generated tables.

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
