# MySQL

Understand the shipped database adapter before you select a target.

Status: available.

## What the code does

Writes bound batched multi-row INSERT statements through PyMySQL. MySQL DDL commits implicitly; row rollback does not restore a replaced or truncated table.

## Connection and data handling

The `mysql` URI selects the database. Non-loopback hosts default to certificate and hostname verification; explicit TLS options override it. NaN and infinity are written as NULL.

The databases plugin imports its optional driver only when opening a connection. A missing
driver fails the command instead of silently selecting another sink. Create mode refuses an
existing table. Append, truncate and replace have different effects; review the target before
you select a destructive mode. A write error can report committed rows, so a retry needs an
idempotency decision.

Evidence: `plugins/shape-databases/src/shape_databases/mysql.py` and shared adapter helpers.
Shape writes to MySQL through `shape-databases`; it does not read MySQL
tables directly in 0.9.2.

## Profile an export

Export the data to Parquet or CSV, then profile the local file.

Reading PostgreSQL and MySQL tables directly is planned (W9-01, issue #2).

**Needs a MySQL account. Not run in CI.** For numeric `order_id` and `amount`
columns, export from the MySQL client. The `NULL` token preserves missing amounts
for Shape's CSV reader:

```sql
SELECT order_id, COALESCE(CAST(amount AS CHAR), 'NULL') AS amount
INTO OUTFILE '/var/lib/mysql-files/orders.csv'
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '"'
LINES TERMINATED BY '\n'
FROM orders;
```

`INTO OUTFILE` writes on the server, requires the `FILE` privilege and is constrained
by `secure_file_priv`. The destination must not already exist. Copy that file to
your machine as `orders-data.csv`, then add its column header and profile it:

```bash
printf 'order_id,amount\n' > orders.csv
cat orders-data.csv >> orders.csv
shape profile orders.csv -o orders.shape
```

## Next step

<!-- owner: database maintainer — execute a MySQL export/write transcript against a disposable server before publishing commands. -->

These pages describe implementation behavior. They make no account or service-validation
claim. Authentication, permissions and server limits remain properties of your deployment.
Do not publish credentials in a profile, project file or issue.

## Related

[Databases](index.md) · [What leaves my machine](../WHAT_LEAVES.md) ·
[Known limitations](../KNOWN_LIMITATIONS.md)
