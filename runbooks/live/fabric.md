# Live test playbook: Microsoft Fabric (Lakehouse, Warehouse, Eventhouse)

Shape 0.9.1 · plugin `sqllocks-shape-fabric` 0.9.1 (with `[sqlserver]` for the Warehouse and
SQL database, `[entra]` for Entra sign-in) plus core's `[azure]` extra · checked against
`release/0.9.1` on 2026-10-11.

**What 0.9.1 supports on Fabric.**

| Surface | Profile (read) | Write back | Emit |
|---|---|---|---|
| Lakehouse | `shape profile onelake://<ws>/<lh>/Tables/<table>` (Delta) or `.../Files/<path>` (core `abfss://` source, `[azure]` extra) | `--to delta+abfss://<ws>@onelake.dfs.fabric.microsoft.com/<lh>.Lakehouse/Tables` (Delta tables) or `--to abfss://.../<lh>.Lakehouse/Files/<folder>` (files); `shape publish -t lakehouse` | n/a |
| Warehouse | `shape profile-db --server <sql endpoint> --database <warehouse>` (SQL Server plugin, catalog plus a 1,000-row sample per table) | `--to warehouse://<sql endpoint>/<warehouse>` with `warehouse.staging_path` (Parquet staged in OneLake, `COPY INTO`); `shape publish -t warehouse` | n/a |
| Eventhouse (KQL database) | **not in 0.9.1** (W9-11, issue #12, `todo`); see step E2 for the OneLake-availability workaround | n/a for `generate --to` (`eventhouse://` is an emitter, not a sink: "no sink writes eventhouse:// targets") | `shape emit --sink eventhouse://<query host>/<db>[/<table>]` (streaming ingestion); `shape publish -t eventhouse` |
| Eventstream | n/a | n/a | `shape emit --sink eventstream://<name>` with `SHAPE_EVENTSTREAM_CONNECTION_STRING` |
| Fabric SQL database | as a SQL Server (`shape profile-db`, `mssql://` source) | `--to mssql://<sql endpoint>/<db>`; `shape publish -t sql-database` | n/a |
| Semantic model | `shape profile semantic-model://<ws>/<model>/<table>`, `shape profile-model` (inside a Fabric notebook only, `[semantic-link]`) | `shape fabric export-model` (`.bim`) | n/a |

## 1. Prerequisites

| Item | Smallest thing that works |
|---|---|
| Capacity | A Fabric trial capacity (free for 60 days), or an F2 capacity you pause afterwards. |
| Workspace | One workspace `shape-test` on that capacity; you are a workspace Admin or Member. |
| Items | A Lakehouse `shape_lh` with the sample data "Public holidays" loaded (Lakehouse, Get data, Start with sample data; it creates the Delta table `publicholidays`, about 70 k rows); a Warehouse `shape_wh` created with "Sample warehouse" (tables `dbo.Trip`, `dbo.Geography`, `dbo.Weather`, `dbo.Date`, ...); an Eventhouse `shape_eh` with its default KQL database (streaming ingestion is on by default) and, for the Eventstream step, an Eventstream `shape_es` with a **Custom endpoint** source. |
| Permissions | Your Entra user (or a service principal with Contributor on the workspace and, for the Warehouse, a database user created with `CREATE USER [<app>] FROM EXTERNAL PROVIDER`). The Entra identity needs read and write on the Lakehouse's OneLake paths (workspace Member gives it). |
| Local tools | Azure CLI signed in (`az login`), Microsoft ODBC Driver 18 for SQL Server and unixODBC on Linux (needed by `pyodbc` for the Warehouse and SQL database), Python 3.11 to 3.14. |

Write the live tests into their own schema or folder: `shape_test` schema in the Warehouse,
`Files/shape_test/` and tables prefixed `shape_` in the Lakehouse, a KQL table named by
`shape emit` (one per generated table) in the Eventhouse.

## 2. Credentials

Nothing on the command line: `--auth cli` (the default) uses the `az login` session. Secrets
are references, never values.

| Variable or option | Read by | Meaning |
|---|---|---|
| `--auth cli` (default), `msi`, `spn`, `device-code`, `fabric` | `shape profile`, `shape generate --to`, `shape emit`, `shape stream`, `shape publish`, `shape profile-db` | how to get the Entra token (`docs/plugins/fabric-auth.md`) |
| `--auth spn --tenant-id $AZURE_TENANT_ID --client-id $AZURE_CLIENT_ID --client-secret env://AZURE_CLIENT_SECRET` | the same commands | a service principal; the secret is a reference to the variable |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | `DefaultAzureCredential` (core `abfss://` reads without `--auth`) | the same service principal through the Azure SDK |
| `SHAPE_FABRIC_TOKEN`, `SHAPE_FABRIC_STORAGE_TOKEN` | `--scale-mode fabric_spark` and the Fabric REST commands | bearer tokens that win over `--auth` when set (not needed here) |
| `SHAPE_EVENTHOUSE_TOKEN` | the `eventhouse://` emitter | a bearer token for the query host; otherwise `--auth` / Entra |
| `SHAPE_EVENTSTREAM_CONNECTION_STRING` | the `eventstream://` emitter | the Custom endpoint's connection string (`Endpoint=sb://...;EntityPath=es_...`) |
| `SHAPE_LAKEHOUSE_PATH`, `SHAPE_WORKSPACE_ID`, `SHAPE_LAKEHOUSE_ID` | `shape publish` | defaults for `--base-path`, `--workspace-id`, `--lakehouse-id` |
| `SHAPE_SQLSERVER_CONNECTION_STRING` | `shape profile-db` | an ODBC connection string instead of `--server/--database` |
| `FABRIC_EVENTHOUSE_URI`, `FABRIC_EVENTHOUSE_TOKEN`, `FABRIC_EVENTSTREAM_CONNECTION_STRING`, `FABRIC_SQL_CONNECTION_STRING`, `FABRIC_WAREHOUSE_CONNECTION_STRING`, `FABRIC_STAGING_PATH`, `FABRIC_WORKSPACE_ID`, `FABRIC_LAKEHOUSE_ID`, `FABRIC_TENANT_ID`, `FABRIC_CLIENT_ID`, `FABRIC_CLIENT_SECRET` | `pytest -m live plugins/shape-fabric/tests` (`test_live.py`, `test_live_writers.py`, `test_live_commands.py`, `test_live_scale_sinks.py`) | the plugin's own live tests; each file's docstring says which it needs |

## 3. Install

Plugins are not on PyPI at 0.9.1: install from the 0.9.1 wheel set in `$SHAPE_WHEELS`
(`README.md`, "Install"):

```bash
pip install --find-links "$SHAPE_WHEELS" "sqllocks-shape[domains,azure]==0.9.1" \
    "sqllocks-shape-fabric[sqlserver,eventhubs,entra]==0.9.1"
shape plugins list | grep -E 'shape.sources:onelake|shape.sinks:warehouse|shape.sinks:sqlserver|shape.emitters:eventhouse|shape.emitters:eventstream|shape.commands:profile-db'
```

`[azure]` brings `adlfs`, `azure-identity` and `deltalake` for OneLake reads and Delta writes;
`[sqlserver]` brings the SQL Server plugin and `pyodbc`; `[eventhubs]` the Eventstream emitter.

## 4. Test steps

Work in an empty directory. Names: `FABRIC_WORKSPACE=shape-test`, `FABRIC_LAKEHOUSE=shape_lh`,
`FABRIC_WAREHOUSE=shape_wh`; `FABRIC_SQL_ENDPOINT` is the Warehouse's SQL connection string host
(`<guid>.datawarehouse.fabric.microsoft.com`, from the Warehouse's settings);
`FABRIC_KQL_HOST` is the Eventhouse query URI host (`<name>.<region>.kusto.fabric.microsoft.com`)
and `FABRIC_KQL_DB` the KQL database name.

### L. Lakehouse

1. **Preflight.**
   ```bash
   az account show --query user.name -o tsv
   shape doctor --fabric "onelake://$FABRIC_WORKSPACE/$FABRIC_LAKEHOUSE"
   ```
   Success: your user is printed; `shape doctor` reports OneLake reachable, sign-in ok, workspace
   and item found; exit 0.

2. **Profile the real Delta table.**
   ```bash
   shape profile "onelake://$FABRIC_WORKSPACE/$FABRIC_LAKEHOUSE/Tables/publicholidays" \
       --name publicholidays -o holidays.shape --json holidays-summary.json
   ```
   Success: exit 0 and `{"shape_content_id": "...", "written": "holidays.shape"}`;
   `shape inspect holidays.shape` shows the table's row count (about 70 k) and its columns. A
   Delta table with deletion vectors or column mapping is read through DuckDB with a note on
   stderr (`[delta-fallback]` extra).

3. **Generate from the profile and write back as a Delta table.**
   ```bash
   export SHAPE_CONFIRM_REMOTE=1
   shape generate --from holidays.shape --rows 50000 \
       --to "delta+abfss://$FABRIC_WORKSPACE@onelake.dfs.fabric.microsoft.com/$FABRIC_LAKEHOUSE.Lakehouse/Tables" \
       --table-prefix shape_ --auth cli --yes
   ```
   Success: exit 0; the Lakehouse explorer shows a new table `shape_publicholidays` after a
   refresh, and a SQL endpoint query `SELECT COUNT(*) FROM shape_publicholidays` returns 50000.
   If `--table-prefix` is refused on this path, drop it and write the table under its own name
   into an empty Lakehouse (record it in the notes). Keep a local copy too:
   ```bash
   shape generate --from holidays.shape --rows 50000 -o generated --format parquet
   ```
   Alternative with files and a run manifest (the `publish` command):
   ```bash
   shape publish retail -t lakehouse --base-path "onelake://$FABRIC_WORKSPACE/$FABRIC_LAKEHOUSE/Files/shape_test" --auth cli
   ```
   Success: files under `Files/shape_test/landing/retail/<table>/dt=latest/part-0001.parquet`
   and `landing/retail/manifest/_control/run_manifest.json`.

4. **Contract check.**
   ```bash
   shape proposals propose holidays.shape -d decisions.json --kinds rule --auto-accept 0.5
   shape proposals contract -d decisions.json -o holidays.contract.json
   shape check holidays.shape holidays.contract.json
   ```
   Success: exit 0 and `{"passed": true, "violations": []}`.
   ```bash
   shape profile "onelake://$FABRIC_WORKSPACE/$FABRIC_LAKEHOUSE/Tables/shape_publicholidays" \
       --name publicholidays --capture full -o holidays-generated.shape
   shape check holidays-generated.shape holidays.contract.json
   ```
   Expected: exit 0, or exit 1 with the violated rules listed (record them).

5. **Drift check.**
   ```bash
   shape profile "onelake://$FABRIC_WORKSPACE/$FABRIC_LAKEHOUSE/Tables/publicholidays" \
       --name publicholidays -o holidays-again.shape
   shape diff holidays.shape holidays-again.shape --fail-on-drift
   shape diff holidays.shape holidays-generated.shape --fail-on breaking --json drift.json
   ```
   Success: the first `diff` exits 0 with `bump: none`; the second exits 0 with `0 breaking`
   (additive and cosmetic changes are expected from generated data).

### W. Warehouse

1. **Profile the real schema** (catalog exact, 1,000 sampled rows per table):
   ```bash
   shape profile-db --server "$FABRIC_SQL_ENDPOINT" --database "$FABRIC_WAREHOUSE" \
       --auth cli --schema dbo --tables Trip,Geography --sample-rows 1000 \
       -o warehouse.shape --json warehouse-summary.json
   ```
   Success: exit 0; `shape inspect warehouse.shape` lists `Trip` and `Geography` with the
   catalog row counts, `sampled_rows: 1000` and `sample_method: checksum spread`; a Warehouse
   declares no keys, so the profile's keys carry `evidence: name` or `data`.

2. **Generate and load with `COPY INTO`** (staged in the Lakehouse's `Files`):
   ```bash
   shape generate --from warehouse.shape \
       --to "warehouse://$FABRIC_SQL_ENDPOINT/$FABRIC_WAREHOUSE" \
       --sink-config "warehouse.staging_path=onelake://$FABRIC_WORKSPACE/$FABRIC_LAKEHOUSE/Files/shape_staging" \
       --sink-config warehouse.schema_name=shape_test --auth cli --write-mode create --yes
   ```
   Success: exit 0; `SELECT COUNT(*) FROM shape_test.Trip` equals the generated row count; the
   staging folder is empty afterwards (the writer removes it, also on failure). A profile made
   with `--sample-rows 1000` generates the catalog's row counts: pass `--rows Trip=20000` to
   cap it. If `warehouse.schema_name` is not accepted as a sink-config key, write into `dbo`
   and record it.
   Alternative: `shape publish retail -t warehouse --connection-string "warehouse://$FABRIC_SQL_ENDPOINT/$FABRIC_WAREHOUSE" --staging-path "onelake://$FABRIC_WORKSPACE/$FABRIC_LAKEHOUSE/Files/shape_staging" --schema-name shape_test --auth cli`.

3. **Contract and drift**, as in L4 and L5 with `warehouse.shape` and a second
   `shape profile-db ... --schema shape_test -o warehouse-generated.shape`:
   `shape check warehouse.shape warehouse.contract.json` (exit 0), then
   `shape diff warehouse.shape warehouse-generated.shape --fail-on breaking` (exit 0, `0 breaking`;
   note that sampled profiles add a `notes` entry about sampling to the result).

### E. Eventhouse and Eventstream

1. **Emit to the Eventhouse** (streaming ingestion; one KQL table per generated table):
   ```bash
   shape emit retail --scale small --table order --max-events 5000 \
       --sink "eventhouse://$FABRIC_KQL_HOST/$FABRIC_KQL_DB" --auth cli --yes --json
   ```
   Success: exit 0 and a report with `"events": 5000`, `"complete": true`; in a KQL queryset
   `['order'] | count` returns 5000 within a minute (streaming ingestion is not deduplicated: a
   retried batch can appear twice, so compare `['order'] | summarize take_any(*) by _shape_table, _shape_seq | count`).

2. **Profile the Eventhouse table.** No `eventhouse://` source in 0.9.1 (gap). Workaround, not
   verified here: turn on **OneLake availability** for the KQL database, then read the Delta copy:
   ```bash
   shape profile "delta+abfss://$FABRIC_WORKSPACE@onelake.dfs.fabric.microsoft.com/$FABRIC_KQL_DB.KQLDatabase/Tables/order" \
       --name order --auth cli -o eventhouse-order.shape
   ```
   Expected if it works: exit 0 and 5,000 rows. Record the outcome either way.

3. **Drift for a stream.** Emit the same seed again into a second table name and compare:
   ```bash
   shape emit retail --scale small --table order --max-events 5000 \
       --sink "eventhouse://$FABRIC_KQL_HOST/$FABRIC_KQL_DB/order_again" --auth cli --yes --json
   ```
   then profile both Delta copies (E2) and `shape diff eventhouse-order.shape eventhouse-order-again.shape --fail-on-drift`.
   Success: exit 0 and `bump: none`.

4. **Emit to the Eventstream** (Custom endpoint source; Event Hubs protocol):
   ```bash
   export SHAPE_EVENTSTREAM_CONNECTION_STRING='<copied from the Custom endpoint, never typed into a file that is committed>'
   shape emit retail --scale small --table order --max-events 1000 \
       --sink eventstream://shape_es --yes --json
   ```
   Success: exit 0, `"events": 1000`, `"complete": true`; the Eventstream's data preview shows
   events with `_shape_table`, `_shape_seq` and the property `shape_key`.

5. **Optional: the plugin's own live tests** (repository checkout; set only the variables you
   have, each test names a missing one):
   ```bash
   pytest -m live plugins/shape-fabric/tests/test_live.py plugins/shape-fabric/tests/test_live_writers.py -q
   ```

## 5. Cleanup and cost

- Lakehouse: delete the table `shape_publicholidays` and the folders `Files/shape_test` and
  `Files/shape_staging` (Lakehouse explorer, or `az storage fs directory delete` against
  `onelake.dfs.fabric.microsoft.com`).
- Warehouse: `DROP TABLE shape_test.Trip; DROP TABLE shape_test.Geography; DROP SCHEMA shape_test;`
- Eventhouse: `.drop tables (['order'], ['order_again'])` in a KQL queryset.
- Eventstream: delete the item (or its Custom endpoint source) so the connection string stops working.
- Delete the workspace `shape-test` when the whole playbook is done, then **pause the F2
  capacity** (Azure portal, the capacity resource, Pause) or let the trial expire.
- Locally: `rm -rf generated *.shape *.json`; `az logout` on a shared machine.

**Cost.** A trial capacity costs nothing. An F2 capacity bills by the hour while running
(about a third of a US dollar per hour at list price) whether or not anything uses it, plus
OneLake storage (cents for these tables); pausing it stops the charge. Streaming ingestion and
the sample tables are well inside F2. Check that the capacity shows **Paused** when you finish.

## 6. Results

| Step | Command | Pass | Fail | Notes (rows, exit code, violated rules, drifted columns) |
|---|---|---|---|---|
| L1 | `shape doctor --fabric` | | | |
| L2 | `shape profile onelake://.../Tables/publicholidays` | | | rows: |
| L3a | `shape generate --from --to delta+abfss://` | | | `COUNT(*)`: |
| L3b | `shape publish -t lakehouse` | | | manifest written: |
| L4 | `shape check` (real, generated) | | | rules: |
| L5 | `shape diff` (same, generated) | | | bump: |
| W1 | `shape profile-db` (dbo) | | | tables, sampled_rows: |
| W2 | `shape generate --from --to warehouse://` | | | `COUNT(*)`: |
| W3 | `shape check`, `shape diff` | | | |
| E1 | `shape emit --sink eventhouse://` | | | events, KQL count: |
| E2 | profile via OneLake availability | | | worked? |
| E3 | `shape diff` two emits | | | |
| E4 | `shape emit --sink eventstream://` | | | events: |
| E5 | `pytest -m live` (fabric) | | | |

Tester, date, Shape version, capacity SKU: ______

## Gaps found while writing this playbook

- No Eventhouse source: `shape profile eventhouse://...` exits 2. Tracked as W9-11 (issue #12;
  `lane/W9-11` has an implementation awaiting acceptance). The OneLake-availability workaround
  in E2 is untested.
- `eventhouse://` is an emitter only: `shape generate --to eventhouse://` exits 2 ("no sink
  writes eventhouse:// targets"); table loads go through `shape emit` or `shape publish -t eventhouse`.
- `onelake://` and `lakehouse://` are not sink schemes for `--to` (sources only); write with
  `delta+abfss://` or `abfss://`, or with `shape publish --base-path onelake://...`.
- `--table-prefix` with `--to delta+abfss://` and `warehouse.schema_name` as a `--sink-config`
  key are documented for the Python writers; whether the command line passes them through was
  not verified here (the steps say what to do if refused).
- Install: plugins are not on PyPI and `v0.9.1` has no tag or release yet; the install line
  needs the wheel set (`README.md`).
