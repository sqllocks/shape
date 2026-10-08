# Fabric writers and the `onelake://` source (`sqllocks-shape-fabric`)

Writers for Lakehouse files, SQL databases, Warehouses and Synapse dedicated SQL pools (`COPY INTO`),
Eventhouses and Eventstreams,
and a source that reads lakehouse tables and files by `onelake://` URI. The API and the write modes
are described in the plugin's README (`plugins/shape-fabric/README.md`).

Safety rules the writers follow: the default `write_mode` is `create`, which never touches an existing
table; identifiers are validated and quoted; values are driver parameters; the `COPY INTO` location is
checked against a strict character set; errors never show passwords or tokens; a failed write rolls
back its table (a `truncate` or `replace` keeps the old rows), and staged files and partial files are removed.

The `onelake://<workspace>/<lakehouse>/Tables/<table>` form reads a Delta table and
`.../Files/<path>` reads files, through the core `abfss://` source (see [cloud-sources.md](cloud-sources.md)).

## `mssql://`: a live SQL Server, Azure SQL or Fabric SQL sink

`shape-fabric` registers three `shape.sinks` entries: `sqlserver` (URI schemes `mssql` and
`sqlserver`, bulk insert into a live database), `warehouse` (`warehouse://`, `COPY INTO`) and
`synapse` (`synapse://`, below). The
`sql` sink of core is different: it writes a script file and never connects.

```python
from shape.plugins.host import PluginHost

sink = PluginHost().get("shape.sinks", "sqlserver")
sink.write(
    "mssql://myserver.database.windows.net/shop?schema=dbo&write_mode=append",
    "customer", batches,                       # batches: an iterable of Arrow RecordBatch
    credential=my_credential,                  # get_token(scope) or scope -> token (Entra)
    commit_rows=50_000,                        # optional: commit every 50,000 rows
)
```

| URI part or option | meaning |
|---|---|
| `mssql://[user[:password]@]host[:port]/database` | the server and database (`sqlserver://` is the same) |
| `?schema=`, `schema_name=` | SQL schema (default `dbo`; created when missing) |
| `?write_mode=`, `write_mode=` | `create` (default; an existing table is an error), `append`, `truncate`, `replace`, `upsert` (below); the `TRUNCATE` or `DROP TABLE` commits together with the new rows, so a failed write keeps the old rows |
| `?identity=`, `identity=` | `keep` (default) or `server` for columns whose `columns` entry has `identity` (below) |
| `?constraints=`, `constraints=` | `keep` (default) or `disable` (below) |
| `columns=`, `primary_key=`, `schema=`, `identity_references=` | per-column metadata (`type`, `nullable`, `max_length`, `identity`), the key columns, the Arrow schema for an empty table, and the foreign keys that point at an identity column (`{"child", "column", "parent_column"}`) |
| `?batch_size=`, `batch_size=` | rows per round trip (default 5,000) |
| `?commit_rows=`, `commit_rows=` | commit every N rows while the batches are consumed, so readers see rows as they arrive; the default is one transaction per call (a failure rolls everything back). With `commit_rows` a failure rolls back only the open chunk and keeps what was committed |
| `credential=` | Microsoft Entra sign-in; without it the login is `user` and `password` |
| `connection_string=`, `connection=` | an ODBC/ADO.NET connection string, or an open DB-API connection (never closed by the sink), instead of the URI's host and database |
| `driver`, `encrypt`, `trust_server_certificate`, `timeout` | connection settings (query or option) |

### Identity, constraints and upsert

* **Identity.** A column whose `columns` entry has `"identity": {"start": N, "step": M}` (or `True`)
  is created as `BIGINT IDENTITY(N, M)`. `identity=keep` inserts the generated values between
  `SET IDENTITY_INSERT [s].[t] ON` and `OFF` (also turned off when the insert fails).
  `identity=server` leaves the column out so the server numbers the rows, and raises a `ShapeError`
  (`identity=server would break the foreign key <child>.<column> -> <table>.<column>`) before any
  connection is made when `identity_references` lists a key that points at the column; a table whose
  columns are all identity columns is refused too. Fabric Warehouse ignores identity.
* **Constraints.** `constraints=disable` runs `ALTER TABLE ... NOCHECK CONSTRAINT ALL` on a table that
  already exists, loads and commits, then `ALTER TABLE ... WITH CHECK CHECK CONSTRAINT ALL`. If
  that fails, `ConstraintError` (a `WriteError` with `exit_code = 1`; `shape` exits 1) names each
  constraint that does not hold as `schema.table.constraint` and says it was left disabled, and
  the others are enabled again; the rows stay. If the load itself fails, checking is put back on
  (`ALTER TABLE ... CHECK CONSTRAINT ALL`) and the load's error is raised. `write_mode=truncate` on
  a table that a foreign key references uses `DELETE`, because SQL Server refuses `TRUNCATE` there.
* **Upsert.** `write_mode=upsert` loads each `batch_size` rows into the session temporary table
  `#shape_stage` and merges it into the target with `MERGE ... WITH (HOLDLOCK)` on `primary_key`:
  non-key columns are updated (an identity column never is) and missing rows inserted; the table is
  created when missing. No primary key: `upsert needs a primary key on <table>`. `identity=server`
  with the identity column as the key is refused. Rerunning the same load leaves the same rows, and
  with `commit_rows` a load killed half way is completed by running it again. The SQL database writer
  (`SqlDatabaseWriter`) supports `upsert`; the Warehouse and Eventhouse writers refuse it.
* **Kerberos.** The sink takes `kerberos=` (a `shape_fabric.kerberos.KerberosSession`, made by
  `--auth kerberos`) or `trusted_connection=True`, and signs in with `Trusted_Connection=yes`
  (`docs/plugins/fabric-auth.md`).
* **`preflight(uri, {table: options})`** checks all of the above for a whole run before the first
  table is written; `shape generate --to` and `shape emit` call it.

Each call writes one table and is safe to call again for each micro-batch of a stream with
`write_mode=append`. The batches are consumed one at a time; nothing is held back until the end.
A password is accepted as an option or in the URI's user information, never in the query string; no
password, token or connection string appears in an exception message or a log record, and none
should go on a command line (use an environment variable and pass it as an option). The `--auth`
modes and `env://`, `kv://` and `file://` credential references are added to these writers by the
sign-in work package and will apply here without a change to the sink.

## `synapse://`: a Synapse dedicated SQL pool

`synapse` (`shape.sinks`, `shape_fabric.SynapseSink`; the writer is `shape_fabric.SynapseWriter`)
writes a table into a **dedicated SQL pool** of an Azure Synapse workspace. It follows the Warehouse
writer: it prepares the table with the same T-SQL helpers (same `write_mode` values, the safe `create`
default, `schema_name`, `columns`, `primary_key`), stages the rows as Parquet files of at most
`chunk_rows` rows (default 1,000,000) under `<staging_path>/staging/<run>/<table>/`, runs **one**
`COPY INTO ... WITH (FILE_TYPE = 'PARQUET')` over that folder, checks that the number of rows it
loaded equals the number staged (otherwise the write fails and a table this call created is dropped),
and deletes the staged files, also when a step fails.

```
shape generate retail --to synapse://myws.sql.azuresynapse.net/pool1 \
  --auth cli --sink-config synapse.staging_path=abfss://stage@myacct.dfs.core.windows.net/shape
```

| URI part or option | meaning |
|---|---|
| `synapse://<workspace>.sql.azuresynapse.net/<pool>` | the workspace's SQL endpoint and the dedicated pool. No user part, port or query: a serverless endpoint (`-ondemand`) is refused, and so is a password anywhere in the URI |
| `staging_path` | **required**: an ADLS Gen2 folder, `abfss://<container>@<account>.dfs.core.windows.net/<folder>`, that the pool can read. `COPY INTO` reads it as `https://<account>.dfs.core.windows.net/<container>/<folder>/...` |
| `copy_identity` | who `COPY INTO` reads the storage as: `managed_identity` (default; the workspace's managed identity, `CREDENTIAL = (IDENTITY = 'Managed Identity')`) or `signed_in` (the Microsoft Entra identity of the connection, no `CREDENTIAL` clause; refused with a SQL login, which has no such identity) |
| `distribution` | `ROUND_ROBIN` (default), `REPLICATE` or `HASH(column)`; the column must be one of the table's and is quoted |
| `index` | `CLUSTERED COLUMNSTORE INDEX` (default) or `HEAP` |
| `write_mode`, `schema_name`, `chunk_rows`, `columns`, `primary_key`, `schema` | as the Warehouse writer (`schema` creates an empty table from no batches) |
| `credential`, `connection_string`, `connection` | sign-in, below |

`distribution` and `index` apply to a table this call creates; a table that already exists
(`append`, `truncate`) keeps its own. Synapse applies its own limits to the hash column's type; a
refusal from the pool fails the write and a table this call created is dropped. A primary key is
declared `NONCLUSTERED ... NOT ENFORCED` (the pool does not enforce keys). Column types are the
Warehouse's (`VARCHAR(8000)`, `DATETIME2(6)`, `BIT`, ...; no `(N)VARCHAR(MAX)`, which a clustered
columnstore index cannot hold), and timestamps are staged in microseconds in UTC.

**Sign-in** is the plugin's `--auth` modes ([fabric-auth](fabric-auth.md)): `cli`, `msi`, `spn`,
`device-code` and `fabric` send a Microsoft Entra token for the SQL connection and for the storage;
`--auth sql` adds a SQL login to `--connection-string synapse://<workspace>.sql.azuresynapse.net/<pool>`
(the password a reference, never a command-line value). From Python give `credential=` or a
`connection_string` with the login. No password, token or connection string appears in an exception
message or a log record.

**What a failure leaves behind.** The load is one `COPY INTO`, committed once the row count has been
checked. A failure rolls back and drops a table this call created (`replace` has already dropped the
old table and `truncate` has already emptied it); the staged files are removed in every case (a
removal that itself fails is a `RuntimeWarning`).

Out of scope: serverless SQL pools, Synapse Spark pools and Synapse pipelines (those are
`integrations/synapse`).

### Read an Eventhouse

The Fabric plugin registers the `eventhouse` source. Read one table with
`shape profile 'eventhouse://<query host>/<database>?table=T' -o table.shape`,
or omit `table` to capture every table in one multi-table profile. The same URI
works through the bridge profile command and as a source for `shape diff`.
Sign-in uses the emitter's `token` option, `SHAPE_EVENTHOUSE_TOKEN`, or Entra
(`sqllocks-shape-fabric[entra]`). Credentials belong in options or environment
variables, never in the URI. `tls=false` supports local emulators.

Python source options are `sample_rows` (default 1000; 0 reads all) and
`batch_size` (default 65536, maximum 500000). Sampling uses KQL `sample N`;
`source_sampling` records `sampled_rows`, `sample_method`, and `catalog_rows`
from `.show table T details`. Profiles exclude emitter `_shape_table` and
`_shape_seq` columns by default, so their delivery metadata does not cause drift.
Add `dedupe=true` to collapse repeated emitter rows using
`summarize take_any(*) by _shape_table, _shape_seq`; ordinary tables without
both key columns are read unchanged.

Schema comes from `.show table T schema as json`. KQL maps to Arrow as follows:
`long` → int64, `int` → int32, `real` → float64, `decimal` → decimal256(57,28),
`datetime` → timestamp(us, UTC), `timespan` → duration(ns), `bool` → boolean,
and `string`/`guid` → string. `dynamic` becomes JSON text. Unknown types fail
explicitly; nulls stay null. Decimal values exceeding the fixed precision or
scale fail rather than round silently.
