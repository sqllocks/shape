# Fabric writers and the `onelake://` source (`sqllocks-shape-fabric`)

Writers for Lakehouse files, SQL databases, Warehouses and Synapse dedicated SQL pools (`COPY INTO`),
Eventhouses and Eventstreams,
and a source that reads lakehouse tables and files by `onelake://` URI. The API and the write modes
are described in the plugin's README (`plugins/shape-fabric/README.md`).

Safety rules the writers follow: the default `write_mode` is `create`, which never touches an existing
table; identifiers are validated and quoted; values are driver parameters; the `COPY INTO` location is
checked against a strict character set; errors never show passwords or tokens; a failed write rolls
back its table, and staged files and partial files are removed.

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
| `?write_mode=`, `write_mode=` | `create` (default; an existing table is an error), `append`, `truncate`, `replace` |
| `?batch_size=`, `batch_size=` | rows per round trip (default 5,000) |
| `?commit_rows=`, `commit_rows=` | commit every N rows while the batches are consumed, so readers see rows as they arrive; the default is one transaction per call (a failure rolls everything back). With `commit_rows` a failure rolls back only the open chunk and keeps what was committed |
| `credential=` | Microsoft Entra sign-in; without it the login is `user` and `password` |
| `connection_string=`, `connection=` | an ODBC/ADO.NET connection string, or an open DB-API connection (never closed by the sink), instead of the URI's host and database |
| `driver`, `encrypt`, `trust_server_certificate`, `timeout` | connection settings (query or option) |

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
