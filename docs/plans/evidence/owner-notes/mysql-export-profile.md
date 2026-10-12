# MySQL export and profile receipt

Owner-run evidence supplied by the Owner, dated **2026-10-11**, using Shape **0.9.1** from PyPI. The documentation editor did not execute these service commands. Statements and output below reproduce the supplied receipt, not a complete independently captured log. No credentials are recorded.

## Environment and setup

MySQL **8.4** in a disposable Docker container. The CSV steps and profiling command ran in the same clean `python:3.12-slim` container as the PostgreSQL receipt, with Shape 0.9.1 installed from PyPI. The exact Python patch version and install log for that container were not supplied.

Setup in MySQL:

```sql
CREATE TABLE orders AS SELECT order_id, order_total AS amount FROM `order`;
```

## Page's exact export statement

```sql
SELECT order_id, COALESCE(CAST(amount AS CHAR), 'NULL') AS amount INTO OUTFILE '/var/lib/mysql-files/orders.csv' FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '"' LINES TERMINATED BY '\n' FROM orders;
```

The Owner reports no error and copied the resulting server file out as `orders-data.csv`. No literal MySQL output or separate exit code was supplied for the export.

## Page's CSV and profile steps

Executed verbatim in the clean container:

```bash
printf 'order_id,amount\n' > orders.csv
cat orders-data.csv >> orders.csv
shape profile orders.csv -o orders.shape
```

Output:

```json
{"shape_content_id": "4694f326df223d6bf96334146870d08519d400f2e98057829878b64ba8eca2ed", "written": "orders.shape"}
```

The receipt's `exit=True` lines are a PowerShell interpolation artifact: PowerShell substituted its own `$?`, not bash's exit status. The `written` JSON is the success evidence; no numeric bash exit status is inferred.

## Earlier write receipt and note resolution

The [earlier MySQL TLS write](mysql-live.md) records `shape generate retail --scale small --seed 42 --to "mysql://root@<host>:3306/shape_test?ssl_ca=<path>/mysql-ca.pem&ssl-mode=VERIFY_CA" --write-mode replace --yes`, 21,750 rows in nine tables, exit 0, and the Owner's persisted checks for order, order_line and customer. SHAPE_MYSQL_PASSWORD held the password. No ssl=false success is claimed; elapsed time is a single run, not a benchmark.

Together these receipts resolve the exact export/write request at `docs/databases/mysql.md:55` in OWNER_NOTES_TRIAGE.md. The page's actual export/CSV/profile commands are reproduced unchanged; the earlier write establishes its documented write direction. No direct MySQL profiling claim is added.
