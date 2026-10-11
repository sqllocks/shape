# Live test playbook: Azure Synapse dedicated SQL pool

Shape 0.9.1 · plugins `sqllocks-shape-fabric[sqlserver]` 0.9.1 (the `synapse` sink) and
`sqllocks-shape-sqlserver` 0.9.1 (profiling) · checked against `release/0.9.1` on 2026-10-11.

**What 0.9.1 supports on Synapse.** Writing generated tables into a **dedicated SQL pool** with
`--to synapse://<workspace>.sql.azuresynapse.net/<pool>`: Parquet staged in ADLS Gen2
(`synapse.staging_path`, required), one `COPY INTO ... WITH (FILE_TYPE = 'PARQUET')`, loaded rows
checked against staged rows, staging removed. Serverless SQL pools, Spark pools and pipelines are
out of scope. Profiling a pool's schema goes through the SQL Server plugin (`shape profile-db`),
which is documented for SQL Server, Azure SQL and Fabric; a dedicated pool speaks the same T-SQL
and ODBC driver, so it is expected to work but is not listed as supported (record the outcome).

## 1. Prerequisites

| Item | Smallest thing that works |
|---|---|
| Workspace | A Synapse workspace with one dedicated SQL pool at **DW100c**, paused when not in use, and the workspace's ADLS Gen2 account (`<account>.dfs.core.windows.net`) with a container `shape-staging`. |
| Permissions | Your Entra user as `db_owner` on the pool (or at least `CREATE SCHEMA`, `CREATE TABLE`, `INSERT`, `ADMINISTER DATABASE BULK OPERATIONS`); **Storage Blob Data Contributor** on the staging container for both you and the workspace's managed identity (`COPY INTO` reads the staging as the managed identity by default, `copy_identity=managed_identity`). |
| Real table to profile | Load Synapse's `NYCTaxi` sample into the pool (Knowledge center, Browse gallery, Datasets), or any table of yours. |
| Local tools | Microsoft ODBC Driver 18 and unixODBC, Azure CLI signed in, Python 3.11 to 3.14. |

```sql
CREATE SCHEMA shape_test;
```

## 2. Credentials

| Variable or option | Read by | Meaning |
|---|---|---|
| `--auth cli` (default), `msi`, `spn`, `device-code` | `shape generate --to synapse://`, `shape profile-db` | an Entra token for the SQL connection and the storage |
| `--auth sql --sql-user ... --sql-password env://SYNAPSE_SQL_PASSWORD --connection-string "synapse://<ws>.sql.azuresynapse.net/<pool>"` | the sink | a SQL login; then `copy_identity=managed_identity` only (a SQL login has no Entra identity for `signed_in`) |
| `--sink-config synapse.staging_path=abfss://shape-staging@<account>.dfs.core.windows.net/shape` | the sink | required; not a secret |
| `SHAPE_TEST_SYNAPSE_URI`, `SHAPE_TEST_SYNAPSE_STAGING`, optionally `SHAPE_TEST_SYNAPSE_AUTH`, `SHAPE_TEST_SYNAPSE_TENANT_ID`, `SHAPE_TEST_SYNAPSE_CLIENT_ID`, `SHAPE_TEST_SYNAPSE_CLIENT_SECRET` | `pytest -m live plugins/shape-fabric/tests/test_live_synapse.py` | the plugin's live round trip |

A password or key anywhere in the URI is refused; a serverless endpoint (`-ondemand`) is refused.

## 3. Install

```bash
pip install --find-links "$SHAPE_WHEELS" "sqllocks-shape[domains]==0.9.1" \
    "sqllocks-shape-fabric[sqlserver]==0.9.1"
shape plugins list | grep -E 'shape.sinks:synapse|shape.commands:profile-db'
```

## 4. Test steps

`SYNAPSE_WS` is the workspace name, `SYNAPSE_POOL` the dedicated pool, `SYNAPSE_STAGING` the
`abfss://` staging folder. Resume the pool first (`az synapse sql pool resume`).

1. **Preflight.** `odbcinst -q -d | grep -F 'ODBC Driver 18'`; `shape doctor`: `Result: OK`.
2. **Profile the real schema.**
   ```bash
   shape profile-db --server "$SYNAPSE_WS.sql.azuresynapse.net" --database "$SYNAPSE_POOL" \
       --auth cli --schema dbo --tables Trip --sample-rows 1000 -o taxi.shape
   ```
   Expected: exit 0 and the table with its catalog row count and `sample_method: checksum spread`
   (a pool enforces no keys, so the key comes from the name rule). If the sampling query is
   rejected, the reader falls back to the first rows and says so; if `profile-db` fails
   altogether, record it as a gap (the plugin does not list Synapse) and profile an export instead.
3. **Generate and load with `COPY INTO`.**
   ```bash
   export SHAPE_CONFIRM_REMOTE=1
   shape generate --from taxi.shape --rows Trip=100000 \
       --to "synapse://$SYNAPSE_WS.sql.azuresynapse.net/$SYNAPSE_POOL" \
       --sink-config "synapse.staging_path=$SYNAPSE_STAGING" --sink-config synapse.schema_name=shape_test \
       --sink-config synapse.distribution=ROUND_ROBIN --auth cli --write-mode create --yes
   ```
   Success: exit 0; `SELECT COUNT(*) FROM shape_test.Trip` returns 100000; the staging folder is
   empty afterwards. A `COPY INTO` permission error names the managed identity: give it Storage
   Blob Data Contributor, or add `--sink-config synapse.copy_identity=signed_in`.
   Alternative with the built-in domain: `shape generate retail --scale small --to "synapse://..." --sink-config "synapse.staging_path=$SYNAPSE_STAGING" --auth cli --yes`
   (the README's example): nine tables, 21,750 rows.
4. **Contract and drift.** As in `azure-sql.md` steps 5 and 6, with `--dialect tsql` for the DDL
   and a second `shape profile-db ... --schema shape_test -o taxi-generated.shape`:
   `shape check taxi.shape taxi.contract.json` (exit 0); `shape diff taxi.shape taxi-generated.shape --fail-on breaking` (exit 0, `0 breaking`).
5. **Emit.** Not applicable.
6. **Optional: the plugin's live round trip** (repository checkout):
   ```bash
   export SHAPE_TEST_SYNAPSE_URI="synapse://$SYNAPSE_WS.sql.azuresynapse.net/$SYNAPSE_POOL" SHAPE_TEST_SYNAPSE_STAGING="$SYNAPSE_STAGING"
   pytest -m live plugins/shape-fabric/tests/test_live_synapse.py -q
   ```
   Success: it passes (a uniquely named table is written, read back and dropped).

## 5. Cleanup and cost

```sql
DROP TABLE shape_test.Trip;  -- and the retail tables if step 3's alternative ran
DROP SCHEMA shape_test;
```

```bash
az storage fs directory delete --account-name <account> -f shape-staging -n shape --yes
az synapse sql pool pause -n "$SYNAPSE_POOL" --workspace-name "$SYNAPSE_WS" -g <resource group>
```

**Cost.** DW100c bills about one US dollar and twenty cents per hour at list price while
**online**, whether or not queries run; pausing stops it (storage stays, cents). This playbook
needs under an hour. `az synapse sql pool show ... --query status` must print `Paused` when you
finish.

## 6. Results

| Step | Command | Pass | Fail | Notes |
|---|---|---|---|---|
| 1 | preflight | | | |
| 2 | `shape profile-db` (pool) | | | worked on Synapse? rows: |
| 3 | `shape generate --from --to synapse://` | | | `COUNT(*)`, copy identity: |
| 4 | `shape check`, `shape diff` | | | |
| 6 | `pytest -m live` (synapse) | | | |

Tester, date, Shape version, pool size: ______

## Gaps found while writing this playbook

- Profiling a dedicated pool is not documented for `shape profile-db` (SQL Server, Azure SQL,
  Fabric are); the playbook treats it as expected-to-work and records the outcome.
- `synapse.schema_name`, `synapse.distribution` and `synapse.copy_identity` are the sink's
  documented options; their command-line pass-through was not verified here.
- Install: the plugin is not on PyPI and `v0.9.1` has no tag or release yet (`README.md`).
