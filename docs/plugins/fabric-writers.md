# Fabric writers and the `onelake://` source (`sqllocks-shape-fabric`)

Writers for Lakehouse files, SQL databases, Warehouses (`COPY INTO`), Eventhouses and Eventstreams,
and a source that reads lakehouse tables and files by `onelake://` URI. The API and the write modes
are described in the plugin's README (`plugins/shape-fabric/README.md`).

Safety rules the writers follow: the default `write_mode` is `create`, which never touches an existing
table; identifiers are validated and quoted; values are driver parameters; the `COPY INTO` location is
checked against a strict character set; errors never show passwords or tokens; a failed `create` or
`append` write rolls back its table, and staged files and partial files are removed.

**`truncate` and `replace` commit before the insert.** The emptying (`truncate`) or the drop and new
`CREATE TABLE` (`replace`) is committed first, then the rows are written. If the write then fails,
a `truncate` target is left empty and a `replace` target is left absent, and the old rows are gone
either way; only `create` and `append` roll back to the state they started from. This applies to
the SQL database and Warehouse writers and to the `sqlserver` and `warehouse` sinks. Keep a copy of
a table you cannot regenerate before using either mode.

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
| `?write_mode=`, `write_mode=` | `create` (default; an existing table is an error), `append`, `truncate`, `replace`. `truncate` and `replace` commit before the insert: a failed write leaves the table empty (`truncate`) or absent (`replace`) |
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
