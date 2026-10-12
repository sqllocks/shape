# PostgreSQL

Understand the shipped database adapter before you select a target.

Status: available.

## What the code does

Writes batches with psycopg 3 COPY FROM STDIN. By default the table and rows are one transaction. With commit_rows, prior commits remain after a later failure.

## Connection and data handling

The sink accepts the `postgresql` and `postgres` URI schemes. Non-loopback hosts default to TLS verify-full; explicit URI options can override that. Credentials can come from an option or environment reference.

The databases plugin imports its optional driver only when opening a connection. A missing
driver fails the command instead of silently selecting another sink. Create mode refuses an
existing table. Append, truncate and replace have different effects; review the target before
you select a destructive mode. A write error can report committed rows, so a retry needs an
idempotency decision.

Evidence: `plugins/shape-databases/src/shape_databases/postgres.py` and shared adapter helpers.
Shape writes to PostgreSQL through `shape-databases`; it does not read PostgreSQL
tables directly in 0.9.1.

## Profile an export

Export the data to Parquet or CSV, then profile the local file.

Reading PostgreSQL and MySQL tables directly is planned (W9-01, issue #2).

**Needs a PostgreSQL account. Not run in CI.** In `psql`, export a table to a
client-side CSV with a header:

```sql
\copy public.orders TO 'orders.csv' WITH (FORMAT csv, HEADER true)
```

Then profile the exported file:

```bash
shape profile orders.csv -o orders.shape
```

## Owner-run receipt

On 2026-10-11, the Owner exported 5,000 Shape-generated rows from disposable PostgreSQL 17 in Docker using the command above (`COPY 5000`), then profiled the CSV with Shape 0.9.1 from PyPI in a clean `python:3.12-slim` container:

```json
{"shape_content_id": "a71f80cb159b382e149d5bbdc2ea320add3e1f16c34941f91c790c9f7fb7317c", "written": "orders.shape"}
```

The [Owner-run export receipt](../evidence/postgresql-export-profile.md) also links to the earlier PostgreSQL write: 21,750 rows in nine tables, exit 0, with persisted-row checks. These receipts describe those disposable runs only.

## Next step

These pages describe implementation behavior. They make no account or service-validation
claim. Authentication, permissions and server limits remain properties of your deployment.
Do not publish credentials in a profile, project file or issue.

## Related

[Databases](index.md) · [What leaves my machine](../WHAT_LEAVES.md) ·
[Known limitations](../KNOWN_LIMITATIONS.md)
