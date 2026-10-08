# Shape in Azure Data Factory: runbook

Owner-operated. **Built and tested locally; not run in a Data Factory or on a Batch pool.** The
gate script runs against the real Shape CLI with local storage (and a fake `adlfs` for the
`abfss://` paths), and the pipeline definition is checked at schema level with its expressions
evaluated on the gate documents the script writes. Everything that needs Azure is tagged
**[VERIFY]** and listed in [section 6](#6-verify-in-the-workspace-on-first-run) and the
[dry-run checklist](#7-live-dry-run-checklist).

## How the gate works

```
Custom activity ProfileAndCheck --Completed--> Lookup ReadGate --Succeeded--> If CheckGate
  (Batch node: docker run <shape image>          (reads gate.json from ADLS)    passed -> done
   python run_gate.py)                                                          else   -> Fail
```

1. The **Custom activity** runs the Shape container (`ghcr.io/sqllocks/shape`, `docs/CONTAINER.md`)
   on an Azure Batch pool. `batch/run_gate.py` calls the Shape CLI (`shape profile`, `shape check`,
   `shape diff`) and **exits with the gate's exit code**: 0 passed, 1 a contract violation (or drift
   with `failOnDrift`), 2 an error. The activity **fails on any non-zero exit code**.
2. The script always writes `gate.json` (plus `profile.shape` and `summary.json` when the profile
   was built) to `abfss://<outputFileSystem>@<storageAccount>.dfs.core.windows.net/<outputFolder>/<RunId>/`,
   even when the gate failed.
3. The **Lookup** runs on `Completed` (so also after the failed Custom activity), reads `gate.json`
   through the `ShapeGateJson` dataset, and the **If Condition** branches on `firstRow.passed`; the
   false branch is a **Fail** activity whose message is the violations (or the error).

`gate.json`: `{passed, exitCode, rowCount, violations[], drifted, changes[], artifactUrl, error, truncated}`
(lists capped at 100). The pipeline fails when the gate does; if Batch itself fails before writing
`gate.json` the Lookup fails and so does the pipeline.

## 1. Prerequisites

- **Azure Batch** account and a **Linux pool with Docker**: a container-enabled VM image (for
  example a Batch `ubuntu-server-container` image); prefetch the Shape image through the pool's
  `containerConfiguration`, or let the command pull it. **[VERIFY]**
- The Shape image (PF-05), built and published by CI on a version tag. Until then build it
  yourself (`docker build -t shape .`) and push it to a registry the pool can reach; a private
  registry needs credentials in the pool's container configuration. Set the pipeline parameter
  `image` accordingly.
- Storage: a **Blob/ADLS** account for the scripts (`ShapeBatchStorage`), and an **ADLS Gen2**
  container for outputs and contracts.
- Identities and roles: the Batch pool's **managed identity** (assign it to the pool) needs
  *Storage Blob Data Reader* on the data and contracts and *Storage Blob Data Contributor* on the
  output container. The **factory's managed identity** needs *Storage Blob Data Reader* on the
  output container (the Lookup). For a user-assigned identity set `managedIdentityClientId`; it is
  exported to the container as `AZURE_CLIENT_ID` (`docs/CONTAINER.md`).
- The Batch account key goes in **Key Vault**; `ShapeBatch` references the secret by name
  (`ShapeKeyVault`). No key appears in this repo's JSON.

## 2. Dry-run every local step first (no Azure needed)

```bash
pip install -e '.[dev]' -r tests/demo/fabric/requirements.txt
pytest tests/demo/fabric/test_adf.py -q
# the gate script by hand, against local files:
python integrations/adf/batch/run_gate.py --activity settings.json
```

where `settings.json` holds `{"sourceUrl": "data/orders.parquet", "contractUrl":
"contracts/orders.json", "outputUrl": "out/run-1"}` (local paths work as well as `abfss://` URLs).

## 3. Publish the scripts and the factory definitions

1. Upload `batch/run_gate.py` to the Blob container behind `ShapeBatchStorage`, folder
   `shape-batch` (the pipeline parameter `scriptsFolder`). The Custom activity downloads that
   folder to the task's working directory.
2. Regenerate if you edit the builder: `python integrations/adf/build_adf.py && ruff format
   integrations`. Fill the placeholders in `factory/` (`<<STORAGE_ACCOUNT>>`, `<<BATCH_ACCOUNT>>`,
   `<<BATCH_REGION>>`, `<<BATCH_POOL>>`, `<<BATCH_KEY_SECRET_NAME>>`, `<<KEY_VAULT>>`).
3. Import the definitions into the factory (Git integration layout: `pipeline/`, `dataset/`,
   `linkedService/`), or create the linked services in the UI and paste the pipeline and dataset JSON.

## 4. Run the pipeline `shape_gate_batch`

Parameters: `sourceUrl` (an `abfss://` file, folder or Delta table; anything `shape profile` reads),
`contractUrl` (`abfss://` JSON, empty for none), `baselineUrl` (an earlier `profile.shape`, empty
for none), `failOnDrift`, `storageAccount`, `outputFileSystem`, `outputFolder`, `image`,
`scriptsFolder`, `managedIdentityClientId`.

- **Expected, day 1:** Custom activity Succeeded (exit 0); Lookup Succeeded; `CheckGate` takes the
  true branch; pipeline Succeeded; `profile.shape`, `summary.json`, `gate.json` appear under
  `<outputFolder>/<RunId>/`.
- **Expected, day 2** (contract violated): Custom activity **Failed** (exit 1); Lookup Succeeded;
  `FailGate` fails the pipeline with `Shape gate failed for <sourceUrl>: [<violations>]`;
  `gate.json` has `passed: false`.
- **Error** (bad source, bad contract): exit 2; `gate.json` has `error`; the message shows it.

The command the activity runs (`docker run ... python /work/run_gate.py --activity
/work/activity.json`) takes its settings from `activity.json`, which ADF writes from the
activity's `extendedProperties`; nothing user-supplied is spliced into the shell command except the
image name.

## 5. Exit codes

| Exit code | Meaning | Custom activity |
|---|---|---|
| 0 | profile written, contract and drift checks passed | Succeeded |
| 1 | contract violation, or drift with `failOnDrift` | Failed |
| 2 | error: unreadable source, bad contract or baseline, storage failure | Failed |

## 6. Verify in the workspace on first run

Not checked live; each is a risk until you confirm it.

1. Docker works from the Batch task: the task user may run it (`autoUserSpecification:
   "pool-admin"` in the Custom activity is a guess at the right value), the image pulls, and
   `$AZ_BATCH_TASK_WORKING_DIR` mounts read-write for the container user (`--user "$(id -u):$(id -g)"`).
2. The Custom activity downloads `folderPath` to the working directory and writes `activity.json`
   with `typeProperties.extendedProperties` (the script reads that path).
3. ADF stores expression values inside `extendedProperties` as `{"value": "@...", "type":
   "Expression"}`; export the pipeline after wiring one by hand and compare.
4. The container reaches storage with the pool's managed identity (IMDS from inside the container),
   including a user-assigned identity through `AZURE_CLIENT_ID`.
5. The Lookup returns the JSON file's top-level object as `firstRow` (so `firstRow.passed` is a
   boolean and `firstRow.violations` an array); the dataset's `AzureBlobFSLocation` property names.
6. The If Condition expression `@activity('ReadGate').output.firstRow.passed` and the Fail message.
7. The Lookup still runs and succeeds when the Custom activity failed (`Completed` dependency),
   and the pipeline's final status is Failed because of `FailGate`.

## 7. Live dry-run checklist

Record timings as measured (pool size, rows, seconds); do not extrapolate.

- [ ] Prerequisites met (section 1); `pytest tests/demo/fabric/test_adf.py -q` passes locally
- [ ] Image available to the pool; `docker run ... shape --version` works on a node
- [ ] `run_gate.py` uploaded to `shape-batch`; placeholders in `factory/` replaced; definitions imported
- [ ] Day 1: pipeline Succeeded; `gate.json`, `profile.shape`, `summary.json` in ADLS
- [ ] Day 2: Custom activity Failed with exit code 1; `FailGate` fails the pipeline with the violations in the message
- [ ] A bad `sourceUrl`: exit 2, `gate.json.error` explains it, the pipeline fails with that message
- [ ] A user-assigned identity: `managedIdentityClientId` set, reads and writes succeed
- [ ] `baselineUrl` + `failOnDrift = true`: drift fails the gate
- [ ] Anything in section 6 that needed a correction is written down (open an issue or tell the lead)

## 8. Generating data in a pipeline: `shape_generate_gate_batch` (PF-06)

Built and tested locally: `pytest tests/demo/fabric/test_generate_adf.py -q` runs the script against
the real Shape CLI with local storage, and evaluates the pipeline's expressions on the gate documents it
writes. **Not run in a Data Factory or on a Batch pool** (the same limits as sections 1 to 7).

```
Custom activity GenerateAndCheck --Completed--> Lookup ReadGate --Succeeded--> If CheckGate
  (Batch node: docker run <shape image>          (reads gate.json from ADLS)    passed -> done
   python run_generate_gate.py)                                                  else   -> Fail
```

`batch/run_generate_gate.py` (it imports `run_gate.py`, so upload both to `shape-batch`) calls the Shape CLI:
`shape generate <domain> --scale S --seed N [--mode M] --format parquet`; profiles the folder of tables in
process, as one dataset (`shape profile` on a folder reads it as a single table, which a multi-table contract
cannot be checked against); `shape check` against the contract that the domain's own schema implies for exactly those
tables (exact row counts, required columns, no extra columns, types, nullability, primary-key uniqueness,
enumerated values), and, with `baselineUrl`, `shape diff`. It **exits with the gate's exit code**
(0 passed, 1 the generated data broke the contract or drifted with `failOnDrift`, 2 an error), so the
Custom activity fails exactly when the gate does. It writes `data/<table>.parquet`, `contract.json`,
`profile.shape`, `summary.json` and `gate.json` to
`abfss://<outputFileSystem>@<storageAccount>.dfs.core.windows.net/<outputFolder>/<RunId>/`, even when the
gate failed (an error gate has only `gate.json`). `gate.json` has the keys of section "How the gate
works" plus `domain`, `tables` (rows per table) and `contractUrl`.

1. The image must carry the domain. The PF-05 image installs `sqllocks-shape` only, and a domain is a
   plugin (`sqllocks-shape-domains`), so without it the script exits 2 with `no domain named 'retail'
   (installed: none installed)`. Build a derived image and use it as the `image` parameter:

   ```dockerfile
   FROM ghcr.io/sqllocks/shape:0.9.1
   USER root
   RUN pip install --no-cache-dir "sqllocks-shape-domains==0.9.1"
   USER shape
   ```

   (Before the domains package is on PyPI, `pip install` the wheel built with `pip wheel --no-deps
   plugins/shape-domains` instead.) **[VERIFY]** that the derived image runs `shape generate retail` as the
   non-root user, and the image size stays reasonable (the plugin is about 2 MB).
2. Parameters: `domain`, `scale`, `seed`, `mode` (`3nf`, `star` or empty), `baselineUrl`, `failOnDrift`,
   `storageAccount`, `outputFileSystem`, `outputFolder`, `image`, `scriptsFolder`,
   `managedIdentityClientId`. Settings travel in `activity.json`, so nothing user-supplied is spliced
   into the shell command except the image name; the domain, scale and mode are also validated by the
   script (letters, digits and underscores only), and a scale with more than `maxRows` rows (50,000,000 by
   default; not a pipeline parameter) is refused before anything is generated.
3. **Expected, defaults** (`retail`, `small`, seed 42): Custom activity Succeeded (exit 0); nine tables, 21,750
   rows, under `<outputFolder>/<RunId>/data/`; `CheckGate` takes the true branch.
4. **Expected, a broken table** (for example replace one parquet file under `data/` and rerun the
   check by hand, or lower `maxRows` to see exit 2): Custom activity **Failed** (exit 1 or 2); `FailGate`
   fails the pipeline with `Shape generated data broke the contract of <domain>: [<violations>]` (or the
   error).
5. The data is generated and profiled on the Batch node: size the pool for `scale` (the node's disk holds
   the Parquet files, its memory the profile).

### 8.1 Verify in the workspace on first run (PF-06)

1. Everything in section 6, for the second Custom activity.
2. The image has the domains (step 1 above), and `shape generate --format parquet` writes under
   the task's working directory as the container user.
3. Uploading about 20 files per run through `adlfs` with the pool's managed identity.
4. The Lookup reads `gate.json` whose `tables` is an object (it is read as `firstRow`); the message
   expression uses only `firstRow.error` and `firstRow.violations`.

### 8.2 Live dry-run checklist (PF-06)

- [ ] `run_generate_gate.py` and `run_gate.py` uploaded to `shape-batch`; the derived image (step 1) has `sqllocks-shape-domains`
- [ ] Defaults: pipeline Succeeded; nine Parquet tables, `contract.json`, `profile.shape`, `summary.json`, `gate.json` in ADLS; a second run with the same seed writes the same tables
- [ ] A bad domain (`nope`): exit 2, `gate.json.error` names it, the pipeline fails with that message
- [ ] A broken table (edit one Parquet file and rerun the check, or use a test image): exit 1, `FailGate` shows the violations
- [ ] `baselineUrl` + `failOnDrift = true` with another seed: drift fails the gate
- [ ] Timings (generate, profile, upload) recorded; anything in section 8.1 that needed a correction is written down
