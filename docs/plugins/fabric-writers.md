# Fabric writers and the `onelake://` source (`sqllocks-shape-fabric`)

Writers for Lakehouse files, SQL databases, Warehouses (`COPY INTO`), Eventhouses and Eventstreams,
and a source that reads lakehouse tables and files by `onelake://` URI. The API and the write modes
are described in the plugin's README (`plugins/shape-fabric/README.md`).

Safety rules the writers follow: the default `write_mode` is `create`, which never touches an existing
table; identifiers are validated and quoted; values are driver parameters; the `COPY INTO` location is
checked against a strict character set; errors never show passwords or tokens; a failed write rolls
back its table, and staged files and partial files are removed.

The `onelake://<workspace>/<lakehouse>/Tables/<table>` form reads a Delta table and
`.../Files/<path>` reads files, through the core `abfss://` source (see [cloud-sources.md](cloud-sources.md)).

## `mssql://`: a live SQL Server, Azure SQL or Fabric SQL sink

`shape-fabric` registers two `shape.sinks` entries: `sqlserver` (URI schemes `mssql` and
`sqlserver`, bulk insert into a live database) and `warehouse` (`warehouse://`, `COPY INTO`). The
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
| `?write_mode=`, `write_mode=` | `create` (default; an existing table is an error), `append`, `truncate`, `replace`, `upsert` (below) |
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
