# sqllocks-shape-databases

Shape plugin: write generated tables straight into a running database, or a local DuckDB file
(`shape.sinks`). Five sinks live here. `postgres` and `mysql` match the dialects of the built-in
`sql` script sink (same quoting, same Arrow-to-database type map); `snowflake` and `databricks`
have their own type maps (see [Snowflake](#snowflake) and [Databricks](#databricks)); `duckdb`
writes Arrow batches into a DuckDB file:

| sink | URI | how rows are sent |
|---|---|---|
| `postgres` | `postgresql://user@host:5432/db?sslmode=require` (also `postgres://`) | `COPY ... FROM STDIN` (psycopg 3) |
| `mysql` | `mysql://user@host:3306/db?ssl_ca=/path/ca.pem` | batched multi-row `INSERT` through bound parameters (PyMySQL) |
| `snowflake` | `snowflake://user@account/database/schema?warehouse=WH&role=ROLE` | Parquet files `PUT` to the table stage, then one `COPY INTO` |
| `databricks` | `databricks://host/http_path?catalog=CAT&schema=SCH` | batched multi-row `INSERT` with bound parameters into Delta tables |
| `duckdb` | `duckdb:///PATH.duckdb[?schema=main]` | Arrow batches scanned by DuckDB directly, `INSERT ... SELECT` (DuckDB) |

```
pip install 'sqllocks-shape[postgres]'                      # psycopg 3
pip install 'sqllocks-shape[mysql]'                         # PyMySQL
pip install 'sqllocks-shape[databases]'                     # both
pip install 'sqllocks-shape[duckdb]'                        # DuckDB
pip install 'sqllocks-shape-databases[snowflake]'           # snowflake-connector-python
pip install 'sqllocks-shape-databases[databricks]'          # databricks-sql-connector
```

The client libraries are extras of this distribution and are imported only when a connection
is opened, so core and the rest of Shape never carry them (the Snowflake and Databricks drivers
are extras of this plugin only, not of core). A sink without its driver fails with the
`pip install` line above, exit 2 on the command line.

## TLS by default

Like the SQL Server sink (`Encrypt=yes`), these sinks verify TLS for a host that is not loopback:

- PostgreSQL uses `sslmode=verify-full`. Point `sslrootcert` at your CA file when the system store
  does not hold it.
- MySQL uses TLS with certificate and host name verification: the system CA store, or `ssl_ca`.

`localhost`, `127.0.0.0/8`, `::1` and Unix sockets keep the driver's defaults, so local and emulator
use is unchanged. Opt out only in the URI: `sslmode=disable` (or `prefer`) for PostgreSQL;
`ssl=false` or `ssl-mode=DISABLED` for MySQL. Setting `sslmode` yourself, or MySQL's
`ssl_verify_cert` / `ssl_verify_identity`, is taken as given. A TLS failure raises an error that
names the host and the opt-out.

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

## Snowflake

```
shape generate retail --to 'snowflake://loader@myorg-myaccount/SHAPE_DB/PUBLIC?warehouse=WH&role=LOADER' \
  --sink-config snowflake.private_key=file:///home/me/.snowflake/rsa_key.p8
```

URI: `snowflake://<user>@<account>/<database>/<schema>?warehouse=WH&role=ROLE`. The account is
the account identifier (`org-account`, or `locator.region`); `schema` may be left out, `warehouse`
and `role` are optional. `schema_name` (an option) writes to another schema of the database.

**How rows go in.** Each table is cut into Parquet files of at most `chunk_rows` rows (default
1,000,000) in a private temporary folder; every file is uploaded with `PUT` to the table's own
internal stage (`@%"TABLE"`), and then one statement loads them all:

```
COPY INTO "TABLE" FROM @%"TABLE" FILE_FORMAT = (TYPE = PARQUET)
    MATCH_BY_COLUMN_NAME = CASE_SENSITIVE PURGE = TRUE
```

The number of rows `COPY INTO` reports as loaded must equal the number staged, or the write fails
(and a table this call created is dropped, a load into an existing table rolled back). The staged
files are removed with `REMOVE` after the load, also when a step fails (a `REMOVE` that fails is a
`RuntimeWarning`, never a second error), and the local files are removed in every case. The stage is
the table's: files left by another process in it would be loaded too, and the row count check
fails the write rather than let them in silently.

**Names.** Tables, schemas and columns are created exactly as named, quoted, so case is kept: a
table `customer` is `"customer"` (not `CUSTOMER`) and is queried quoted. A name longer than 255
characters, with a control character or a leading or trailing space, is refused with the name in the
message before any connection; so are two columns of one name.

**Type map from Arrow** (a type not listed, such as `time` or `duration`, is refused before any
connection, with the column's name):

| Arrow | Snowflake |
|---|---|
| any integer (including `uint64`) | `NUMBER(38,0)` |
| `float32`, `float64` | `FLOAT` |
| `decimal128(p,s)` (p up to 38) | `NUMBER(p,s)` |
| `string`, `large_string` (dictionaries decoded) | `VARCHAR`, or `VARCHAR(n)` from `columns={"c": {"max_length": n}}` |
| `bool` | `BOOLEAN` |
| `date32`, `date64` | `DATE` |
| `timestamp` without a zone | `TIMESTAMP_NTZ` (zone-less, kept as written) |
| `timestamp` with a zone | `TIMESTAMP_TZ`, stored as UTC |
| `binary`, `large_binary`, `fixed_size_binary` | `BINARY` |
| `list`, `struct`, `map` | `VARIANT` |

Timestamps are staged in microseconds (nanoseconds are rounded to them).

**Transactions and what a failure leaves behind.** Snowflake commits DDL at once. The connection
runs with `autocommit` off, so the `COPY INTO` is one transaction: a failure rolls the rows back, and
a table this call created is dropped again. `replace` has already dropped the old table and
`truncate` has already emptied it. With `commit_rows=N` the staged rows are loaded and committed
once at least N of them are waiting at the end of an input batch (N is rounded up to whole batches,
since a `COPY INTO` is a heavy statement); the committed rows and the table stay on a failure, and
`WriteError.rows_committed` says how many.

**Credentials.** A password is never accepted in the URI. Either a key pair or a password:

1. `private_key`: a reference, `file://PATH` (readable only by its owner) or `env://NAME`, to an
   unencrypted or encrypted PEM private key (PKCS#8), with an optional `private_key_passphrase`
   (a reference or the value). On the command line `--sink-config snowflake.private_key=file://...`
   resolves the reference first, so the sink then receives the PEM text itself, which it accepts too;
   any other text is refused. Needs the `cryptography` package, which the connector installs.
2. `password`: a reference or the value (in Python), or the environment variable
   `SNOWFLAKE_PASSWORD`.

Giving both a key and a password is an error. Nothing of a key, passphrase or password appears in an
error message, a log record or a `repr`.

## Databricks

```
shape generate retail --to 'databricks://adb-123.4.azuredatabricks.net/sql/1.0/warehouses/abc123?catalog=main&schema=demo'
# DATABRICKS_TOKEN in the environment
```

URI: `databricks://<host>/<http_path>?catalog=CAT&schema=SCH`, the workspace host and the SQL
warehouse's HTTP path (its connection details show both) with `catalog` and `schema` for where
tables go (`schema_name` overrides the schema). Only SQL warehouses are supported.

**How rows go in.** Delta tables in Unity Catalog are created with `CREATE TABLE ... USING DELTA`
and filled with multi-row `INSERT` statements of `batch_size` rows (default 1,000), every value a
bound parameter (`?` markers, the connector's native parameters). Dates, timestamps and binary
values are bound as text and converted in the statement (`CAST(? AS DATE)`,
`CAST(? AS TIMESTAMP_NTZ)`, `CAST(? AS TIMESTAMP)` from UTC, `UNHEX(?)`), so they reach the table
exactly whatever the session time zone. A warehouse limits how many parameters one statement can
carry: with very wide tables lower `batch_size`.

**Names.** Unity Catalog stores table and schema names in lower case, so a name with capitals, a
space, `.`, `/` or a backtick is refused with the name in the message (use the lower-case name);
a column name with a space or one of `,;{}()=`, a tab or a newline (which Delta cannot hold) is
refused; any name over 255 characters is refused; two columns that differ only in case are
refused. All before any connection.

**Type map from Arrow** (a type not listed, such as `time` or `duration`, is refused before any
connection, with the column's name):

| Arrow | Databricks |
|---|---|
| `int8`, `int16`, `int32`, `uint8`, `uint16` | `INT` |
| `int64`, `uint32` | `BIGINT` |
| `uint64` | `DECIMAL(20,0)` |
| `float32`, `float64` | `DOUBLE` |
| `decimal128(p,s)` (p up to 38) | `DECIMAL(p,s)` |
| `string`, `large_string` (dictionaries decoded) | `STRING` |
| `bool` | `BOOLEAN` |
| `date32`, `date64` | `DATE` |
| `timestamp` without a zone | `TIMESTAMP_NTZ` |
| `timestamp` with a zone | `TIMESTAMP`, stored as UTC |
| `binary`, `large_binary`, `fixed_size_binary` | `BINARY` |
| `list`, `struct`, `map` | `STRING`, as JSON text |

**Transactions and what a failure leaves behind.** A SQL warehouse commits every statement and has
no rollback, so there is no transaction around a write. `commit_rows` is accepted and is the batch
boundary: it does not shrink statements (`batch_size` sets how many rows each carries); it makes a
statement carry no rows of the next input batch, so rows become visible batch by batch while a
stream runs. A failure drops a table this call created, unless `commit_rows` was given; an `append`
into a table that already existed keeps the rows of the statements that had run (the error says how
many, `WriteError.rows_committed`). `replace` has already dropped the old table and `truncate` has
already emptied it.

**Credentials.** A token is never accepted in the URI, and the URI has no user part. Either:

1. a personal access token: the `token` option (a reference `env://` / `file://`, or the value in
   Python) or `DATABRICKS_TOKEN`;
2. OAuth machine-to-machine for a service principal: `client_id` (an option, or in the URI query) and
   `client_secret` (a reference or the value). The sink exchanges them at the workspace's
   `/oidc/v1/token` endpoint for an access token that lasts about an hour.

On the command line, `--sink-config databricks.token=env://NAME` resolves the reference first. A
token and a client secret together, or one half of the OAuth pair, are errors.

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
   (`token_scope` overrides the default `https://ossrdbms-aad.database.windows.net/.default`, the
   Azure Database for PostgreSQL and MySQL scope in the public cloud);
3. the environment: `SHAPE_POSTGRES_PASSWORD`, then `PGPASSWORD`; `SHAPE_MYSQL_PASSWORD`, then
   `MYSQL_PWD`;
4. none: the driver's default (PostgreSQL reads `~/.pgpass`).

A password or token never appears in an exception message, a log record or a `repr`: errors from
the driver are scrubbed before they are reported, and are raised without the driver's own
exception attached.

## Testing

Contract tests run on every pull request against an in-memory server
(`shape_databases.testing.FakeServer`) that records every statement, every `COPY` row and every
`INSERT` parameter list; `FakeSnowflake` and `FakeDatabricks` do the same for the cloud sinks
(staged Parquet files, `COPY INTO` results, a warehouse that refuses rollbacks). Round trips against
real accounts are marked `live` and skip, with the missing setting named, unless it is set:
`SHAPE_TEST_SNOWFLAKE_URI` with `SNOWFLAKE_PASSWORD` (or `SHAPE_TEST_SNOWFLAKE_PRIVATE_KEY`), and
`SHAPE_TEST_DATABRICKS_URI` with `DATABRICKS_TOKEN`
(`pytest -m live plugins/shape-databases/tests/test_live.py`). `pytest -m emulator plugins/shape-databases/tests` runs the end-to-end
tests against real PostgreSQL 16 and MySQL 8.4 (`docker compose -f ci/emulators/docker-compose.yml
up -d --wait postgres mysql`, with `SHAPE_POSTGRES_PASSWORD` / `SHAPE_MYSQL_PASSWORD` set to
`shape_emulator`), nightly in CI. The DuckDB sink needs no server: its tests write real DuckDB files.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository.
