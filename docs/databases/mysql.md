# MySQL

Understand the shipped database adapter before you select a target.

Status: available.

## What the code does

Writes bound batched multi-row INSERT statements through PyMySQL. MySQL DDL commits implicitly; row rollback does not restore a replaced or truncated table.

## Connection and data handling

The `mysql` URI selects the database. Non-loopback hosts default to certificate and hostname verification; explicit TLS options override it. NaN and infinity are written as NULL.

MySQL 8.4 commonly uses `caching_sha2_password`. Without TLS, full authentication can fail in
PyMySQL's auth-switch handling. Shape reports this cause and recommends verified TLS, for
example `ssl_ca=/path/ca.pem&ssl-mode=VERIFY_CA`, or a server/user authentication configuration
supported by the driver. Shape does not download an untrusted RSA public key or install an RSA
dependency automatically. Prefer verified TLS rather than disabling it.

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

## Owner-run receipt

On 2026-10-11, the Owner ran the export statement above against disposable MySQL 8.4 in Docker without an error, copied the server file as `orders-data.csv`, then ran the header, copy and profile steps above with Shape 0.9.1 from PyPI in a clean `python:3.12-slim` container:

```json
{"shape_content_id": "4694f326df223d6bf96334146870d08519d400f2e98057829878b64ba8eca2ed", "written": "orders.shape"}
```

The `written` JSON is the success evidence; the receipt's `exit=True` lines came from PowerShell interpolation and are not bash exit statuses. The [Owner-run export receipt](../evidence/mysql-export-profile.md) links to the earlier MySQL write over TLS with the server CA: 21,750 rows in nine tables, exit 0. These receipts describe those disposable runs only.

## Next step

These pages describe implementation behavior. They make no account or service-validation
claim. Authentication, permissions and server limits remain properties of your deployment.
Do not publish credentials in a profile, project file or issue.

## Related

[Databases](index.md) · [What leaves my machine](../WHAT_LEAVES.md) ·
[Known limitations](../KNOWN_LIMITATIONS.md)
