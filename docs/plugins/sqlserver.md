# SQL Server, Azure SQL and Fabric SQL (`shape-sqlserver`)

`sqllocks-shape-sqlserver` profiles a relational database and reads its tables. It talks to
SQL Server 2016 and later, Azure SQL Database, Azure SQL Managed Instance, Fabric Warehouse and
Fabric SQL database through `pyodbc`.

## Install

```bash
pip install 'sqllocks-shape[sqlserver]'         # the plugin and pyodbc
pip install 'sqllocks-shape-sqlserver[entra]'   # adds azure-identity for --auth cli|msi|spn
```

`pyodbc` needs the **Microsoft ODBC Driver 18 for SQL Server** (and unixODBC on Linux).
Nothing is imported until a connection is opened, so `shape plugins doctor` is clean without
them. `shape plugins list` shows `shape.sources:mssql` and `shape.commands:profile-db`.

## Profile a database

```bash
shape profile-db --server myserver.database.windows.net --database shop \
    --schema dbo --sample-rows 1000 -o shop.shape --json shop-summary.json
```

| Option | Meaning |
|---|---|
| `--connection-string ODBC` | A full ODBC connection string, or set `SHAPE_SQLSERVER_CONNECTION_STRING` (keeps secrets out of shell history). |
| `--server`, `--database` | Build the connection string for you (encrypted, certificate validated). |
| `--auth` | `cli` (default): the Azure CLI's signed-in account. `msi`: managed identity or the default Azure credential chain. `spn`: service principal (`--tenant-id`, `--client-id`, secret in `SHAPE_SQLSERVER_CLIENT_SECRET` or `--client-secret`). `fabric`: the token of the running Fabric notebook. `sql`: the login in the connection string. |
| `--schema` | Schema to profile (default `dbo`). |
| `--tables A,B` | Only these tables. |
| `--sample-rows N` | Rows sampled per table (default 1000). `0` profiles the catalog only. |
| `-o OUT.shape`, `--json SUMMARY.json` | Where to write the profile and its summary. |

Exit codes: `0` done; `2` bad input or a failed connection (the message never contains the
password or token).

From Python:

```python
import shape
from shape_sqlserver import Credentials, profile_database

prof = profile_database(
    "Driver={ODBC Driver 18 for SQL Server};Server=...;Database=shop",
    credentials=Credentials("cli"),
    schema="dbo",
    sample_rows=1000,
)
shape.save(prof, "shop.shape")
```

`profile_database(connection=conn)` reuses an open connection and leaves it open (for example
the one a Fabric user data function hands you).

### What is exact and what is sampled

- **Exact, from the catalog:** the tables, columns and SQL types, primary keys (including
  composite ones), declared foreign keys, and the row count of every table.
- **Sampled:** per column, `null_count`, `cardinality`, the enumeration of low-cardinality text
  and boolean columns (with observed frequencies), and for numeric columns `min_value`,
  `max_value`, `mean` and `std`. The sample is the first `--sample-rows` rows the server
  returns (`SELECT TOP n`).
- **An enumeration** is a sampled column with at most 50 distinct values, or distinct values under
  5% of the table's rows, whose values also repeat: distinct values are at most half of the
  sample's non-null values. A unique column, a tiny table of distinct text and free text are
  never enumerations.
- **Ratios use the table's row count** as the denominator: `null_rate = sampled nulls / table
  rows`, `cardinality_ratio = sampled distinct values / table rows`. For a table larger than the
  sample both are therefore lower than the true rates, and `is_unique` (ratio above 0.99) is
  only true for tables that fit in the sample. Raise `--sample-rows` to the table size to get
  true rates.
- A table that cannot be read (no `SELECT` permission) still gets its catalog profile.
- Distribution fitting, patterns and quantiles are not computed here (those fields are null).

### Tables without declared keys

A Fabric warehouse enforces no primary or foreign keys. When a table declares none, its key is
the identity column, else a column named like `<table>_id`, `<table>_key` or `id`, else any
column ending in `id` or `key`, else the first column. When no foreign key is declared in the
schema and the sampled data suggests none, `*_id` and `*_key` columns are linked to a table of
the matching name (`orders.customer_id` to `customer`, or to `dimcustomer`) and listed in
`detected_fks` and `relationships`.

## Read a table

`mssql://` is a `shape.sources` plugin: one table as Arrow record batches.

```python
from shape_sqlserver import SqlServerSource

src = SqlServerSource()
uri = "mssql://myserver.database.windows.net/shop?schema=dbo&table=orders"
schema = src.schema(uri, auth="cli")
for batch in src.read(uri, auth="cli", batch_size=65536):
    ...
```

The URI never carries a secret. Options: `connection` (an open connection, left open),
`connection_string`, `auth` or `credentials`, `user` and `password` (a SQL login),
`trust_server_certificate`, `batch_size`. A table with a primary key is read in key order, so
reading twice gives the same data. `datetimeoffset` arrives as UTC timestamps, `money` and
`decimal` as `decimal128`, `uniqueidentifier` as text.

## For plugin authors

`shape_sqlserver.sql` and `shape_sqlserver.auth` are meant to be shared (the Fabric plugin
uses them): `quote_ident` and `qualified_name` (always quote identifiers; `]` is doubled),
`build_connection_string`, `redact_connection_string` (use it on anything shown to a user),
the catalog queries, `sql_type_to_dtype` and `sql_type_to_arrow`, `Credentials`, `connect` and
`access_token`. `shape_sqlserver.testing` has an in-memory `FakeConnection` that answers the
catalog queries and the sample reads, with deterministic databases (`scenario("retail")`),
so contract tests need no server.

## Testing

- Contract tests (`pytest -m "not emulator and not live"`) run everything against the
  in-memory server.
- `pytest -m emulator plugins/shape-sqlserver/tests` runs against a real SQL Server 2022
  (`docker compose -f ci/emulators/docker-compose.yml up -d --wait mssql`, with
  `msodbcsql18` installed). CI runs it nightly.
