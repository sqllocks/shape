# Live test playbook: MySQL

Shape 0.9.1 · plugin `sqllocks-shape-databases` 0.9.1, extra `[mysql]` (PyMySQL) · checked
against `release/0.9.1` on 2026-10-11.

**What 0.9.1 supports on MySQL.** Writing generated tables with `--to mysql://...` (batched
multi-row `INSERT` with bound parameters; `LOAD DATA LOCAL INFILE` is deliberately not used),
`shape seed` into a MySQL target, and `shape stream ... --to mysql://`. Reading with
`shape profile mysql://...` is **not** in 0.9.1 (W9-01, issue #2, `todo`), so the profiling step
exports to CSV first. No MySQL emitter.

## 1. Prerequisites

| Item | Smallest thing that works |
|---|---|
| Server | MySQL 8.4 in **Docker** (`docker compose -f ci/emulators/docker-compose.yml up -d --wait mysql`, the nightly `databases-e2e` server; free), or Azure Database for MySQL Flexible Server Burstable B1ms, or any MySQL 8 you own. |
| Permissions | A user with `CREATE`, `DROP`, `INSERT`, `SELECT` on the test database (`shape_test`); the sink creates the database (MySQL's schema) when missing if the user has `CREATE` globally. |
| Real table to profile | The `employees` sample database's `salaries` table (2.8 M rows) or `employees` (300 k rows), or any table of yours. |
| Local tools | `mysql` client, Python 3.11 to 3.14. |

```sql
CREATE DATABASE IF NOT EXISTS shape_test;
```

## 2. Credentials

A password is never accepted in the URI. First match wins:

| Variable or option | Read by | Meaning |
|---|---|---|
| `--sink-config mysql.password=env://MYSQL_PWD` | `shape generate --to` | explicit reference |
| `SHAPE_MYSQL_PASSWORD`, then `MYSQL_PWD` | the `mysql` sink | the password of the URI's user |
| `--sink-config mysql.credential=env://AZURE_MYSQL_TOKEN` | the sink | a Microsoft Entra token for Azure Database for MySQL |
| `SHAPE_TEST_MYSQL` with `SHAPE_MYSQL_PASSWORD` | `pytest -m emulator plugins/shape-databases/tests` | the emulator tests (nightly sets `SHAPE_TEST_MYSQL=mysql://shape@localhost:3306/shape`) |
| `MYSQL_PWD` | the `mysql` client (export step) | the client tool |

TLS: a non-loopback host gets certificate and host-name verification; `?ssl_ca=/path/ca.pem`
names the CA, `?ssl=false` opts out (recorded as your choice).

## 3. Install

```bash
pip install --find-links "$SHAPE_WHEELS" "sqllocks-shape[domains]==0.9.1" \
    "sqllocks-shape-databases[mysql]==0.9.1"
shape plugins list | grep -E 'shape.sinks:mysql'
```

(`README.md`, "Install", explains `$SHAPE_WHEELS`.)

## 4. Test steps

Work in an empty directory with `MYSQL_HOST`, `MYSQL_USER` and `MYSQL_PWD` set.

1. **Preflight.** `mysql -h "$MYSQL_HOST" -u "$MYSQL_USER" -e 'SELECT VERSION();'` prints the
   version; `shape doctor` says `Result: OK`.
2. **Export the real table.**
   ```bash
   mysql -h "$MYSQL_HOST" -u "$MYSQL_USER" --batch --raw -e 'SELECT * FROM employees.employees LIMIT 200000' | sed 's/\t/,/g' > employees.csv
   ```
   Success: `employees.csv` has 200,001 lines (a header and 200,000 rows). (`SELECT ... INTO
   OUTFILE` writes on the server, so the client-side pipe is used.)
3. **Profile.** `shape profile employees.csv --name employees -o employees.shape`: exit 0,
   `{"shape_content_id": "...", "written": "employees.shape"}`, 200,000 rows, 6 columns.
4. **Generate and write back.**
   ```bash
   export SHAPE_CONFIRM_REMOTE=1
   shape generate --from employees.shape --rows 50000 \
       --to "mysql://$MYSQL_USER@$MYSQL_HOST:3306/shape_test" --write-mode create --commit-rows 10000 --yes
   shape generate --from employees.shape --rows 50000 -o generated --format csv
   ```
   Success: exit 0 and `SELECT COUNT(*) FROM shape_test.employees` returns 50000; the local copy
   reports `Wrote 1 csv files to generated: 50,000 rows`. A rerun with `create` is refused; use
   `replace` or `truncate`. Whole domain: `shape seed retail --target "mysql://$MYSQL_USER@$MYSQL_HOST:3306/shape_test" --scale tiny --mode create --json` (exit 0, nine tables).
5. **Contract check.** As PostgreSQL step 5 with `--dialect mysql` for the DDL:
   `shape proposals propose employees.shape -d decisions.json --kinds rule --auto-accept 0.5`,
   `shape proposals contract -d decisions.json -o employees.contract.json`,
   `shape check employees.shape employees.contract.json` (exit 0, no violations),
   `shape contract emit employees.contract.json --to ddl --dialect mysql -o employees.sql` (exit 0),
   then export `shape_test.employees` the same way as step 2, `shape profile ... --capture full
   -o employees-generated.shape` and `shape check employees-generated.shape employees.contract.json`
   (exit 0, or 1 with the rules listed).
6. **Drift check.** `shape profile employees.csv --name employees -o employees-again.shape`;
   `shape diff employees.shape employees-again.shape --fail-on-drift` (exit 0, `bump: none`);
   `shape diff employees.shape employees-generated.shape --fail-on breaking` (exit 0, `0 breaking`);
   `shape drift employees.csv generated --psi` (exit 1 expected; record the columns).
7. **Streaming append.** `shape stream retail -t order --max-events 2000 --to "mysql://$MYSQL_USER@$MYSQL_HOST:3306/shape_test" --write-mode append --commit-rows 500 --yes --json`:
   exit 0, `"events": 2000`, `"complete": true`. Note: MySQL cannot store NaN or infinity; they
   are written as NULL.
8. **Optional: emulator suite.** `docker compose -f ci/emulators/docker-compose.yml up -d --wait mysql`,
   `export SHAPE_TEST_MYSQL="mysql://shape@localhost:3306/shape" SHAPE_MYSQL_PASSWORD=shape_emulator`,
   `pytest -m emulator plugins/shape-databases/tests -q -k mysql`: all pass.

## 5. Cleanup and cost

```bash
mysql -h "$MYSQL_HOST" -u "$MYSQL_USER" -e 'DROP DATABASE IF EXISTS shape_test;'
docker compose -f ci/emulators/docker-compose.yml down -v
rm -rf employees.csv generated *.shape *.json *.sql
```

Delete the managed server if you created one and rotate the password. **Cost.** Docker:
nothing. A B1ms Azure server is a few cents per hour plus storage; stop or delete it.

## 6. Results

| Step | Command | Pass | Fail | Notes |
|---|---|---|---|---|
| 1 | preflight | | | |
| 2 | export | | | rows: |
| 3 | `shape profile` | | | content id: |
| 4 | `shape generate --from --to mysql://`, `shape seed` | | | `COUNT(*)`: |
| 5 | `shape check`, `contract emit --dialect mysql` | | | rules: |
| 6 | `shape diff`, `shape drift --psi` | | | bump, columns: |
| 7 | `shape stream --to mysql://` | | | events: |
| 8 | emulator suite | | | |

Tester, date, Shape version, server: ______

## Gaps found while writing this playbook

- No `mysql://` source (`shape profile mysql://...` exits 2); W9-01 (issue #2).
- No MySQL emitter; `shape stream --to` is the streaming write path.
- Install: the plugin is not on PyPI and `v0.9.1` has no tag or release yet (`README.md`).
