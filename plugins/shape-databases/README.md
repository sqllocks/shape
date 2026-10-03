# sqllocks-shape-databases

Shape plugin: write generated tables straight into a running database, or a local DuckDB file
(`shape.sinks`). Three sinks live here. `postgres` and `mysql` match the `postgres` and `mysql`
dialects of the built-in `sql` script sink (same quoting, same Arrow-to-database type map);
`duckdb` writes Arrow batches into a DuckDB file:

| sink | URI | how rows are sent |
|---|---|---|
| `postgres` | `postgresql://user@host:5432/db?sslmode=require` (also `postgres://`) | `COPY ... FROM STDIN` (psycopg 3) |
| `mysql` | `mysql://user@host:3306/db?ssl_ca=/path/ca.pem` | batched multi-row `INSERT` through bound parameters (PyMySQL) |
| `duckdb` | `duckdb:///PATH.duckdb[?schema=main]` | Arrow batches scanned by DuckDB directly, `INSERT ... SELECT` (DuckDB) |

```
pip install 'sqllocks-shape[postgres]'    # psycopg 3
pip install 'sqllocks-shape[mysql]'       # PyMySQL
pip install 'sqllocks-shape[databases]'   # both
pip install 'sqllocks-shape[duckdb]'      # DuckDB
```

The client libraries are extras of this distribution and are imported only when a connection
is opened, so core and the rest of Shape never carry them. A sink without its driver fails with
the `pip install` line above.

## Writing

```python
from shape_databases import PostgresSink

PostgresSink().write(
    "postgresql://shape@db.example:5432/shape?sslmode=verify-full",
    "customer",
    batches,  # an iterable of pyarrow RecordBatches
    write_mode="create",
    commit_rows=50_000,
)
```

Batches are consumed one at a time and rows go straight to the server, so memory does not grow
with the length of the stream.

| option | meaning |
|---|---|
| `write_mode` | `create` (default: create the table, an existing table is an error and is never touched), `append` (add rows, create if missing), `truncate` (empty then add, create if missing), `replace` (drop and create again) |
| `schema_name` | PostgreSQL schema / MySQL database to write into (created when missing) |
| `table_prefix` | text put in front of the table name |
| `batch_size` | rows converted per step (default 5000); for MySQL, rows per `INSERT` |
| `commit_rows` | commit every N rows so readers see rows while a stream runs (see below) |
| `columns`, `primary_key`, `schema` | as for the `sql` sink: per-column `{type, nullable, max_length, precision, scale}`, key columns, and the Arrow schema for creating an empty table from no batches |
| `password`, `credential`, `token_scope` | see Credentials |

A missing table is created from the Arrow schema with the `sql` sink's types (`BIGINT`,
`VARCHAR(n)`, `NUMERIC`/`DECIMAL`, `TIMESTAMP`/`DATETIME(6)`, `BOOLEAN`/`TINYINT(1)`, `BYTEA`/`LONGBLOB`,
...). String columns are sized to the first batch (at least 255); more than 4000 characters, and
nested list/struct values (written as JSON text), become `TEXT` / `LONGTEXT`. A later, longer
value fails the write instead of being truncated. Timestamps with a zone are stored as UTC.

All names (table, schema, columns) are checked and quoted: a hostile name stays one identifier,
a name the database would truncate (more than 63 bytes in PostgreSQL, 64 characters in MySQL) or
misread is refused before any connection is made. Values are never part of a statement.

### Transactions

* PostgreSQL: by default the table, a `replace`'s drop and all rows are **one transaction**: a
  failure leaves the database as it was. With `commit_rows=N` each N rows are one `COPY` and one
  commit, and the table is committed before the first rows.
* MySQL commits implicitly at DDL, so creation cannot be rolled back with the rows: by default
  the rows are one transaction and a table this call created is dropped again on failure
  (`replace` has already dropped the old table, `truncate` has already emptied it). `commit_rows`
  commits every N rows.
* With `commit_rows`, a failure rolls back only the uncommitted rows. The committed rows stay, and
  `WriteError.rows_committed` says how many.
* MySQL cannot store NaN or infinity: they are written as NULL (as the script dialect does).
* `LOAD DATA LOCAL INFILE` is deliberately not used: servers often disable it and enabling it on
  the client allows server-driven file reads.

## DuckDB (`duckdb://`)

```python
from shape_databases import DuckDbSink

DuckDbSink().write(
    "duckdb:///out/retail.duckdb?schema=raw",
    "customer",
    batches,
    write_mode="upsert",
    primary_key=["customer_id"],
    commit_rows=50_000,
)
```

`duckdb:///PATH.duckdb` is relative to the working directory, `duckdb:////abs/path.duckdb`
absolute (Windows: `duckdb:///C:/data/x.duckdb`); a host, a password, no path and `:memory:` are
refused. `?schema=` (or `schema_name=`) is the DuckDB schema, created when missing in a step of its
own. Each Arrow batch is scanned by DuckDB as an Arrow table: no value is converted in Python.

| Arrow type | DuckDB column |
|---|---|
| bool | `BOOLEAN` |
| int8 / 16 / 32 / 64 | `TINYINT` / `SMALLINT` / `INTEGER` / `BIGINT` |
| uint8 / 16 / 32 / 64 | `UTINYINT` / `USMALLINT` / `UINTEGER` / `UBIGINT` |
| float32 / float64 (float16 as float32) | `FLOAT` / `DOUBLE` |
| decimal128(p, s), p at most 38 | `DECIMAL(p,s)` |
| string, large_string, dictionary | `VARCHAR`; `UUID` when the `columns` entry says `"type": "uuid"` |
| binary, large_binary, fixed binary | `BLOB` |
| date32 / date64 | `DATE` |
| time32 / time64 | `TIME` (nanoseconds cut to microseconds) |
| timestamp, no zone: s / ms / us / ns | `TIMESTAMP_S` / `TIMESTAMP_MS` / `TIMESTAMP` / `TIMESTAMP_NS` |
| timestamp with a zone | `TIMESTAMPTZ` |

Nested values and durations are refused with the column named. `primary_key` becomes a `PRIMARY KEY`;
a key column, or one whose `columns` entry says `"nullable": false`, is `NOT NULL`.

| option | meaning |
|---|---|
| `write_mode` (or `?write_mode=`; the option wins) | `create` (default: an existing table is an error), `append` (create if missing), `truncate` (`DELETE`, then add; create if missing), `replace` (drop and create), `upsert` (`INSERT OR REPLACE` on the primary key; none: `upsert needs a primary key on <table>`) |
| `commit_rows` | commit every N rows (rounded up to a batch) so another connection sees them |
| `schema_name`, `columns`, `primary_key`, `schema` | as above, as for the other sinks |

One table is one transaction: a failure rolls back the rows, the table this call created and a
`replace`'s drop; with `commit_rows` only the open chunk is lost (`WriteError.rows_committed` counts
what stays). Tables of a run are written at the same time, each on its own connection. A file that
another process holds locked is a `ShapeError` (exit 2) with DuckDB's message. Reading the tables
back gives the same `shape.repro.dataset_id` as the generated tables. DuckDB is imported when a write
starts, never at import time, and is never a core dependency.

## Credentials

A password is never accepted in the URI (it would reach logs and shell history): a URI
with one is refused. Where it comes from, first match wins:

1. the `password` option: the secret, or a reference `env://NAME` or `file://PATH`
   (the file must be readable only by its owner, `chmod 600`);
2. the `credential` option: an object with `get_token(scope)` returning something with `.token`
   (every `azure-identity` credential, for Microsoft Entra database logins), a function
   `scope -> token`, or an `env://` / `file://` reference; the token is the password
   (`token_scope` overrides the default `https://ossrh-postgresql.azure.com/.default`);
3. the environment: `SHAPE_POSTGRES_PASSWORD`, then `PGPASSWORD`; `SHAPE_MYSQL_PASSWORD`, then
   `MYSQL_PWD`;
4. none: the driver's default (PostgreSQL reads `~/.pgpass`).

A password or token never appears in an exception message, a log record or a `repr`: errors from
the driver are scrubbed before they are reported, and are raised without the driver's own
exception attached.

## Testing

Contract tests run on every pull request against an in-memory server
(`shape_databases.testing.FakeServer`) that records every statement, every `COPY` row and every
`INSERT` parameter list. `pytest -m emulator plugins/shape-databases/tests` runs the end-to-end
tests against real PostgreSQL 16 and MySQL 8.4 (`docker compose -f ci/emulators/docker-compose.yml
up -d --wait postgres mysql`, with `SHAPE_POSTGRES_PASSWORD` / `SHAPE_MYSQL_PASSWORD` set to
`shape_emulator`), nightly in CI. The DuckDB sink needs no server: its tests write real DuckDB files.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository.
