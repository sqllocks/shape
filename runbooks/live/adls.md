# Live test playbook: Azure Data Lake Storage Gen2 (`abfss://`)

Shape 0.9.1 · core, extra `[azure]` (`adlfs`, `azure-identity`, `deltalake`); no plugin ·
checked against `release/0.9.1` on 2026-10-11.

**What 0.9.1 supports on ADLS Gen2.** Reading files (CSV, Parquet, JSONL, Arrow IPC; a file, a
glob or a folder) with `shape profile abfss://<container>@<account>.dfs.core.windows.net/<path>`
and Delta tables with `delta+abfss://...`; writing generated tables as files (`--to abfss://...`,
the landing layout) or Delta tables (`--to delta+abfss://...`); `shape stream --to abfss://` for
rolling files. This is also the long form behind every OneLake path (`fabric.md`).

## 1. Prerequisites

| Item | Smallest thing that works |
|---|---|
| Account | A StorageV2 account with **hierarchical namespace** on, Standard LRS, hot tier; container `shape-test`. |
| Permissions | **Storage Blob Data Contributor** on the container for your Entra user (`az login`), or the account key or a SAS token with read, write, list and delete. |
| Real data to profile | Any Parquet or CSV folder you own; or `shape generate retail --scale small --to abfss://.../real` once, then treat it as the source (see step 2). |
| Local tools | Azure CLI, Python 3.11 to 3.14. Emulator: Azurite (`docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite`) with the short form `abfss://<container>/<path>` and `--sink-config abfss.account_name=devstoreaccount1`. |

```bash
az storage account create -g shape-test -n "$AZURE_STORAGE_ACCOUNT" --sku Standard_LRS --kind StorageV2 --hns true
az storage container create --account-name "$AZURE_STORAGE_ACCOUNT" -n shape-test --auth-mode login
```

## 2. Credentials

First match wins (`docs/plugins/cloud-sources.md`):

| Variable or option | Read by | Meaning |
|---|---|---|
| `--auth cli` (default) on `shape profile`, `shape generate --to`, `shape emit/stream --to` | the `abfss` source and sink | an Entra token from the Azure CLI session; `spn`, `msi`, `device-code` as in `docs/plugins/fabric-auth.md` |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | `DefaultAzureCredential` | a service principal through the SDK (no `--auth`) |
| `--sink-config abfss.account_key=env://AZURE_STORAGE_ACCOUNT_KEY`, `abfss.sas_token=env://AZURE_STORAGE_SAS_TOKEN`, `abfss.connection_string=env://AZURE_STORAGE_CONNECTION_STRING` | the sink | explicit secrets, references only (a literal is refused) |
| `AZURE_STORAGE_ACCOUNT_KEY`, `AZURE_STORAGE_SAS_TOKEN`, `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_ACCOUNT_NAME` | the source and sink | the same from the environment (a Delta table takes a key or SAS, not a connection string) |

## 3. Install

```bash
pip install --find-links "$SHAPE_WHEELS" "sqllocks-shape[domains,azure]==0.9.1"
```

(`README.md`, "Install", explains `$SHAPE_WHEELS`.)

## 4. Test steps

`ADLS_ROOT="abfss://shape-test@$AZURE_STORAGE_ACCOUNT.dfs.core.windows.net"`.

1. **Preflight.** `az storage fs list --account-name "$AZURE_STORAGE_ACCOUNT" --auth-mode login -o table` lists `shape-test`; `shape doctor` says `Result: OK`.
2. **Seed real-looking data once** (skip if you have your own folder):
   ```bash
   export SHAPE_CONFIRM_REMOTE=1
   shape generate retail --scale small --seed 7 --to "$ADLS_ROOT/real" --format parquet --batch-date 2026-10-11 --manifest --auth cli --yes
   ```
   Success: exit 0; `real/order/ingest_date=2026-10-11/order_20261011.parquet` and a
   `_SUCCESS` per table folder (`az storage fs file list -f shape-test --path real --account-name ... --auth-mode login`).
3. **Profile from the lake.**
   ```bash
   shape profile "$ADLS_ROOT/real/order/ingest_date=2026-10-11/order_20261011.parquet" --name order --auth cli -o order.shape
   shape profile "$ADLS_ROOT/real/order/**/*.parquet" --name order --auth cli -o order-glob.shape
   ```
   Success: exit 0 twice with the same `shape_content_id` (5,000 rows, 8 columns for retail
   small's `order`). Delta: `shape profile "delta+$ADLS_ROOT/delta/order" ...` after step 4's
   Delta write.
4. **Generate from the profile and write back**, files and Delta:
   ```bash
   shape generate --from order.shape --rows 20000 --to "$ADLS_ROOT/generated" --format parquet --batch-date 2026-10-12 --manifest --auth cli --yes
   shape generate --from order.shape --rows 20000 --to "delta+$ADLS_ROOT/delta" --auth cli --yes
   ```
   Success: exit 0 twice; `generated/order/ingest_date=2026-10-12/order_20261012.parquet` and
   `_SUCCESS` exist; `delta/order/_delta_log/00000000000000000000.json` exists;
   `shape profile "delta+$ADLS_ROOT/delta/order" --name order --capture full --auth cli -o order-generated.shape` shows 20,000 rows.
5. **Contract check.** `shape proposals propose order.shape -d decisions.json --kinds rule --auto-accept 0.5`;
   `shape proposals contract -d decisions.json -o order.contract.json`;
   `shape check order.shape order.contract.json` (exit 0, no violations);
   `shape check order-generated.shape order.contract.json` (exit 0, or 1 with the rules listed).
6. **Drift check.** `shape profile "$ADLS_ROOT/real/order/**/*.parquet" --name order --auth cli -o order-again.shape`;
   `shape diff order.shape order-again.shape --fail-on-drift` (exit 0, `bump: none`);
   `shape diff order.shape order-generated.shape --fail-on breaking` (exit 0, `0 breaking`).
7. **Streaming files.** `shape stream retail -t order --max-events 3000 --to "$ADLS_ROOT/live" --format jsonl --roll-rows 1000 --auth cli --yes --json`:
   exit 0, `"events": 3000`, three rolled files under `live/order/`.
8. **Optional: emulator and live tests** (repository checkout): `pytest -m emulator tests/builtins/test_cloud_sources_azurite.py -q` against Azurite; the live OneLake and ADLS reads are marked `live` and need the owner's secrets (plan O-02, O-03).

## 5. Cleanup and cost

```bash
az storage fs directory delete --account-name "$AZURE_STORAGE_ACCOUNT" -f shape-test -n real --yes --auth-mode login
az storage fs directory delete --account-name "$AZURE_STORAGE_ACCOUNT" -f shape-test -n generated --yes --auth-mode login
az storage fs directory delete --account-name "$AZURE_STORAGE_ACCOUNT" -f shape-test -n delta --yes --auth-mode login
az storage fs directory delete --account-name "$AZURE_STORAGE_ACCOUNT" -f shape-test -n live --yes --auth-mode login
az storage account delete -g shape-test -n "$AZURE_STORAGE_ACCOUNT" --yes
rm -rf *.shape *.json
```

**Cost.** A few megabytes of hot LRS storage and a few thousand operations: under one US cent.
Nothing runs; deleting the account removes everything (rotate the account key if you used one).

## 6. Results

| Step | Command | Pass | Fail | Notes |
|---|---|---|---|---|
| 1 | preflight | | | |
| 2 | `shape generate --to abfss://` (seed) | | | |
| 3 | `shape profile abfss://` (file, glob) | | | content id: |
| 4 | `shape generate --from --to abfss://`, `delta+abfss://` | | | rows: |
| 5 | `shape check` (real, generated) | | | rules: |
| 6 | `shape diff` | | | bump: |
| 7 | `shape stream --to abfss://` | | | files: |
| 8 | emulator / live tests | | | |

Tester, date, Shape version, account: ______

## Gaps found while writing this playbook

- No emitter writes to storage queues; file streaming (`shape stream --to abfss://`) is the
  streaming path.
- Install: `v0.9.1` has no tag or release yet; core 0.9.1 comes from the wheel set (`README.md`).
