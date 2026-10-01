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
  returns (`SELECT TOP n`), so a table stored in an order (by date, say) is sampled from its
  start.
- **Ratios use the rows actually sampled** as the denominator: `null_rate = sampled nulls /
  sampled rows`, `cardinality_ratio = sampled distinct values / sampled rows`, and `is_unique` is
  true when that ratio is above 0.99. `is_enum` follows the same ratio (at most 50 distinct
  values, or under 5% of the sampled rows). So all of these describe the sample: `is_unique`
  can be true for a table larger than the sample, because the sampled rows were distinct, and it
  says nothing about rows that were not read. Raise `--sample-rows` to the table size to cover
  the whole table.
- **The profile says so.** Every table has `sampled_rows` (the rows read: the smaller of
  `--sample-rows` and the table, `0` for `--sample-rows 0` or a table that could not be read),
  and the dataset has a `sampling` entry (`method`, `requested_rows` and a note naming the
  fields that describe the sample). `row_count` stays the catalog's count of the whole table.
- A table that cannot be read (no `SELECT` permission) still gets its catalog profile.
- Distribution fitting, patterns and quantiles are not computed here (those fields are null).

### Tables without declared keys

A Fabric warehouse enforces no primary or foreign keys. When a table declares none, its key is
the identity column, else a column named like `<table>_id`, `<table>_key` or `id`, else any
column ending in `id` or `key`, else the first column. (`<table>` is the table's name without a
leading `dim` or `fact`, so `dimcustomer` gives `customer`; `dim` or `fact` elsewhere in a name
is part of the name.)

Foreign keys declared in the schema are authoritative: when the schema declares any, those are
the relationships and nothing is inferred beside them. When it declares none, foreign keys come
from two kinds of evidence, in this order:

1. **The sampled data.** Profiling the sampled tables together reports a `*_id` column whose
   values (nearly) all exist in the key of the table it is named after
   (`orders.customer_id` into `customer`).
2. **Column names.** For every column the data did not link, a `*_id`, `*id` or `*_key` column is
   linked to a table of the matching name (`orders.customer_id` to `customer`, or to
   `dimcustomer`), even when the sample is empty or shows no match.

A column the data links keeps the data's link; a name never replaces it. Every link, from
either source, is listed in `relationships`, in the table's `detected_fks`, and marks its
column with `is_foreign_key` and `fk_ref_table`, exactly as a declared key does. Data-based links
come first in `relationships`.

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
