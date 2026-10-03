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
| `--tables A,B` | Only these tables. A name the schema does not have, or a schema with no tables, is an error (exit 2) that lists the schema's tables, never an empty profile. |
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

- **Exact, from the catalog:** the tables, columns and SQL types, declared primary keys
  (including composite ones), declared foreign keys, and the row count of every table.
- **Sampled:** per column, `null_count`, `cardinality`, the enumeration of low-cardinality text
  and boolean columns (with observed frequencies), and for numeric columns `min_value`,
  `max_value`, `mean` and `std`.

#### Which rows are sampled

`--sample-rows n` (default 1000) reads **n rows spread over the whole table**, not the first n.
A table stored in an order (by date, say, or by an identity key) is therefore not sampled from
its start. The choice is deterministic: the same data gives the same rows on every run.

- A table with no more than n rows is read whole (`SELECT TOP n *`).
- A larger table is read with
  `SELECT TOP n * FROM t ORDER BY (CAST(CHECKSUM(<key>) AS bigint) * 1327217885) % 2147483647, <key>`.
  `<key>` is the declared primary key. A table with no declared key (a heap, a Fabric
  warehouse) hashes every column the server can hash (not `text`, `ntext`, `image`, `xml`,
  spatial, `hierarchyid` or `sql_variant`), so duplicate key values do not cluster the sample.
  `CHECKSUM` alone would not do: for an integer it returns the integer, so the order would still
  be the key order and the sample the lowest keys. The multiplication scrambles it, so
  consecutive keys land far apart in the order and the n smallest are spread evenly over the
  table. Hash collisions are settled by the key; on a heap two different rows with the same
  hash at the cut-off could in principle be picked differently from run to run, which is
  vanishingly rare.
- **Cost.** One full scan of the table per profiled table, plus a top-n sort that keeps only n
  rows in memory (nothing spills). Measured on SQL Server 2022 in a container, a 5-million-row
  table with a primary key took about 3 seconds against 4 milliseconds for the first n rows;
  it grows linearly with the table. Only `CHECKSUM`, `CAST`, `ORDER BY` and `TOP` are used, plain T-SQL
  that SQL Server 2019 and later and Azure SQL are documented to support; it was tested on
  SQL Server 2022. Azure SQL and Fabric SQL were not available to test here: if a server rejects the query, the table's first n rows are read instead, a warning is
  logged, and the profile says so (`sample_method`, below).
- Other ways were rejected: `TABLESAMPLE` samples whole pages, so a table of few pages is
  sampled coarsely and the rows depend on page layout rather than on the data alone;
  `ORDER BY NEWID()` is not repeatable. There is no seed: the rows follow from the data alone.

The profile says how it was sampled: every table has `sampled_rows` (the rows read, the smaller
of n and the table; `0` for `--sample-rows 0` or a table that could not be read) and
`sample_method` (`all rows`, `checksum spread`, `first rows (fallback)` or `none`), and the
dataset has a `sampling` entry (`method`, `requested_rows` and a note naming the fields that
describe the sample). `row_count` stays the catalog's count of the whole table. A table that
cannot be read (no `SELECT` permission) still gets its catalog profile.

#### Ratios describe the sample, and may be unknown

- `null_rate = sampled nulls / sampled rows`, `cardinality_ratio = sampled distinct values /
  sampled rows`.
- **An enumeration** (`is_enum`, with `enum_values`) is a column with at most 50 distinct values,
  or distinct values under 5% of the sampled rows, whose values also repeat: distinct values are
  at most half of the sampled non-null values, and a unique column never is one. A tiny table of
  distinct text, a key and free text are therefore not enumerations; the same rule as the core
  profiler.
- **No sampled rows means unknown, not zero.** For a column with no sampled row
  (`--sample-rows 0`, an empty table, a table that could not be read) `null_rate`,
  `cardinality_ratio` and `is_unique` are `null`, not `0.0`/`false`. Saving, loading, the HTML
  report, `summary()`, `shape.check` (a `unique` or `max_null_rate` rule it cannot judge is not
  a violation) and `shape.diff` (a rate it cannot compare is skipped) all accept `null`.
- **`is_unique` is null-aware:** the column is unique among its **non-null** sampled values
  (distinct values / non-null values above 0.99). One value proves nothing, so with fewer than 2
  non-null sampled values it is `null` (unknown): a 1-row sample no longer calls every column
  unique. It still says nothing about rows that were not read; raise `--sample-rows` to the
  table size to cover the whole table.
- Distribution fitting, patterns and quantiles are not computed here (those fields are null).

### Keys without declarations

A Fabric warehouse enforces no primary or foreign keys.

**Primary keys.** A table that declares none gets a guessed key, only when the guess is
plausible: a column that is not a foreign key (declared, shown by the data or suggested by its
name) and whose sampled rows show no null and no repeated value (with no sampled rows nothing
rules it out). Of those, the identity column wins, then a column named `id` or like the table:
`<table>_id`, `<table>_key`, and the same with the table's singular (`orders` gives `order_id`).
(`<table>` is the table's name without a leading `dim` or `fact`, so `dimcustomer` gives
`customer`; `dim` or `fact` elsewhere in a name is part of the name.) **Otherwise the table has
no primary key** (`primary_key` is empty): an arbitrary column ending in `id` would only look
authoritative.

**Foreign keys.** Every link carries its evidence, `declared`, `data` or `name`: in each
relationship's `evidence` and in the child column's `fk_evidence` (null on a column that is no
foreign key). The evidence is applied in this order:

1. **Declared** in the schema. These stay authoritative for the columns they cover and are
   never replaced or changed by inference.
2. **The sampled data.** Profiling the sampled tables together reports a `*_id` column whose
   values (nearly) all exist in the key of the table it is named after
   (`orders.customer_id` into `customer`).
3. **Column names**, for every column the first two left unlinked: a column whose name ends in a
   whole `id` or `key` word is linked to the table of the stem, or its `dim` form
   (`orders.customer_id` to `customer` or `dimcustomer`), even when the sample is empty or shows
   no match.

Inference runs beside declared keys: undeclared `*_id` columns in a schema that declares some
foreign keys are still linked, and say so (`evidence`). A column the data links keeps the data's
link; a name never replaces it. Every link is listed in `relationships` (declared ones first,
then data, then names), in the table's `detected_fks`, and marks its column with
`is_foreign_key` and `fk_ref_table`, exactly as a declared key does.

**What counts as a name.** `id` and `key` match only as a whole word of the column name,
split at `_` and at CamelCase boundaries: `customer_id`, `CustomerId`, `customerID`,
`customer_key` and `CustomerKey` give the stem `customer`; `paid`, `valid`, `monkey` and
`identity` do not match. A name written entirely in one case with no separator (`customerid`)
has no word boundary to split at, so it is not matched: declare the key, or use `_` or
CamelCase.

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
`decimal` as `decimal128`, `uniqueidentifier` as text. A column of a user-defined alias type
(`CREATE TYPE Amount FROM decimal(10, 2)`) is read and profiled as its base system type.

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
