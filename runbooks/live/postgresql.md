# Live test playbook: PostgreSQL

Shape 0.9.1 · plugin `sqllocks-shape-databases` 0.9.1, extra `[postgres]` (`psycopg` 3) ·
checked against `release/0.9.1` on 2026-10-11.

**What 0.9.1 supports on PostgreSQL.** Writing generated tables with `--to postgresql://...`
(`COPY ... FROM STDIN`, one transaction per table or `--commit-rows`), `shape seed` into a
PostgreSQL target, and `shape stream ... --to postgresql://` for streaming appends. Reading a
table with `shape profile postgresql://...` is **not** in 0.9.1 (W9-01, issue #2, is `todo`;
`lane/W9-01` has an implementation awaiting review), so the profiling step exports to CSV first.
No PostgreSQL emitter.

## 1. Prerequisites

| Item | Smallest thing that works |
|---|---|
| Server | Any PostgreSQL 14+: **local Docker** (`docker compose -f ci/emulators/docker-compose.yml up -d --wait postgres`, the `postgres:16` server the nightly `databases-e2e` job uses; free), or a managed server such as Azure Database for PostgreSQL Flexible Server Burstable B1ms, or a free-tier hosted instance. |
| Permissions | A role with `CONNECT` on the database and `CREATE` on it (the sink creates the schema when missing) or on the test schema; `SELECT` on the table to export. |
| Real table to profile | `pgbench_accounts` from `pgbench -i -s 10` (1 M rows, 4 columns; realistic widths and a key), or any table of yours. |
| Local tools | `psql` and `pgbench` (the PostgreSQL client package), Python 3.11 to 3.14. |

Create the dedicated schema and the sample table:

```bash
export PGHOST=... PGPORT=5432 PGDATABASE=shape PGUSER=shape    # PGPASSWORD in the shell, never in a file you commit
psql -c 'CREATE SCHEMA IF NOT EXISTS shape_test;'
pgbench -i -s 10 --no-vacuum "$PGDATABASE"      # pgbench_accounts: 1,000,000 rows
```

## 2. Credentials

A password is never accepted in the URI (one is refused). First match wins:

| Variable or option | Read by | Meaning |
|---|---|---|
| `--sink-config postgres.password=env://PGPASSWORD` | `shape generate --to` | explicit reference |
| `SHAPE_POSTGRES_PASSWORD`, then `PGPASSWORD` | the `postgres` sink | the password of the URI's user |
| `~/.pgpass` | the driver | when none of the above is set |
| `--sink-config postgres.credential=env://AZURE_PG_TOKEN` | the sink | a Microsoft Entra token for Azure Database for PostgreSQL (scope `https://ossrdbms-aad.database.windows.net/.default`) |
| `SHAPE_TEST_POSTGRES` with `SHAPE_POSTGRES_PASSWORD` | `pytest -m emulator plugins/shape-databases/tests` | the emulator tests (the nightly job sets `SHAPE_TEST_POSTGRES=postgresql://shape@localhost:5432/shape`) |
| `PGPASSWORD` | `psql`, `pgbench` (export step) | the client tools |

TLS: for a host that is not loopback the sink uses `sslmode=verify-full`; add
`?sslrootcert=/path/ca.pem` when the system store lacks the CA, or `?sslmode=require` to skip
certificate verification (recorded as your choice).

## 3. Install

Plugins are not on PyPI at 0.9.1: install from the 0.9.1 wheel set in `$SHAPE_WHEELS`
(`README.md`, "Install"):

```bash
pip install --find-links "$SHAPE_WHEELS" "sqllocks-shape[domains]==0.9.1" \
    "sqllocks-shape-databases[postgres]==0.9.1"
shape plugins list | grep -E 'shape.sinks:postgres'
```

## 4. Test steps

Work in an empty directory with `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` and `PGPASSWORD` set.

1. **Preflight.**
   ```bash
   psql -c 'SELECT version();'
   shape doctor
   ```
   Success: the server version prints; `Result: OK`, exit 0.

2. **Export the real table** (no `postgresql://` source in 0.9.1):
   ```bash
   psql -c "\copy (SELECT * FROM pgbench_accounts ORDER BY aid LIMIT 200000) TO 'accounts.csv' CSV HEADER"
   ```
   Success: `COPY 200000`; `accounts.csv` exists.

3. **Profile the real table.**
   ```bash
   shape profile accounts.csv --name accounts -o accounts.shape --json accounts-summary.json
   ```
   Success: exit 0 and `{"shape_content_id": "...", "written": "accounts.shape"}`;
   `shape inspect accounts.shape` shows 200,000 rows and 4 columns (`aid` unique).

4. **Generate from the profile and write back.**
   ```bash
   export SHAPE_CONFIRM_REMOTE=1
   shape generate --from accounts.shape --rows 50000 \
       --to "postgresql://$PGUSER@$PGHOST:$PGPORT/$PGDATABASE?sslmode=require" \
       --sink-config postgres.schema_name=shape_test --write-mode create --commit-rows 10000 --yes
   ```
   Success: exit 0; `psql -c 'SELECT COUNT(*) FROM shape_test.accounts'` returns 50000. For a
   loopback host drop `?sslmode=require` (the driver's default applies). If
   `postgres.schema_name` is not accepted from the command line, the table lands in `public`:
   record it and drop it in cleanup. A rerun with `--write-mode create` is refused (the
   existing table is never touched); use `--write-mode replace` or `truncate`.
   Whole domain in foreign-key order:
   ```bash
   shape seed retail --target "postgresql://$PGUSER@$PGHOST:$PGPORT/$PGDATABASE" --scale tiny --mode create --json
   ```
   Success: exit 0 and a JSON result with the nine retail tables and their rows; exit 1 means
   a table already exists (nothing written).
   Keep a local copy of the generation for the data-level checks:
   ```bash
   shape generate --from accounts.shape --rows 50000 -o generated --format csv
   ```

5. **Contract check.**
   ```bash
   shape proposals propose accounts.shape -d decisions.json --kinds rule --auto-accept 0.5
   shape proposals contract -d decisions.json -o accounts.contract.json
   shape check accounts.shape accounts.contract.json
   shape contract emit accounts.contract.json --to ddl --dialect postgres -o accounts.sql
   ```
   Success: `check` exits 0 with `{"passed": true, "violations": []}`; `emit` exits 0 and
   `accounts.sql` holds a `CREATE TABLE "accounts"` with `NOT NULL` where the contract says so.
   Then the written-back table (synthetic, so a full capture is fine):
   ```bash
   psql -c "\copy shape_test.accounts TO 'accounts_generated.csv' CSV HEADER"
   shape profile accounts_generated.csv --name accounts --capture full -o accounts-generated.shape
   shape check accounts-generated.shape accounts.contract.json
   ```
   Expected: exit 0, or exit 1 with the violated rules listed (record them).

6. **Drift check.**
   ```bash
   shape profile accounts.csv --name accounts -o accounts-again.shape
   shape diff accounts.shape accounts-again.shape --fail-on-drift
   shape diff accounts.shape accounts-generated.shape --fail-on breaking --json drift.json
   shape drift accounts.csv generated --psi --json -o drift-psi.json
   ```
   Success: the first `diff` exits 0 with `bump: none`; the second exits 0 with `0 breaking`;
   `drift --psi` is expected to exit 1 on generated data (record the drifted columns).

7. **Streaming append** (the closest thing to an emitter; optional):
   ```bash
   shape stream retail -t order --max-events 2000 --realtime --rate 200 \
       --to "postgresql://$PGUSER@$PGHOST:$PGPORT/$PGDATABASE" --write-mode append --commit-rows 500 --yes --json
   ```
   Success: exit 0, `"events": 2000`, `"complete": true`; `SELECT COUNT(*) FROM "order"` grows by
   2000 (the table is created on the first batch when missing).

8. **Optional: the emulator suite** (repository checkout, Docker server):
   ```bash
   docker compose -f ci/emulators/docker-compose.yml up -d --wait postgres
   export SHAPE_TEST_POSTGRES="postgresql://shape@localhost:5432/shape" SHAPE_POSTGRES_PASSWORD=shape_emulator
   pytest -m emulator plugins/shape-databases/tests -q -k postgres
   docker compose -f ci/emulators/docker-compose.yml down -v
   ```
   Success: all pass (the nightly `databases-e2e` job).

## 5. Cleanup and cost

```bash
psql -c 'DROP SCHEMA IF EXISTS shape_test CASCADE;'
psql -c 'DROP TABLE IF EXISTS "order";'
psql -c 'DROP TABLE IF EXISTS pgbench_accounts, pgbench_branches, pgbench_tellers, pgbench_history;'
rm -rf accounts.csv accounts_generated.csv generated *.shape *.json *.sql
```

Then delete the managed server (or `docker compose ... down -v`) and rotate the password you
used. **Cost.** Docker: nothing. A Burstable B1ms Azure server is a few cents per hour plus
storage; delete it or stop it (Azure stops a Flexible Server for at most seven days) so nothing
keeps running.

## 6. Results

| Step | Command | Pass | Fail | Notes (rows, exit code, violated rules, drifted columns) |
|---|---|---|---|---|
| 1 | `psql`, `shape doctor` | | | |
| 2 | `\copy` export | | | rows: |
| 3 | `shape profile` | | | content id: |
| 4a | `shape generate --from --to postgresql://` | | | `COUNT(*)`, schema: |
| 4b | `shape seed retail` | | | |
| 5a | `shape check` + `contract emit` | | | |
| 5b | `shape check` (generated, full) | | | rules: |
| 6a | `shape diff` same data | | | |
| 6b | `shape diff` real vs generated `--fail-on breaking` | | | bump: |
| 6c | `shape drift --psi` | | | drifted columns: |
| 7 | `shape stream --to postgresql://` | | | events: |
| 8 | emulator suite | | | |

Tester, date, Shape version, server: ______

## Gaps found while writing this playbook

- No `postgresql://` source: `shape profile postgresql://...` exits 2 ("no installed source
  plugin reads ..."). Tracked as W9-01 (issue #2). The `\copy` export stands in.
- `postgres.schema_name` as a `--sink-config` key is the sink's documented Python option; the
  command-line pass-through was not verified here (step 4 says what to do if it is ignored).
- No PostgreSQL emitter; `shape stream --to` is the streaming write path.
- Install: plugins are not on PyPI and `v0.9.1` has no tag or release yet; the install line
  needs the wheel set (`README.md`).
