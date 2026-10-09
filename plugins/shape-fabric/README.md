# sqllocks-shape-fabric

Shape plugin: Microsoft Fabric. It holds the two event emitters of `shape emit` (`shape.emitters`),
the `sqlserver`, `warehouse` and `synapse` sinks (`shape.sinks`), the writers behind the scale router's Fabric
sinks, `--auth` and credential references ([fabric-auth](../../docs/plugins/fabric-auth.md)), and
the commands `shape fabric publish|notebook|deploy-notebook|setup|export-model|known-answer|
check-answers|publish-report` with their top-level aliases ([fabric-commands](../../docs/plugins/fabric-commands.md)).

```
pip install sqllocks-shape-fabric                        # Lakehouse, Eventhouse, notebooks, commands
pip install 'sqllocks-shape-fabric[sqlserver,eventhubs]'  # + SQL targets and the Eventstream
```

`[sqlserver]` brings the SQL Server plugin (and `pyodbc`) for the `sqlserver`, `warehouse` and
`synapse` sinks and the SQL database writer; `[eventhubs]` brings the Event Hubs plugin for the
`eventstream` emitter and writer. Without one, that part says which extra to install. The plugins
are not on PyPI yet: install from the release's wheels with `--find-links` (docs/INSTALL.md).

## `eventstream://`: a Fabric Eventstream

Add a **custom endpoint** source to an Eventstream, copy its connection string (it ends in
`EntityPath=...`) and:

```
export SHAPE_EVENTSTREAM_CONNECTION_STRING='Endpoint=sb://...;EntityPath=es_...'
shape emit retail --realtime --rate 2000 --sink eventstream://my-eventstream
```

The custom endpoint speaks the Event Hubs protocol, so this is the Event Hubs emitter (idempotency
key `<table>/<seq>` in the property `shape_key`, at-least-once, throttling waited for) with the
Eventstream's connection string. Sign-in with Microsoft Entra is not available for a custom
endpoint.

## `eventhouse://`: a Fabric Eventhouse (KQL database)

```
export SHAPE_EVENTHOUSE_TOKEN=$(az account get-access-token --resource https://<query-uri-host> --query accessToken -o tsv)
shape emit retail --sink eventhouse://<query-uri-host>/<database>
```

Events are sent by Kusto **streaming ingestion**, a JSON-lines request per batch (under the 4 MB
limit). Each Shape table goes to the KQL table of the same name, created from its schema
(`.create-merge table`, plus a JSON ingestion mapping); put a table name last in the URI to send
everything to one table. The idempotency key is the pair of columns `_shape_table`,
`_shape_seq`. Streaming ingestion does not deduplicate, so a batch the runtime repeats after a
failure is in the table twice: read it with

```python
from shape_fabric.eventhouse import dedupe_query

dedupe_query("order_line")  # ['order_line'] | summarize take_any(*) by _shape_table, _shape_seq
```

Sign-in: `token` option, `SHAPE_EVENTHOUSE_TOKEN`, or Microsoft Entra through `azure-identity`
(`pip install 'sqllocks-shape-fabric[entra]'`). A 429/503 is waited for (`Retry-After` honoured);
400 (a malformed event, or streaming ingestion not enabled) and 401/403 stop the run.

## Writers (Python API)

Batch writers take Arrow `RecordBatch` iterators, one table per `write_table` call (or a mapping to
`write_tables`), and a `credential` (any object with `get_token(scope)`, or a function
`scope -> token`; `None` means the writer's default). A failure raises `WriteError`; its `.result`
lists the tables completed before it. Nothing is reported written unless the destination accepted it.

| writer | destination | notes |
|---|---|---|
| `LakehouseWriter(folder)` | Parquet/CSV/JSONL files in a local folder or OneLake (`abfss://`, `onelake://<ws>/<lakehouse>/Files/..`) | streamed; a file is complete or absent; landing-zone, manifest and done-flag helpers |
| `SqlDatabaseWriter(connection_string)` | Fabric SQL database, Azure SQL, SQL Server | parameterised `INSERT`s, `batch_size` rows per trip, one transaction per table |
| `WarehouseWriter(connection_string, staging_path)` | Fabric Warehouse | Parquet staged in OneLake, `COPY INTO`, rows loaded must equal rows staged, staging always removed |
| `SynapseWriter(connection_string, staging_path)` | Synapse dedicated SQL pool | Parquet staged in ADLS Gen2, `COPY INTO` (managed or signed-in identity), rows loaded must equal rows staged, staging always removed; `distribution` and `index` table options |
| `EventhouseWriter(uri)` | KQL table | the emitter's Kusto transport (retries, sign-in), a table's own columns |
| `EventstreamWriter(uri)` | Eventstream | the emitter's transport, flat events with the `_shape_table`/`_shape_seq` key |

`write_mode` (SQL, Warehouse, Eventhouse): `create` (default: an existing table is an error), `append`,
`truncate`, `replace` (drops the old table). In a SQL database and a Warehouse the `TRUNCATE` or
`DROP TABLE` commits together with the new rows, so a failed write keeps the old rows. Names are quoted and checked, values are parameters; the
one literal that cannot be a parameter (the `COPY INTO` location) is validated against a strict
character set. `shape_fabric.sinks` has `Sink`-protocol adapters; `shape_fabric.onelake` builds
OneLake paths.

Profile a lakehouse straight from OneLake: `shape profile onelake://<workspace>/<lakehouse>/Tables/<table>`
(Delta) or `.../Files/<path>`; authentication and `adlfs` handling are core's `abfss://` source.

## `mssql://`: a live SQL Server, Azure SQL or Fabric SQL sink

`shape-fabric` registers three `shape.sinks` entries: `sqlserver` (URI schemes `mssql` and
`sqlserver`, bulk insert into a live database), `warehouse` (`warehouse://`, `COPY INTO`) and
`synapse` (`synapse://<workspace>.sql.azuresynapse.net/<pool>`, `COPY INTO` a dedicated SQL pool;
see [fabric-writers](../../docs/plugins/fabric-writers.md#synapse-a-synapse-dedicated-sql-pool)). The
`sql` sink of core is different: it writes a script file and never connects.

```python
from shape.plugins.host import PluginHost

sink = PluginHost().get("shape.sinks", "sqlserver")
sink.write(
    "mssql://myserver.database.windows.net/shop?schema=dbo&write_mode=append",
    "customer",
    batches,  # batches: an iterable of Arrow RecordBatch
    credential=my_credential,  # get_token(scope) or scope -> token (Entra)
    commit_rows=50_000,  # optional: commit every 50,000 rows
)
```

| URI part or option | meaning |
|---|---|
| `mssql://[user[:password]@]host[:port]/database` | the server and database (`sqlserver://` is the same) |
| `?schema=`, `schema_name=` | SQL schema (default `dbo`; created when missing) |
| `?write_mode=`, `write_mode=` | `create` (default; an existing table is an error), `append`, `truncate`, `replace`; the `TRUNCATE` or `DROP TABLE` commits together with the new rows, so a failed write keeps the old rows |
| `?batch_size=`, `batch_size=` | rows per round trip (default 5,000) |
| `?commit_rows=`, `commit_rows=` | commit every N rows while the batches are consumed, so readers see rows as they arrive; the default is one transaction per call (a failure rolls everything back). With `commit_rows` a failure rolls back only the open chunk and keeps what was committed |
| `credential=` | Microsoft Entra sign-in; without it the login is `user` and `password` |
| `connection_string=`, `connection=` | an ODBC/ADO.NET connection string, or an open DB-API connection (never closed by the sink), instead of the URI's host and database |
| `driver`, `encrypt`, `trust_server_certificate`, `timeout` | connection settings (query or option) |

Each call writes one table and is safe to call again for each micro-batch of a stream with
`write_mode=append`. The batches are consumed one at a time; nothing is held back until the end.
A password is accepted as an option or in the URI's user information, never in the query string; no
password, token or connection string appears in an exception message or a log record, and none
should go on a command line (use an environment variable and pass it as an option, or sign in
with `--auth` and `--connection-string` on `shape generate --to mssql://...`; see the next section).

## Sign-in and credential references

`--auth cli|msi|spn|sql|device-code|fabric`, `--tenant-id`, `--client-id`, `--client-secret REF`,
`--sql-user`, `--sql-password REF` and `--connection-string` on `shape generate --scale-mode`, `shape emit`,
`shape stream`, `shape profile` and `shape jobs`; in Python `shape_fabric.auth` (`AuthSettings`,
`build_credential`, `writer_options`) builds the `credential` every writer takes. Secrets are `env://`,
`file://` (refused when others can read the file) or `kv://VAULT/SECRET` (`shape_fabric.keyvault`, Azure Key
Vault over HTTPS) references, never command-line values. See `docs/plugins/fabric-auth.md`.

## Tests

Contract tests replay recorded interactions (`tests/fixtures/*.json`, made by `python -m
shape_fabric.scenarios record <dir>`; secrets are scrubbed, including quoted, braced and JSON
values, a tape that still holds one is refused when it is written and when it is replayed, and a
test fails if a tape holds one).
Emulator tests: `test_sql_emulator.py` and `test_sqlserver_sink_emulator.py` (SQL Server), `test_kusto_emulator.py`. Live tests
(`test_live_writers.py`, nightly `fabric-live` job) need the `FABRIC_*` secrets listed in the file.

Contract tests run on every PR against in-memory fakes (`shape_fabric.testing`; the emitter
contract is `shape.streaming.emit.contract`). `pytest -m emulator plugins/shape-fabric/tests` runs
the end-to-end tests against the Event Hubs emulator (as an Eventstream stand-in) and the Kusto
emulator (`docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite eventhubs && docker
compose -f ci/emulators/docker-compose.yml up -d kusto`), nightly in CI. `pytest -m live
plugins/shape-fabric/tests` needs the owner's `FABRIC_*` secrets (`FABRIC_EVENTSTREAM_CONNECTION_STRING`;
`FABRIC_EVENTHOUSE_URI`, `FABRIC_EVENTHOUSE_TOKEN`); the nightly job runs each only where its secret
exists.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository.

## Which Fabric surfaces this plugin calls

[`docs/FABRIC_PLATFORM.md`](../../docs/FABRIC_PLATFORM.md) lists every Fabric and OneLake API, item type and Spark runtime that Shape calls, with its release stage, and describes the live Git sync check (`tests/test_live_git_sync.py`). A test keeps preview APIs out of the code.

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

**Sign-in** is the plugin's `--auth` modes ([fabric-auth](../../docs/plugins/fabric-auth.md)): `cli`, `msi`, `spn`,
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

## SQL type fidelity

SQL database writes keep integer widths (`int8` is signed `SMALLINT`), decimal `(p,s)`,
float32 `REAL` and temporal precision. Zoned SQL database timestamps use
`DATETIMEOFFSET(p)` and are normalized to UTC; Warehouse stages UTC instants in
`DATETIME2(6)`. Read-back values are UTC-aware; the original zone name is not kept.
Declared generation-schema widths are forwarded as `columns.max_length`; SQL database
strings without a width remain `NVARCHAR(MAX)`. Warehouse uses `VARCHAR(MAX)` (the service stores up to 16 MB); indexed keys remain
bounded. Synapse retains its separate columnstore-compatible string limits. See [the complete map](../../docs/SINKS.md#sql-type-fidelity-on-write).

`shape generate schema.json --to mssql://host/db --dry-run` prints the resolved DDL
without generating rows, signing in or connecting. `--json` includes the same statements
in the generation plan's `ddl` list. The SQL sink adapters' optional offline hook is
`ddl(uri, table, arrow_schema, **options)`.
