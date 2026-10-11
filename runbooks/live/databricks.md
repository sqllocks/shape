# Live test playbook: Databricks

Shape 0.9.1 · plugin `sqllocks-shape-databases` 0.9.1, extra `[databricks]` · checked against
`release/0.9.1` on 2026-10-11.

**What 0.9.1 supports on Databricks.** Writing generated tables into Unity Catalog through a SQL
warehouse (`--to databricks://...`, the `databricks` sink). Reading a Databricks table with
`shape profile databricks://...` is **not** in 0.9.1 (W9-02, issue #3, is `todo`), so the
profiling step exports the table to Parquet first. There is no Databricks emitter.

## 1. Prerequisites

| Item | Smallest thing that works |
|---|---|
| Account | Any workspace with Unity Catalog and SQL warehouses (a 14-day trial is enough). |
| Compute | One **serverless SQL warehouse**, size 2X-Small, auto-stop 10 minutes. Only SQL warehouses are supported; clusters are not. |
| Permissions (the identity Shape signs in as) | `USE CATALOG` on the catalog, `USE SCHEMA`, `CREATE TABLE`, `SELECT` and `MODIFY` on the test schema, `CAN USE` on the warehouse, `READ VOLUME`/`WRITE VOLUME` on the export volume. |
| Real table to profile | `samples.tpch.orders` (shipped with every workspace; 1.5 M rows, 9 columns). |
| Tools | Databricks CLI (`databricks`) signed in to the workspace, Python 3.11 to 3.14. |

Create the dedicated test schema and an export volume (SQL editor, on the warehouse):

```sql
CREATE SCHEMA IF NOT EXISTS main.shape_test;
CREATE VOLUME IF NOT EXISTS main.shape_test.exports;
```

Unity Catalog stores table and schema names in lower case; the sink refuses a name with capitals
(`plugins/shape-databases/README.md`, "Databricks"), so keep `shape_test` lower case.

## 2. Credentials

Never put a token in the URI: the sink refuses a URI with a user part. Shape reads, in this order:

| Variable | Read by | Meaning |
|---|---|---|
| `DATABRICKS_TOKEN` | the `databricks` sink | a personal access token (the simplest path) |
| `--sink-config databricks.token=env://DATABRICKS_TOKEN` | `shape generate --to` | the same token as an explicit reference |
| `--sink-config databricks.client_secret=env://DATABRICKS_CLIENT_SECRET` plus `?client_id=<app id>` in the URI | the sink | OAuth machine-to-machine for a service principal (token exchanged at `/oidc/v1/token`) |
| `DATABRICKS_HOST`, `DATABRICKS_TOKEN` | the Databricks CLI (export step) | not read by Shape |
| `SHAPE_TEST_DATABRICKS_URI`, `DATABRICKS_TOKEN` | `pytest -m live plugins/shape-databases/tests/test_live.py` | the plugin's own live round-trip test |

Set `DATABRICKS_TOKEN` in the shell before step 4. The token never appears in an error, a log
or a `repr`.

## 3. Install

Shape's plugins are not on PyPI at 0.9.1 (PyPI holds `sqllocks-shape` 0.9.0 only on 2026-10-11),
so install from the 0.9.1 wheel set in `$SHAPE_WHEELS` (see `README.md`, "Install"):

```bash
pip install --find-links "$SHAPE_WHEELS" "sqllocks-shape[domains]==0.9.1" \
    "sqllocks-shape-databases[databricks]==0.9.1"
shape plugins list | grep -E 'shape.sinks:databricks'
```

`shape plugins list` must show `shape.sinks:databricks`. The driver
(`databricks-sql-connector`) is imported only when a connection opens.

## 4. Test steps

Work in an empty directory. `SQL_WAREHOUSE_HOST` is the workspace host
(`adb-....azuredatabricks.net`) and `SQL_WAREHOUSE_PATH` the warehouse's HTTP path
(`/sql/1.0/warehouses/<id>`), both shown under the warehouse's connection details.

1. **Preflight.**
   ```bash
   shape doctor
   ```
   Success: `Result: OK`, exit 0. (`shape doctor` has no Databricks check; the connection is
   tested in step 3.)

2. **Export the real table to Parquet** (no `databricks://` source in 0.9.1). In a notebook or
   a SQL cell on any compute that can write the volume:
   ```python
   spark.table("samples.tpch.orders").limit(200000).write.mode("overwrite") \
       .parquet("/Volumes/main/shape_test/exports/orders")
   ```
   then on your machine:
   ```bash
   databricks fs cp -r dbfs:/Volumes/main/shape_test/exports/orders ./orders_parquet
   ```
   Success: `orders_parquet/` holds `part-*.parquet` files (Spark's `_SUCCESS` and
   `_committed_*` files are skipped by Shape because they start with `_`).

3. **Profile the real table.**
   ```bash
   shape profile ./orders_parquet --name orders -o orders.shape --json orders-summary.json
   ```
   Success: exit 0 and one line `{"shape_content_id": "...", "written": "orders.shape"}`;
   `shape inspect orders.shape` shows table `orders` with 200,000 rows and 9 columns. The
   profile is a safe capture (statistics and formats only), so it can be kept.

4. **Generate from the profile and write back to Databricks.**
   ```bash
   export SHAPE_CONFIRM_REMOTE=1
   shape generate --from orders.shape --rows 50000 \
       --to "databricks://$SQL_WAREHOUSE_HOST$SQL_WAREHOUSE_PATH?catalog=main&schema=shape_test" \
       --write-mode create --yes
   ```
   Success: exit 0; the summary line reports 1 table, 50,000 rows; in the SQL editor
   `SELECT COUNT(*) FROM main.shape_test.orders` returns 50000. `--write-mode create` refuses
   an existing table, so a rerun must use `--write-mode replace` or drop the table first.
   Also keep a local copy of the same generation for the data-level checks:
   ```bash
   shape generate --from orders.shape --rows 50000 -o generated --format parquet
   ```
   Success: `Wrote 1 parquet files to generated: 50,000 rows in 1 tables`.

5. **Contract check.** Build a contract from the real profile, then check both profiles:
   ```bash
   shape proposals propose orders.shape -d decisions.json --kinds rule --auto-accept 0.5
   shape proposals contract -d decisions.json -o orders.contract.json
   shape check orders.shape orders.contract.json
   ```
   Success: exit 0 and `{"passed": true, "violations": []}`.
   Then the generated copy (synthetic data, so a full capture is fine):
   ```bash
   shape profile generated/orders.parquet --capture full -o orders-generated.shape
   shape check orders-generated.shape orders.contract.json
   ```
   Expected: exit 0, or exit 1 listing the violated rules. A `no_placeholder` violation on a
   column whose real data has a legitimate spike (for example a 0.0 amount) is a known
   difference of generated data; record the rule names in the results table.
   Optional: `shape contract emit orders.contract.json --to jsonschema --table orders`.

6. **Drift check.** First prove a stable feed is quiet: profile the export a second time and
   compare.
   ```bash
   shape profile ./orders_parquet --name orders -o orders-again.shape
   shape diff orders.shape orders-again.shape --fail-on-drift
   ```
   Success: exit 0 and `bump: none (0 breaking, 0 additive, 0 cosmetic)`.
   Then the written-back data against the real profile:
   ```bash
   shape diff orders.shape orders-generated.shape --fail-on breaking --json drift.json
   ```
   Success: exit 0 with `0 breaking` in the `bump:` line (additive and cosmetic changes, such
   as `new_categorical_values` or `category_shift`, are expected from generated data). Exit 1
   means a breaking change (a removed column, a type change): a failure to report.
   Data-level drift score, no SciPy needed:
   ```bash
   shape drift ./orders_parquet generated --psi --json -o drift-psi.json
   ```
   Expected: exit 1 (PSI above 0.2 on some columns is normal for generated data); the report
   lists the PSI per column. Record the drifted columns.

7. **Emit.** Not applicable: there is no Databricks emitter in 0.9.1 (`shape emit --sink`
   takes `kafka://`, `eventhubs://`, `eventstream://` and `eventhouse://` only).

8. **Optional: the plugin's own live round trip** (needs the repository checkout):
   ```bash
   export SHAPE_TEST_DATABRICKS_URI="databricks://$SQL_WAREHOUSE_HOST$SQL_WAREHOUSE_PATH?catalog=main&schema=shape_test"
   pytest -m live plugins/shape-databases/tests/test_live.py -k databricks -q
   ```
   Success: the test passes (it writes a uniquely named table, reads it back, compares the row
   count and the dataset id, and drops the table).

## 5. Cleanup and cost

```sql
DROP TABLE IF EXISTS main.shape_test.orders;
DROP VOLUME IF EXISTS main.shape_test.exports;
DROP SCHEMA IF EXISTS main.shape_test CASCADE;
```

Then stop the SQL warehouse (Compute, Stop) and revoke the personal access token (User Settings,
Developer, Access tokens). Locally: `rm -rf orders_parquet generated *.shape *.json`.

**Cost.** A 2X-Small serverless warehouse is billed per DBU only while running; the whole
playbook needs about 20 minutes of warehouse time, which is a few dollars at list price in most
regions. Nothing else keeps running: check that the warehouse shows **Stopped** and that no
all-purpose cluster was left on by the export notebook (terminate it).

## 6. Results

| Step | Command | Pass | Fail | Notes (rows, exit code, violated rules, drifted columns) |
|---|---|---|---|---|
| 1 | `shape doctor` | | | |
| 2 | export to Parquet | | | rows: |
| 3 | `shape profile` | | | content id: |
| 4a | `shape generate --from --to databricks://` | | | `SELECT COUNT(*)`: |
| 4b | `shape generate --from -o generated` | | | |
| 5a | `shape check` (real profile) | | | |
| 5b | `shape check` (generated, full capture) | | | rules: |
| 6a | `shape diff` same data `--fail-on-drift` | | | |
| 6b | `shape diff` real vs generated `--fail-on breaking` | | | bump: |
| 6c | `shape drift --psi` | | | drifted columns: |
| 7 | emit | n/a | n/a | no emitter |
| 8 | `pytest -m live` (databricks) | | | |

Tester, date, Shape version (`shape --version`), warehouse size: ______

## Gaps found while writing this playbook

- No `databricks://` source: `shape profile databricks://...` exits 2 with "no installed source
  plugin reads ...". Tracked as W9-02 (issue #3). This playbook exports to Parquet instead.
- No Databricks emitter and no `--to databricks://` for `shape emit`/`shape stream` (the sink
  is registered, so `--to` on `emit`/`stream` may work; not verified here).
- Install: the plugin is not on PyPI and `v0.9.1` has no tag or release yet (2026-10-11), so
  the install line needs the wheel set (`README.md`).
