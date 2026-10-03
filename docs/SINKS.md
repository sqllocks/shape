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

## Databases

```bash
shape generate retail --scale small --to mssql://myserver.database.windows.net/mydb \
  --write-mode create
shape stream retail -t order --to postgresql://me@dbhost/shape --commit-rows 500 --realtime --rate 100
```

`--write-mode` is `create` (the default: never touches an existing table), `append`, `truncate`
or `replace`; `--commit-rows N` commits every N rows so readers see rows during the run (a stream
commits every batch). Tables are created from the schema (types, primary key) with the same type
mapping as the `sql` script sink. Identifiers are quoted and values are parameters. Passwords are
never in a URI or on a command line: use the environment (`SHAPE_POSTGRES_PASSWORD`/`PGPASSWORD`,
`SHAPE_MYSQL_PASSWORD`/`MYSQL_PWD`), a credential object (Entra token for SQL Server), or a
credential reference. See `docs/plugins/fabric-writers.md` and the README of
`sqllocks-shape-databases`.

## Secrets

A secret is never a command-line value (it would be in the process list and the shell history).
`--sink-config SINK.KEY=VALUE` takes options such as `abfss.account_name=acct`; a secret-looking key
must be a **credential reference** (`env://NAME`, `file://PATH` (mode 600), `kv://VAULT/NAME`); a
literal is refused. Errors and logs never contain a password, key, token or SAS signature.

`--auth cli|msi|spn|sql|device-code|fabric`, `--tenant-id`, `--client-id`, `--client-secret REF`,
`--sql-user`, `--sql-password REF` and `--connection-string STR|REF` (the same options as
`shape emit` to Fabric, `docs/plugins/fabric-auth.md`) sign in to `abfss://`, `delta+abfss://`,
`mssql://` and `warehouse://` targets of `shape generate --to` and `shape emit/stream --to`;
`--auth sql` needs `--connection-string`. PostgreSQL and MySQL sign in with their password
environment variables or a `password` reference, and refuse `--auth`. References are resolved by
one resolver in core, `shape.security.credrefs`.

## Writing a sink

A sink takes `write(uri, table, batches, **options)` and consumes `batches` incrementally. Add
`schemes = ("myscheme",)` and it is routed. For streams add `open_table(uri, table, schema,
**options)` returning an object with `write_batch`, `flush` (make everything so far visible and
durable), `close` and `abort`; without it the stream drives `write` on a thread and relies on its
own commit option. Check it with `shape.plugins.kit.check_sink` and `shape conformance`.
