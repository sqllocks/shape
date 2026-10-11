# Live test playbook: Azure SQL Database and SQL Server

Shape 0.9.1 · plugins `sqllocks-shape-sqlserver` 0.9.1 (profiling, the `mssql://` source) and
`sqllocks-shape-fabric[sqlserver]` 0.9.1 (the `sqlserver` sink for `--to mssql://`) · checked
against `release/0.9.1` on 2026-10-11.

**What 0.9.1 supports.** Profiling a whole schema (`shape profile-db`: catalog exact, a sampled
1,000 rows per table) and one table (`shape profile mssql://...`); writing generated tables with
`--to mssql://...` (bulk insert, `create|append|truncate|replace|upsert`); `shape seed` into a
SQL Server target. Works for SQL Server 2016+, Azure SQL Database, Azure SQL Managed Instance
and Fabric SQL database. There is no SQL Server emitter.

## 1. Prerequisites

| Item | Smallest thing that works |
|---|---|
| Server | **Azure SQL Database**, Basic tier (5 DTU) or General Purpose serverless with auto-pause 60 minutes, created with the sample **AdventureWorksLT**; or **SQL Server 2022** in Docker, free (`docker compose -f ci/emulators/docker-compose.yml up -d --wait mssql`, the server the nightly `sqlserver-e2e` job uses). |
| Network | Azure: add your client IP to the server firewall (or Allow Azure services for a CI runner). |
| Permissions | Your Entra user (or SQL login) as `db_datareader` on `SalesLT` and `db_ddladmin` plus `db_datawriter` on the database, so the sink can create `shape_test` and its tables. A SQL login needs `CREATE SCHEMA` or a pre-created schema. |
| Real table to profile | `SalesLT.SalesOrderDetail` (542 rows) and `SalesLT.Customer` (847 rows) from AdventureWorksLT. |
| Local tools | Microsoft ODBC Driver 18 for SQL Server and unixODBC (`libodbc2`) on Linux, Azure CLI signed in (`az login`) for `--auth cli`, Python 3.11 to 3.14. |

Create the test schema once (Azure SQL; the sink also creates it when missing):

```sql
CREATE SCHEMA shape_test;
```

## 2. Credentials

| Variable or option | Read by | Meaning |
|---|---|---|
| `--auth cli` (default) | `shape profile-db`, `shape profile mssql://`, `shape generate --to mssql://` | the Azure CLI session; `msi`, `spn`, `fabric` and `device-code` as in `docs/plugins/fabric-auth.md` |
| `--auth spn --tenant-id ... --client-id ...` with `SHAPE_SQLSERVER_CLIENT_SECRET` | `shape profile-db` | a service principal; the secret comes from the variable (`--client-secret` also exists, prefer the variable) |
| `--auth spn ... --client-secret env://AZURE_CLIENT_SECRET` | `shape generate --to mssql://` | the same for the sink (a reference, never a value) |
| `--auth sql --sql-user $AZSQL_USER --sql-password env://AZSQL_PASSWORD --connection-string env://AZSQL_CONNECTION` | `shape generate --to mssql://`, `shape emit/stream --to` | a SQL login; `AZSQL_CONNECTION` holds `Server=...;Database=...` without a password (a connection string with `PWD=` is refused on the command line) |
| `SHAPE_SQLSERVER_CONNECTION_STRING` | `shape profile-db` | a full ODBC connection string, with `--auth sql` when it carries the login |
| `SHAPE_TEST_MSSQL` | `pytest -m emulator plugins/shape-sqlserver/tests` and the Fabric plugin's SQL emulator tests | the ODBC string of the Docker server (set as in `.github/workflows/nightly.yml`, job `sqlserver-e2e`; the password is the one in `ci/emulators/docker-compose.yml`) |

No password or token ever appears in an error message or a log; connection strings are
redacted before they are shown.

## 3. Install

Plugins are not on PyPI at 0.9.1: install from the 0.9.1 wheel set in `$SHAPE_WHEELS`
(`README.md`, "Install"):

```bash
pip install --find-links "$SHAPE_WHEELS" "sqllocks-shape[domains]==0.9.1" \
    "sqllocks-shape-sqlserver[entra]==0.9.1" "sqllocks-shape-fabric[sqlserver]==0.9.1"
shape plugins list | grep -E 'shape.commands:profile-db|shape.sources:mssql|shape.sinks:sqlserver'
```

`pyodbc` is imported when a connection opens, so `shape plugins doctor` is clean before the
ODBC driver is installed; the first connection fails with a message naming the driver otherwise.

## 4. Test steps

Work in an empty directory. `AZSQL_SERVER` is `<server>.database.windows.net`, `AZSQL_DATABASE`
the database name. For the Docker server use `--auth sql` with the connection string in
`SHAPE_SQLSERVER_CONNECTION_STRING` (profile-db) and `AZSQL_CONNECTION` (the sink).

1. **Preflight.**
   ```bash
   odbcinst -q -d | grep -F 'ODBC Driver 18'
   shape doctor
   ```
   Success: the driver is listed; `Result: OK`, exit 0.

2. **Profile the real schema.**
   ```bash
   shape profile-db --server "$AZSQL_SERVER" --database "$AZSQL_DATABASE" --auth cli \
       --schema SalesLT --tables Customer,SalesOrderHeader,SalesOrderDetail --sample-rows 1000 \
       -o adventureworks.shape --json adventureworks-summary.json
   ```
   Success: exit 0; `shape inspect adventureworks.shape` lists the three tables with their
   catalog row counts (847, 32, 542), `sample_method: all rows` (each table is smaller than
   the sample), declared primary keys and the two declared foreign keys
   (`SalesOrderHeader.CustomerID`, `SalesOrderDetail.SalesOrderID`) with `evidence: declared`.

3. **Profile one table through the source.**
   ```bash
   shape profile "mssql://$AZSQL_SERVER/$AZSQL_DATABASE?schema=SalesLT&table=Customer" \
       --auth cli --name Customer -o customer.shape
   ```
   Success: exit 0 and `{"shape_content_id": "...", "written": "customer.shape"}`; 847 rows,
   every column (the table is read in primary-key order, so a second run gives the same
   content id).

4. **Generate from the profile and write back.**
   ```bash
   export SHAPE_CONFIRM_REMOTE=1
   shape generate --from adventureworks.shape --rows Customer=2000 --rows SalesOrderHeader=500 --rows SalesOrderDetail=3000 \
       --to "mssql://$AZSQL_SERVER/$AZSQL_DATABASE?schema=shape_test" \
       --auth cli --write-mode create --commit-rows 1000 --yes
   ```
   Success: exit 0; `SELECT COUNT(*) FROM shape_test.Customer` returns 2000 (and 500, 3000 for
   the others); every `SalesOrderDetail.SalesOrderID` exists in `shape_test.SalesOrderHeader`
   (100% foreign-key integrity is a generation guarantee: `SELECT COUNT(*) FROM shape_test.SalesOrderDetail d LEFT JOIN shape_test.SalesOrderHeader h ON h.SalesOrderID = d.SalesOrderID WHERE h.SalesOrderID IS NULL` is 0).
   Rerun with `--write-mode upsert` (merge on the primary key): the counts stay the same.
   Alternative, a whole domain in foreign-key order:
   ```bash
   shape seed retail --target "mssql://$AZSQL_SERVER/$AZSQL_DATABASE?schema=shape_test" --scale tiny --mode create --json
   ```
   `shape seed` has no `--auth` flag: it works with a SQL login (user in the URI, password in the
   sink's environment) and is not expected to work with Entra sign-in; record the outcome.

5. **Contract check.**
   ```bash
   shape proposals propose adventureworks.shape -d decisions.json --kinds rule --auto-accept 0.5
   shape proposals contract -d decisions.json -o adventureworks.contract.json
   shape check adventureworks.shape adventureworks.contract.json
   ```
   Success: exit 0 and `{"passed": true, "violations": []}`.
   ```bash
   shape profile-db --server "$AZSQL_SERVER" --database "$AZSQL_DATABASE" --auth cli \
       --schema shape_test --sample-rows 5000 -o generated.shape
   shape check generated.shape adventureworks.contract.json
   ```
   Expected: exit 0, or exit 1 with the violated rules (record them; `profile-db` writes a
   safe capture, so rules on real values report `not evaluable`, exit 2, rather than a
   violation).
   DDL from the contract: `shape contract emit adventureworks.contract.json --to ddl --dialect tsql -o adventureworks.sql` (exit 0, a `CREATE TABLE` per table).

6. **Drift check.**
   ```bash
   shape profile-db --server "$AZSQL_SERVER" --database "$AZSQL_DATABASE" --auth cli \
       --schema SalesLT --tables Customer,SalesOrderHeader,SalesOrderDetail -o adventureworks-again.shape
   shape diff adventureworks.shape adventureworks-again.shape --fail-on-drift
   shape diff adventureworks.shape generated.shape --fail-on breaking --json drift.json
   ```
   Success: the first `diff` exits 0 with `bump: none`; the second exits 0 with `0 breaking`
   (`row_count_change` and categorical changes are expected and are additive or cosmetic).

7. **Emit.** Not applicable (no SQL Server emitter). `shape stream retail -t order --to mssql://...`
   appends rows per micro-batch through the same sink if you want a streaming write test.

8. **Optional: the emulator suites** (repository checkout, Docker server):
   ```bash
   docker compose -f ci/emulators/docker-compose.yml up -d --wait mssql
   pytest -m emulator plugins/shape-sqlserver/tests -q
   pytest -m emulator plugins/shape-fabric/tests/test_sql_emulator.py plugins/shape-fabric/tests/test_sqlserver_sink_emulator.py -q
   docker compose -f ci/emulators/docker-compose.yml down -v
   ```
   Success: all pass (what the nightly `sqlserver-e2e` job runs).

## 5. Cleanup and cost

```sql
DROP TABLE IF EXISTS shape_test.SalesOrderDetail;
DROP TABLE IF EXISTS shape_test.SalesOrderHeader;
DROP TABLE IF EXISTS shape_test.Customer;
DROP SCHEMA shape_test;
```

Then delete the Azure SQL database (and the logical server if it was created for this test),
or `docker compose -f ci/emulators/docker-compose.yml down -v`. Remove the firewall rule for
your IP. Locally: `rm -rf *.shape *.json *.sql`.

**Cost.** Basic tier is about five US dollars per month, billed hourly: a one-hour test is
cents. Serverless auto-pauses after the idle delay and then bills storage only. The Docker
server is free (Developer edition). Nothing keeps running once the database is deleted; check
the resource group is empty.

## 6. Results

| Step | Command | Pass | Fail | Notes (rows, exit code, violated rules, drifted columns) |
|---|---|---|---|---|
| 1 | driver + `shape doctor` | | | |
| 2 | `shape profile-db` (SalesLT) | | | tables, keys: |
| 3 | `shape profile mssql://` (Customer) | | | content id: |
| 4a | `shape generate --from --to mssql://` | | | counts: |
| 4b | rerun `--write-mode upsert` | | | |
| 4c | `shape seed` | | | |
| 5a | `shape check` (real) | | | |
| 5b | `shape check` (generated schema) | | | rules: |
| 5c | `shape contract emit --to ddl --dialect tsql` | | | |
| 6a | `shape diff` same schema | | | |
| 6b | `shape diff` real vs generated `--fail-on breaking` | | | bump: |
| 7 | emit | n/a | n/a | |
| 8 | emulator suites | | | |

Tester, date, Shape version, tier: ______

## Gaps found while writing this playbook

- `shape seed` takes no `--auth`, so Entra sign-in to Azure SQL is not available for it; use
  `shape generate --from ... --to mssql://` with `--auth` instead.
- `shape profile-db` writes a sampled, safe profile: contract rules that need values (`min`,
  `max` on dates) are `not evaluable` on it, and `shape diff` adds sampling notes.
- No SQL Server emitter.
- Install: plugins are not on PyPI and `v0.9.1` has no tag or release yet; the install line
  needs the wheel set (`README.md`).
