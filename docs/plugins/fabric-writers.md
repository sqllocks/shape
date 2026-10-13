# Fabric writers and the `onelake://` source (`sqllocks-shape-fabric`)

Status: experimental.

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
For a schema-enabled Lakehouse, read `.../Tables/<schema>/<table>` instead. The `lakehouse`
writer and `shape publish -t lakehouse` write a Files landing zone; they do not register managed
Lakehouse tables. Use the Delta sink with the [appropriate table layout](cloud-sources.md#lakehouse-table-layouts)
or load the landing files in Fabric. Notebook deployment also generates Parquet under Files.
The Delta sink and the Spark mode ask Fabric which kind of Lakehouse the target is (a
schema-enabled one reports `defaultSchema`) and write `Tables/<schema>/<table>` (schema `dbo`
unless `--schema-name` / `--lakehouse-schema` says otherwise) or `Tables/<table>`. Tables outside a
schema folder of a schema-enabled Lakehouse are invisible to its SQL endpoint and semantic models,
so the kind is never guessed: `--sink-config delta.schema_enabled=true|false` states it, and a
target that cannot be asked (names instead of ids, no identity) fails with that setting named. A
location given together with raw `storage_options` tokens and no identity to ask with is used as
written: put the schema in it (`.../Tables/dbo`).

Warehouse and Synapse primary keys are informational: table creation is followed by
`ALTER TABLE ... ADD CONSTRAINT ... PRIMARY KEY NONCLUSTERED (...) NOT ENFORCED` in the same
DDL batch. SQL Server and Azure SQL keep their enforced inline keys. Staging upload and
cleanup use one cached filesystem with the same credential, including after a failed load;
an unrelated ambient storage key cannot replace an explicit identity.

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

## Warehouse OneLake storage-token failures

Warehouse `COPY INTO` reads OneLake under the SQL connection's Microsoft Entra
identity by default. Shape's client-side credential uploads the staging files;
it cannot supply a storage token to the Warehouse's server-side reader.
Microsoft documents GUID-based OneLake source URLs and the permissions needed
for Microsoft Entra or an explicitly configured Workspace Identity. A Workspace
Identity is a separate Fabric configuration, so Shape does not switch to it
automatically. See [the Fabric COPY authentication requirements](https://learn.microsoft.com/en-us/sql/t-sql/statements/copy-into-transact-sql?view=fabric).

When the Warehouse reports SQL error **13840** while obtaining the OneLake
storage token, Shape warns and loads the already staged Parquet through
parameterized SQL `INSERT` on the existing connection. This path consumes at most
5,000 rows per batch and has lower throughput than `COPY INTO`. Shape rolls back
the failed COPY transaction, reapplies an uncommitted truncate or replace,
verifies the number of rows loaded, and removes staging files on success or
failure. Primary keys retain their informational Warehouse behavior. Other COPY
errors fail the write; they do not activate this recovery path. A service
principal still needs permission to write the Warehouse and read and write the
staging Lakehouse.

## `synapse://`: a Synapse dedicated SQL pool

`synapse` (`shape.sinks`, `shape_fabric.SynapseSink`; the writer is `shape_fabric.SynapseWriter`)
writes a table into a **dedicated SQL pool** of an Azure Synapse workspace. It follows the Warehouse
writer: it prepares the table with the same T-SQL helpers (same `write_mode` values, the safe `create`
default, `schema_name`, `columns`, `primary_key`), stages the rows as Parquet files of at most
`chunk_rows` rows (default 1,000,000) under `<staging_path>/staging/<run>/<table>/`, runs **one**
`COPY INTO ... WITH (FILE_TYPE = 'PARQUET')` over that folder, checks that the number of rows it
loaded equals the number staged (otherwise the write fails and a table this call created is dropped),
and deletes the staged files, also when a step fails.

Use [the tested starters](../TUTORIAL.md) for local commands and complete output.

<!-- example: 1 -->

**Needs a Fabric account. Not run in CI.**

```
shape generate retail --to synapse://myws.sql.azuresynapse.net/pool1 \
  --auth cli --sink-config synapse.staging_path=abfss://stage@myacct.dfs.core.windows.net/shape
```

<!-- owner: Fabric maintainer — supply the transcript for docs/plugins/fabric-writers.md example 1. -->


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

## Time limits

Every SQL connection the Fabric writers open (`SqlDatabaseWriter`, `WarehouseWriter`,
`SynapseWriter`) gives each statement a deadline, so a stalled load fails with the driver's timeout
message instead of waiting for ever. `SHAPE_SQL_QUERY_TIMEOUT` sets it in seconds (default 900).
`SHAPE_SQL_QUERY_TIMEOUT` does **not** cover `shape profile-db`, which connects through the SQL
Server plugin (see [sqlserver.md](sqlserver.md)).

When `COPY INTO` fails with error 13840 and the Warehouse writer falls back to parameterized
`INSERT` (see [Known limitations](../KNOWN_LIMITATIONS.md)), one table may spend at most
`SHAPE_WAREHOUSE_INSERT_SECONDS` seconds there (default 600). A larger table stops with an error
that names the cause and the rows already sent, and the write is rolled back.

## Unattended live tests

Run `pytest -m live plugins/shape-fabric/tests/test_live_owner_matrix.py` for the Owner matrix,
and the other `test_live*.py` modules for emitters, deployed notebooks and scale routing.
Missing credentials fail with the variable name (P6-07a and LIVE-FIX #15). Service errors and failed assertions fail;
these tests never use interactive sign-in or xfail. Local contract tests do not replace this
live validation.

Set `FABRIC_TENANT_ID`, `FABRIC_CLIENT_ID` and `FABRIC_CLIENT_SECRET` for a service principal
in the target tenant. Grant it access to each target workspace/item, OneLake read/write/delete
permissions, and SQL permissions to create, load, read and drop the test tables and schemas.
Use dedicated test items: the tests create unique names and remove what they create.

| Cases | Additional variables |
|---|---|
| Classic Lakehouse Delta, Files, prefix and `onelake://` read-back | `FABRIC_CLASSIC_LAKEHOUSE_TABLES_PATH=onelake://<workspace>/<lakehouse>/Tables` |
| Schema-enabled Lakehouse Delta, Files, prefix and read-back | `FABRIC_SCHEMA_LAKEHOUSE_TABLES_PATH` in the same root form; `FABRIC_LAKEHOUSE_SCHEMA` names an existing schema (default `dbo`) |
| Warehouse COPY with/without keys, failed-load cleanup, publish | `FABRIC_WAREHOUSE_CONNECTION_STRING` (ODBC, no password), `FABRIC_STAGING_PATH` (OneLake Files folder) |
| Fabric SQL database write and `profile-db` | `FABRIC_SQL_CONNECTION_STRING` (ODBC, no password) |
| `profile-db` on the sample Warehouse | `FABRIC_SAMPLE_WAREHOUSE_CONNECTION_STRING`; optional `FABRIC_SAMPLE_WAREHOUSE_SCHEMA` (default `dbo`) |
| Publish to Lakehouse Files | `FABRIC_STAGING_PATH`, in `test_live_commands.py`; Files layout is independent of Lakehouse schemas |
| Eventhouse emit and ingestion/read-back | `FABRIC_EVENTHOUSE_URI`, `FABRIC_EVENTHOUSE_TOKEN` for the existing token tests; the token must grant ingestion, query and table management |
| Eventstream emit/write | `FABRIC_EVENTSTREAM_CONNECTION_STRING` for its custom Event Hubs-compatible endpoint; the endpoint connection string replaces SPN authentication |
| Notebook deploy and setup | `FABRIC_WORKSPACE_ID`, plus the common SPN variables, in `test_live_commands.py` |
| Spark worker | `FABRIC_WORKSPACE_ID`, `FABRIC_LAKEHOUSE_ID`, plus common SPN variables; set `FABRIC_LAKEHOUSE_SCHEMA` when the test Lakehouse has schemas enabled |
| Synapse dedicated-pool round trip | `SHAPE_TEST_SYNAPSE_URI`, `SHAPE_TEST_SYNAPSE_STAGING`, `SHAPE_TEST_SYNAPSE_AUTH=spn`, `SHAPE_TEST_SYNAPSE_TENANT_ID`, `SHAPE_TEST_SYNAPSE_CLIENT_ID`, `SHAPE_TEST_SYNAPSE_CLIENT_SECRET` |

### The `fabric-live` workflow

`.github/workflows/fabric-live.yml` runs these tests nightly and on demand
(`gh workflow run fabric-live.yml -f ref=<branch>`), as the service principal `shape-ci-fabric`, with
the values held by the `fabric-live` environment. Each test is printed as it starts, a test that
stays 10 minutes dumps every thread's stack, the step is capped at 50 minutes, and a failed test
prints its failure at once. After the tests the run lists the items left in the workspace and, for
the Git workspace, Fabric's answers to `git/connection`, `git/status` and `git/myGitCredentials`
(read-only). Two dispatch inputs, both off by default, do one-time setup:

| Input | What it does |
|---|---|
| `set_git_credentials` | As the service principal, sets its own Git credentials on the Git workspace (`FABRIC_GIT_WORKSPACE_ID`) to the existing GitHub connection `shape-fabric-sync-gh`: `PATCH .../git/myGitCredentials` with `ConfiguredConnection`. It reads the credentials first and changes nothing when they already point at that connection. It prints only the source and the connection id. Timeout: 5 minutes. |
| `delete_leftover_notebooks` | Lists the Notebook items of the test workspace with their ids, then deletes only those named `shape_live_nb_` plus 8 hex digits (what the deploy-notebook test names its notebook). Nothing else is deleted. Timeout: 5 minutes. |

Only one run waits behind a running one: dispatch a run when none is pending.

The Owner matrix needs two distinct Lakehouse items and a sample Warehouse:

- `FABRIC_CLASSIC_LAKEHOUSE_TABLES_PATH` is the root Tables URI of a **classic,
  schema-less Lakehouse**, for example
  `onelake://<workspace-guid>/<classic-lakehouse-guid>/Tables`.
- `FABRIC_SCHEMA_LAKEHOUSE_TABLES_PATH` is the root Tables URI of a **schema-enabled
  Lakehouse**, for example `onelake://<workspace-guid>/<schema-lakehouse-guid>/Tables`.
  Do not append `/dbo` or a table name. The matrix selects `dbo`, or the existing
  schema named by `FABRIC_LAKEHOUSE_SCHEMA`, when writing and reading its tables.
- `FABRIC_SAMPLE_WAREHOUSE_CONNECTION_STRING` names a **Fabric Warehouse containing
  sample tables**, for example
  `Data Source=<sql-endpoint>.datawarehouse.fabric.microsoft.com;Initial Catalog=<sample-warehouse-name>`.
  Copy the SQL endpoint host from that Warehouse's settings. Do not include a
  password, `UID` or `Authentication`: the common service-principal variables
  provide the token. The Microsoft ODBC Driver 18 must be installed; Shape adds
  the driver when it is absent from the string. The matrix profiles `dbo`, or
  `FABRIC_SAMPLE_WAREHOUSE_SCHEMA` if set. Grant the principal permission to read
  its tables and catalog metadata.

`profile-db` receives its connection and secret through `SHAPE_SQLSERVER_CONNECTION_STRING`
and `SHAPE_SQLSERVER_CLIENT_SECRET`; the matrix sets these from the Fabric variables for each
test. No secret is placed in argv. `SHAPE_RECORD_DIR` optionally records scrubbed live tapes
in the existing writer/command tests. Do not commit unreviewed live recordings.
