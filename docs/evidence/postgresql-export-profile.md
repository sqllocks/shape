# PostgreSQL export and profile receipt

Status: available.

Needs a PostgreSQL server. Not run in CI. Commands and output are historical evidence, not commands executed by the docs harness.

Private account identifiers, service usernames, workspace names and key-file paths are replaced with angle-bracket placeholders where present. Public GitHub run, job and ruleset IDs remain so you can trace the results. Recorded findings, dates, row counts and exit statuses are unchanged.

Owner-run evidence supplied by the Owner, dated **2026-10-11**, using Shape **0.9.1** from PyPI. The documentation editor did not execute these service commands. Statements and output below reproduce the supplied receipt, not a complete independently captured log. No credentials are recorded.

## Environment and setup

PostgreSQL **17** in a disposable Docker container. The profiling command ran in a clean `python:3.12-slim` container with Shape 0.9.1 installed from PyPI. The exact Python patch version and install log for that container were not supplied.

Setup in PostgreSQL:

```sql
CREATE TABLE public.orders AS SELECT * FROM public."order";
```

Owner-reported output:

```text
SELECT 5000
```

The source is 5,000 Shape-generated rows, not production data.

## Page's export command

In psql:

```sql
\copy public.orders TO 'orders.csv' WITH (FORMAT csv, HEADER true)
```

Output:

```text
COPY 5000
```

## Page's profile command

In the clean Python container:

```bash
shape profile orders.csv -o orders.shape
```

Output:

```json
{"shape_content_id": "a71f80cb159b382e149d5bbdc2ea320add3e1f16c34941f91c790c9f7fb7317c", "written": "orders.shape"}
```

No numeric process exit status was supplied for this new command; the `written` JSON is the supplied success evidence.

## Earlier write receipt

The [earlier PostgreSQL write](postgresql-live.md) records `shape generate retail --scale small --seed 42 --to "postgresql://postgres@<host>:5433/shape_test?sslmode=disable" --write-mode create --yes`, 21,750 rows in nine tables, exit 0, and the Owner's persisted-row checks. Its credentials were held in SHAPE_POSTGRES_PASSWORD; SHAPE_CONFIRM_REMOTE=1 was set. Its individual elapsed time is not a benchmark.

These receipts preserve the export/profile steps and the earlier write direction. They do not establish direct database profiling.

## Related

[Published receipts](index.md) · [Public owner decisions](../DECISIONS.md)
