# Live test playbook: Snowflake

Shape 0.9.1 · plugin `sqllocks-shape-databases` 0.9.1, extra `[snowflake]` · checked against
`release/0.9.1` on 2026-10-11.

**What 0.9.1 supports on Snowflake.** Writing generated tables with `--to snowflake://...`
(Parquet files `PUT` to the table stage, one `COPY INTO`, row count checked). Reading a
Snowflake table with `shape profile snowflake://...` is **not** in 0.9.1 (W9-02, issue #3, is
`todo`), so the profiling step unloads the table to Parquet first. There is no Snowflake emitter.

## 1. Prerequisites

| Item | Smallest thing that works |
|---|---|
| Account | Standard edition; a 30-day trial is enough. |
| Compute | One X-Small warehouse, `AUTO_SUSPEND = 60`, `AUTO_RESUME = TRUE`. |
| Permissions (the role Shape signs in with) | `USAGE` on the warehouse and the database, `CREATE SCHEMA` on the database (or `USAGE`, `CREATE TABLE` and `CREATE STAGE` on the test schema), `USAGE` on `SNOWFLAKE_SAMPLE_DATA`. |
| Real table to profile | `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS` (shared sample data, 1.5 M rows, 9 columns). |
| Tools | SnowSQL (or Snowflake CLI) for the unload, Python 3.11 to 3.14. |

Create the dedicated database, schema, warehouse and role (as `ACCOUNTADMIN` or `SYSADMIN`):

```sql
CREATE WAREHOUSE IF NOT EXISTS SHAPE_WH WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE;
CREATE DATABASE IF NOT EXISTS SHAPE_TEST;
CREATE SCHEMA IF NOT EXISTS SHAPE_TEST.LIVE;
CREATE ROLE IF NOT EXISTS SHAPE_LOADER;
GRANT USAGE ON WAREHOUSE SHAPE_WH TO ROLE SHAPE_LOADER;
GRANT USAGE ON DATABASE SHAPE_TEST TO ROLE SHAPE_LOADER;
GRANT USAGE, CREATE TABLE, CREATE STAGE ON SCHEMA SHAPE_TEST.LIVE TO ROLE SHAPE_LOADER;
GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE_SAMPLE_DATA TO ROLE SHAPE_LOADER;
GRANT ROLE SHAPE_LOADER TO USER <your user>;
```

Shape creates tables quoted, exactly as named (`"orders"`, not `ORDERS`): query them quoted.

## 2. Credentials

A password is never accepted in the URI (a URI with one is refused). One of:

| Variable or option | Read by | Meaning |
|---|---|---|
| `SNOWFLAKE_PASSWORD` | the `snowflake` sink | the password of the URI's user |
| `--sink-config snowflake.private_key=file://$HOME/.snowflake/rsa_key.p8` | `shape generate --to` | a PKCS#8 key pair instead of a password; the file must be mode 600 |
| `--sink-config snowflake.private_key_passphrase=env://SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` | the sink | the passphrase of an encrypted key |
| `SHAPE_TEST_SNOWFLAKE_URI` with `SNOWFLAKE_PASSWORD` (or `SHAPE_TEST_SNOWFLAKE_PRIVATE_KEY`) | `pytest -m live plugins/shape-databases/tests/test_live.py` | the plugin's own live round-trip test |
| `SNOWSQL_PWD` | SnowSQL (unload step) | not read by Shape |

A key and a password together are an error. Nothing of a key, passphrase or password appears
in an error message or a log.

## 3. Install

Plugins are not on PyPI at 0.9.1: install from the 0.9.1 wheel set in `$SHAPE_WHEELS`
(`README.md`, "Install"):

```bash
pip install --find-links "$SHAPE_WHEELS" "sqllocks-shape[domains]==0.9.1" \
    "sqllocks-shape-databases[snowflake]==0.9.1"
shape plugins list | grep -E 'shape.sinks:snowflake'
```

## 4. Test steps

Work in an empty directory. `SNOWFLAKE_ACCOUNT` is the account identifier (`<org>-<account>`),
`SNOWFLAKE_USER` the login.

1. **Preflight.**
   ```bash
   shape doctor
   ```
   Success: `Result: OK`, exit 0.

2. **Unload the real table to Parquet** (no `snowflake://` source in 0.9.1). In SnowSQL, as
   `SHAPE_LOADER` on `SHAPE_WH`:
   ```sql
   COPY INTO @~/shape_test/orders/
     FROM (SELECT * FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS LIMIT 200000)
     FILE_FORMAT = (TYPE = PARQUET) HEADER = TRUE OVERWRITE = TRUE;
   GET @~/shape_test/orders/ file:///absolute/path/to/orders_parquet/;
   ```
   Success: `orders_parquet/` holds one or more `.parquet` files; `HEADER = TRUE` keeps the
   column names.

3. **Profile the real table.**
   ```bash
   shape profile ./orders_parquet --name orders -o orders.shape --json orders-summary.json
   ```
   Success: exit 0 and `{"shape_content_id": "...", "written": "orders.shape"}`;
   `shape inspect orders.shape` shows 200,000 rows and 9 columns.

4. **Generate from the profile and write back to Snowflake.**
   ```bash
   export SHAPE_CONFIRM_REMOTE=1
   shape generate --from orders.shape --rows 50000 \
       --to "snowflake://$SNOWFLAKE_USER@$SNOWFLAKE_ACCOUNT/SHAPE_TEST/LIVE?warehouse=SHAPE_WH&role=SHAPE_LOADER" \
       --write-mode create --yes
   ```
   Success: exit 0; `SELECT COUNT(*) FROM SHAPE_TEST.LIVE."orders"` returns 50000; the table
   stage `@%"orders"` is empty again (`LIST @SHAPE_TEST.LIVE.%"orders"` returns no rows). The
   sink fails the write if `COPY INTO` reports fewer rows than it staged.
   Keep a local copy for the data-level checks:
   ```bash
   shape generate --from orders.shape --rows 50000 -o generated --format parquet
   ```
   Success: `Wrote 1 parquet files to generated: 50,000 rows in 1 tables`.

5. **Contract check.**
   ```bash
   shape proposals propose orders.shape -d decisions.json --kinds rule --auto-accept 0.5
   shape proposals contract -d decisions.json -o orders.contract.json
   shape check orders.shape orders.contract.json
   ```
   Success: exit 0 and `{"passed": true, "violations": []}`.
   ```bash
   shape profile generated/orders.parquet --capture full -o orders-generated.shape
   shape check orders-generated.shape orders.contract.json
   ```
   Expected: exit 0, or exit 1 with the violated rules listed (record them; a
   `no_placeholder` spike on a column that really has one is a known difference).

6. **Drift check.**
   ```bash
   shape profile ./orders_parquet --name orders -o orders-again.shape
   shape diff orders.shape orders-again.shape --fail-on-drift
   ```
   Success: exit 0 and `bump: none (0 breaking, 0 additive, 0 cosmetic)`.
   ```bash
   shape diff orders.shape orders-generated.shape --fail-on breaking --json drift.json
   shape drift ./orders_parquet generated --psi --json -o drift-psi.json
   ```
   Success for `diff`: exit 0 and `0 breaking` in the `bump:` line. `drift --psi` is expected to
   exit 1 on generated data; record the drifted columns.

7. **Emit.** Not applicable: no Snowflake emitter in 0.9.1.

8. **Optional: the plugin's own live round trip** (repository checkout):
   ```bash
   export SHAPE_TEST_SNOWFLAKE_URI="snowflake://$SNOWFLAKE_USER@$SNOWFLAKE_ACCOUNT/SHAPE_TEST/LIVE?warehouse=SHAPE_WH&role=SHAPE_LOADER"
   pytest -m live plugins/shape-databases/tests/test_live.py -k snowflake -q
   ```
   Success: the test passes (a uniquely named table is written, read back and dropped).

## 5. Cleanup and cost

```sql
DROP TABLE IF EXISTS SHAPE_TEST.LIVE."orders";
REMOVE @~/shape_test/;
DROP SCHEMA IF EXISTS SHAPE_TEST.LIVE;
DROP DATABASE IF EXISTS SHAPE_TEST;
ALTER WAREHOUSE SHAPE_WH SUSPEND;
DROP WAREHOUSE IF EXISTS SHAPE_WH;
```

Rotate or drop the password or key pair you created for the test. Locally:
`rm -rf orders_parquet generated *.shape *.json`.

**Cost.** An X-Small warehouse is 1 credit per hour while running, billed per second with a
60-second minimum per resume; the playbook uses a few minutes of warehouse time plus the user
stage's storage for the unloaded files (cents). `AUTO_SUSPEND = 60` and the `SUSPEND` above make
sure nothing keeps running; `SHOW WAREHOUSES` must show `SUSPENDED`.

## 6. Results

| Step | Command | Pass | Fail | Notes (rows, exit code, violated rules, drifted columns) |
|---|---|---|---|---|
| 1 | `shape doctor` | | | |
| 2 | `COPY INTO @~` + `GET` | | | files: |
| 3 | `shape profile` | | | content id: |
| 4a | `shape generate --from --to snowflake://` | | | `SELECT COUNT(*)`: |
| 4b | `shape generate --from -o generated` | | | |
| 5a | `shape check` (real profile) | | | |
| 5b | `shape check` (generated, full capture) | | | rules: |
| 6a | `shape diff` same data `--fail-on-drift` | | | |
| 6b | `shape diff` real vs generated `--fail-on breaking` | | | bump: |
| 6c | `shape drift --psi` | | | drifted columns: |
| 7 | emit | n/a | n/a | no emitter |
| 8 | `pytest -m live` (snowflake) | | | |

Tester, date, Shape version, warehouse size: ______

## Gaps found while writing this playbook

- No `snowflake://` source: `shape profile snowflake://...` exits 2 ("no installed source
  plugin reads ..."). Tracked as W9-02 (issue #3). The unload step stands in.
- No Snowflake emitter.
- Install: the plugin is not on PyPI and `v0.9.1` has no tag or release yet; the install line
  needs the wheel set (`README.md`).
