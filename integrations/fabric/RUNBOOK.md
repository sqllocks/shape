# Shape in Microsoft Fabric: runbook

Owner-operated. The builder could not reach a Fabric workspace, so everything below is
**built and tested locally, and unverified live** unless it says otherwise. Steps that
depend on live Fabric behaviour are tagged **[VERIFY]**; the full list is in
[section 10](#10-verify-in-the-workspace-on-first-run) and the owner's checklist is in
[section 11](#11-owner-live-dry-run-checklist).

What you are building:

| Item | Type | Source in this repo |
|---|---|---|
| `shape_demo` | Lakehouse | you create it |
| `shape_setup` | Python notebook | `integrations/fabric/notebooks/shape_setup.ipynb` |
| `shape_profile` | Python notebook (kernel 3.11/3.12) | `notebooks/shape_profile.ipynb` |
| `shape-env` | Environment (Runtime 2.0) | `environment/` |
| `shape_profile_spark` | PySpark notebook | `notebooks/shape_profile_spark.ipynb` |
| `shape_udf` | User Data Functions item | `udf/function_app.py` |
| `shape_gate_notebook`, `shape_gate_spark`, `shape_gate_udf` | Data pipelines | `pipelines/` |

## 1. Prerequisites

- A Fabric workspace on a capacity you can use (trial or F2 or larger), in a **region where
  User Data Functions are available**. Check the User Data Functions item exists under
  *New item*; if it does not, the region or tenant setting is the blocker.
- Workspace role Contributor or higher; a tenant setting allowing User Data Functions and
  Python notebooks.
- The Shape wheel `sqllocks_shape-0.9.0-py3-none-any.whl` from DM-03 (the GitHub release,
  or `python scripts/build_pure_wheel.py` on the merged branch). Once the package is on
  PyPI, `%pip install sqllocks-shape==0.9.0` works instead in notebooks and the UDF
  library list. The wheel must be under 28.6 MB and `py3-none-any` for the UDF.
- The demo data from `demo/make_data.py` (lane L3): Parquet files for day 1 and day 2, and
  `demo/contracts/*.json`. This runbook assumes the layout

  ```
  Files/demo/day1/orders.parquet   Files/demo/day2/orders.parquet   (and the other tables)
  Files/contracts/orders.json
  ```

  If L3 names things differently, change the paths below; nothing in the code depends on
  them except the setup notebook's table naming (`<table>_<day>`, for example `orders_day1`).

## 2. Dry-run every local step first (no Fabric needed)

From the repo root, in a Python 3.11+ environment:

```bash
pip install numpy pandas "pyarrow>=14" deltalake nbformat ipython jsonschema pytest \
    pyspark delta-spark fabric-user-data-functions
# fabric-user-data-functions imports pyodbc: on Linux install the system package unixodbc first.
pytest tests/demo/fabric -q
```

Expected: all tests pass. The header line `fabric lane: shape API under test = real|stub`
tells you whether the real Shape API was used. Before the merge of lane L1 it says `stub`
and the tests prove the notebooks, functions and pipeline definitions are wired
correctly, not that Shape's profiler is right (that is DM-01 and DM-02).

Regenerate the notebooks or pipelines after editing their builders:

```bash
python integrations/fabric/notebooks/build_notebooks.py && ruff format integrations
python integrations/fabric/pipelines/build_pipelines.py
```

## 3. Lakehouse and data

1. **New item > Lakehouse**, name it `shape_demo`.
2. In the lakehouse **Files** area: create `demo/day1`, `demo/day2` and `contracts`, and
   upload the Parquet files and contract JSONs (Upload > Upload files/folder).
3. Import `shape_setup.ipynb` (**Import > Notebook > Upload**), attach `shape_demo` as the
   default lakehouse, and **Run all**.
   - Expected: one printed line per table, for example `orders_day1  ...  rows`. Delta
     tables `orders_day1`, `orders_day2`, ... appear under **Tables** (refresh).
   - Error `No .parquet files under /lakehouse/default/Files/demo` means the default
     lakehouse is not attached or the upload path is wrong.

## 4. Python notebook: `shape_profile`

1. Import `shape_profile.ipynb`, attach `shape_demo` as the default lakehouse.
2. Kernel: **Python 3.11** or **3.12** (bottom-left kernel picker, *not* PySpark).
3. Open **Resources > builtin** in the notebook's left pane and upload the wheel.
   The install cell runs `%pip install builtin/sqllocks_shape-0.9.0-py3-none-any.whl`. **[VERIFY]**
4. The first cell, `%%configure {"vCores": 8}`, is a cell magic that must run before the
   session starts. The default is 2 vCores; Shape's parallel speed-ups need more. **[VERIFY]**
   If the session does not start with 8 vCores, run **Stop session**, then run the cell again
   from a fresh session (it must be the first thing executed).
5. Run with the defaults for **day 1** (`tableName = "orders_day1"`, `contractPath =
   "contracts/orders.json"`, no baseline):
   - Expected: the HTML report renders inline; the exit value (shown in the run output or
     the pipeline) has `"passed": true`, `"violations": []`, `"drifted": false`.
   - Artifacts appear in `Files/shape/orders_day1/<timestamp>/`: `.shape`, `.html`,
     `.summary.json`.
   - **Copy the `artifactPath` from the printed result**. It is your day-1 baseline, for
     example `shape/orders_day1/20260930T120000Z/orders_day1.shape`.
6. Run again for **day 2**: set `tableName = "orders_day2"` and `baselinePath` to the
   artifact path from step 5.
   - Expected: `"passed": false`, violations naming the documented drift rules (see
     `demo/DRIFT.md`), `"drifted": true`, and `changes` listing each documented change.

Exit rule: `notebookutils.notebook.exit(...)` is the **last statement, at top level,
never inside `try`/`except`** (Fabric ignores it or the pipeline gets no value otherwise).
Do not wrap the last cell.

## 5. Environment and PySpark notebook

1. Create the Environment as described in [`environment/README.md`](environment/README.md):
   Runtime **2.0**, upload the wheel as a custom library, **Publish** in **Quick** mode for
   the live demo (about 5 s; notebooks only) or **Full** mode (3 to 6 min publish plus 1 to
   3 min at session start) for pipeline runs. **[VERIFY]**
2. Run the version check in step 7 of that README (numpy 2.x, pyarrow 14+).
3. Import `shape_profile_spark.ipynb`, attach `shape_demo` (default lakehouse) and `shape-env`.
4. Run day 1 then day 2 exactly as in section 4.
   - Expected: the **same** exit JSON as the Python notebook on the same table (the
     `artifactPath` timestamps differ). This is asserted locally by the tests and must be
     re-checked here.
   - On Runtime 2.0 the notebook uses `DataFrame.toArrow()`; on Runtime 1.3 it uses
     `toPandas()`. It prints the Spark version. **[VERIFY]**
   - Driver-side profiling is capped at `DRIVER_ROW_LIMIT = 5_000_000` rows. Above the cap
     the notebook samples and reports `"sampled": true`. Lower the constant for wide tables.

## 6. User Data Functions: `shape_udf`

1. **New item > User Data Functions**, name it `shape_udf`.
2. **Manage connections > Add data connection**: choose the `shape_demo` lakehouse and set
   the alias to **`shapeLakehouse`** (the name in `function_app.py`).
3. **Library management** and the private-wheel steps: follow
   [`udf/requirements.md`](udf/requirements.md) (numpy, pyarrow, pandas from PyPI; the Shape
   wheel as a private library, under 28.6 MB and `py3-none-any`).
4. Open the code editor, replace its content with `udf/function_app.py`, and **Publish**.
   Wait for publishing to finish (library installation can take minutes).
5. Test each function from the portal (**Test** mode) or through the pipeline:

   | Function | Inputs | Expected on day 1 |
   |---|---|---|
   | `profileLakehouseFile` | `filePath = demo/day1/orders.parquet`, `outputPath = shape/orders/udf_day1.shape` | rows, columns, summary; `sampled: false`; the `.shape` is written |
   | `checkProfile` | `profilePath = shape/orders/udf_day1.shape`, `contract = <contents of contracts/orders.json>` | `passed: true` |
   | `profileLakehouseFile` on day 2, then `checkProfile` with `failOnViolation = true` | as above with the day-2 file | **error** whose message lists the violations |
   | `diffProfiles` | `baselinePath` day 1, `currentPath` day 2, `failOnDrift = true` | error `Drift detected: N change(s): ...` |
   | `profileLakehouseTable` | `tableName = orders_day1`, `maxRows = 1000000` | result with `sampled` true only if the table has more rows |
   | `profileDataFrame` | a small DataFrame | summary |

   **[VERIFY]** for each: that the connection object works as the tests assume
   (`connectToFiles().get_file_client(path)` with `get_file_properties().size`,
   `download_file().readall()`, `upload_data(..., overwrite=True)`; `connectToSql()` with
   `TOP (n)` queries), and that a `UserThrownError` message reaches the caller.

Limits to remember: 240 s per call (100 s via the public endpoint), 4 MB request, 30 MB
response. `profileLakehouseFile` refuses files above `maxMegabytes` (default 50) with a
message pointing to the notebook; that message is intentional and is the demo's honest
answer for large inputs.

## 7. Pipelines

Three definitions live in `pipelines/<name>.DataPipeline/` (`pipeline-content.json` and
`.platform`). They hold placeholders (`<<WORKSPACE_ID>>`, `<<NOTEBOOK_ID:shape_profile>>`,
`<<NOTEBOOK_ID:shape_profile_spark>>`, `<<FUNCTION_SET_ID>>`) for the item IDs, which you
only know after creating the items above.

### 7.1 Import route (if your workspace supports importing/Git-syncing item definitions)

Get the GUIDs (workspace ID and each item's ID appear in the item's URL in the portal:
`.../groups/<workspaceId>/<type>/<itemId>`), then:

```bash
python integrations/fabric/pipelines/build_pipelines.py bind ./bound \
    --workspace-id <workspaceId> \
    --notebook shape_profile=<notebookId> \
    --notebook shape_profile_spark=<sparkNotebookId> \
    --function-set <userDataFunctionsItemId>
```

Then create each pipeline from `./bound/<name>.DataPipeline/` through Git integration or
the Fabric Items API (create item with definition). **[VERIFY]** The Functions-activity
property names (`functionSetId`, `functionName`, `parameters`) and the `Fail` activity
`errorCode` field were written from the documented shape and are the most likely to need a
correction. If an import is rejected, use the manual route below.

### 7.2 Manual route (always works; about 10 minutes per pipeline)

**Pipeline (a) `shape_gate_notebook`** (repeat with `shape_profile_spark` for (a2)
`shape_gate_spark`; the spark notebook must have the Environment attached, and the
Environment must be published in **Full** mode)

1. **New item > Data pipeline**, name `shape_gate_notebook`.
2. Add **Parameters** (pipeline canvas > Parameters tab):
   `tableName` string `orders_day1`; `contractPath` string `contracts/orders.json`;
   `baselinePath` string (empty); `outputDir` string `shape`; `failOnDrift` bool `false`.
3. Add a **Notebook** activity. Name: **`ProfileTable`** (the name is used in expressions).
   Settings: workspace, notebook `shape_profile`. **Base parameters**, one per notebook
   parameter, with dynamic content:
   `tableName` (String) `@pipeline().parameters.tableName`, likewise `contractPath`,
   `baselinePath`, `outputDir`, and `failOnDrift` (Bool) `@pipeline().parameters.failOnDrift`.
4. Add an **If Condition** activity named `CheckGate`, connected from `ProfileTable` **On
   success**. Expression (dynamic content), **[VERIFY]**:

   ```
   @json(activity('ProfileTable').output.result.exitValue).passed
   ```

   Fallback if the If Condition rejects a non-boolean or a string: wrap it,
   `@equals(json(activity('ProfileTable').output.result.exitValue).passed, true)`.
   To see what the activity really returns, run once and open the *Output* of `ProfileTable`
   in the run details; the exit value is under `output.result.exitValue` (a JSON string).
5. In the **False** branch add a **Fail** activity named `FailGate`. Error code
   `ShapeGateFailed`. Message (dynamic content), **[VERIFY]**:

   ```
   @concat('Shape gate failed for ', pipeline().parameters.tableName, ': ', string(json(activity('ProfileTable').output.result.exitValue).violations))
   ```
6. Leave the **True** branch empty (or add your next step there). **Validate**, **Save**.

**Pipeline (b) `shape_gate_udf`**

1. New data pipeline `shape_gate_udf`. Parameters: `filePath` string `demo/day1/orders.parquet`;
   `profilePath` string `shape/orders/latest.shape`; `contract` **object** (paste the
   contents of `contracts/orders.json`).
2. Add a **Functions** activity named `ProfileFile`: Type *Fabric user data functions*,
   workspace, the `shape_udf` item, function `profileLakehouseFile`. Parameters:
   `filePath = @pipeline().parameters.filePath`,
   `outputPath = @pipeline().parameters.profilePath`, `maxMegabytes = 50`. **[VERIFY]**
3. Add a second Functions activity `CheckContract`, connected **On success** from
   `ProfileFile`, function `checkProfile`. Parameters:
   `profilePath = @pipeline().parameters.profilePath`,
   `contract = @pipeline().parameters.contract`, `failOnViolation = true`.
4. There is no If Condition: with `failOnViolation = true` the function raises
   `UserThrownError`, so the activity, and therefore the pipeline, fails, with the
   violations in the message. Validate and save.

### 7.3 Expected pipeline results

| Pipeline | Parameters | Expected |
|---|---|---|
| `shape_gate_notebook` | defaults (`orders_day1`) | Succeeded; `CheckGate` takes the True branch |
| `shape_gate_notebook` | `tableName = orders_day2` | **Failed** at `FailGate`; the error message lists the violations |
| `shape_gate_spark` | as above | same results as the Python notebook pipeline |
| `shape_gate_udf` | `filePath = demo/day1/orders.parquet` | Succeeded |
| `shape_gate_udf` | `filePath = demo/day2/orders.parquet` | **Failed** at `CheckContract`; the message lists the violations |

## 8. Parameters reference

| Parameter | Used by | Meaning |
|---|---|---|
| `tableName` | notebooks | Delta table in the default lakehouse; `schema.table` accepted |
| `contractPath` | notebooks | contract JSON, relative to `Files/` (or absolute); empty means no contract |
| `baselinePath` | notebooks | earlier `.shape`, relative to `Files/`; empty means no diff |
| `outputDir` | notebooks | artifacts go to `Files/<outputDir>/<table>/<timestamp>/` |
| `failOnDrift` | notebooks | `true`/`True`/`1` make drift against the baseline fail the gate (strings from a pipeline are accepted) |
| `filePath`, `outputPath`, `maxMegabytes` | UDF | file relative to `Files/`; where to write the `.shape`; size cap in MB (default 50) |
| `tableName`, `maxRows`, `outputPath` | UDF | table via the SQL endpoint; row cap (default 1,000,000, max 5,000,000) |
| `profilePath`, `contract`, `failOnViolation` | UDF | saved `.shape`; contract dict; raise on violations |
| `baselinePath`, `currentPath`, `failOnDrift` | UDF | two saved `.shape` files; raise on drift |

Notebook exit value (compact, well under 1 MB):
`{table, rows, passed, violations, drifted, changes, artifactPath, sampled, truncated}`.
`violations` and `changes` are capped at 100 entries each (`truncated: true` if cut).

## 9. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ModuleNotFoundError: shape` in the Python notebook | the `%pip install` cell did not run or the wheel is not in *Resources > builtin*; kernel must be Python, not PySpark |
| `pip` complains about `pyarrow`/`numpy` versions | Shape needs `numpy>=2,<3` and `pyarrow>=14`. In the Python notebook `%pip install "pyarrow>=14"` and restart the kernel; in the Environment see `environment.yml` |
| Session has 2 vCores | the `%%configure` cell must run first in a fresh session; stop the session and rerun |
| Pipeline gets no exit value / the If Condition fails to evaluate | the `exit` call is inside `try`/`except`, is not the last statement, or the notebook failed earlier; open the Notebook activity output. Do not add code after `exit` |
| Environment changes not picked up | Quick mode applies to notebooks only; use Full mode and wait for the publish to finish before a pipeline run; the first session start after a publish takes 1 to 3 minutes |
| PyPI blocked (outbound access protection) | upload every dependency as a custom wheel to the Environment |
| Spark 1.3 runtime | works: the notebook falls back to `toPandas()`; `toArrow()` needs Spark 4 |
| `Table or view not found` in the Spark notebook | attach the default lakehouse; for schema-enabled lakehouses use `dbo.orders_day1` |
| UDF: `... is N MB, above the 50 MB limit` | by design (240 s limit). Raise `maxMegabytes` only if a call stays well inside the limit, or use the notebook path |
| UDF: publish fails on the library | the wheel must be `py3-none-any` and under 28.6 MB; public libraries come from PyPI |
| UDF: timeout after 100 s | you are calling through the public endpoint (100 s); use the pipeline/portal path (240 s) or a smaller input |
| UDF: parameter rejected | names must be camelCase; `contract` must be a JSON object |
| Notebook says `No .parquet files under ...` | default lakehouse not attached, or data not under `Files/demo/` |

## 10. Verify in the workspace on first run

Not checked live by the builder; each is a risk until you confirm it.

1. `%%configure {"vCores": 8}` is accepted as the first cell of a Python notebook.
2. `%pip install builtin/<wheel>` resolves the uploaded resource.
3. **The exit-value expression** `@json(activity('ProfileTable').output.result.exitValue).passed`
   evaluates in the If Condition (and the fallback with `@equals(...)`).
4. The Fail activity's message expression and `errorCode` field.
5. The Functions-activity JSON property names in the imported pipeline, and that its
   parameters accept the `contract` object.
6. Notebook base parameters: `failOnDrift` arriving as Bool or string (the notebook accepts both).
7. Runtime 2.0's bundled numpy, pyarrow and pandas satisfy `numpy>=2,<3` and `pyarrow>=14`.
8. Quick mode installs the wheel for interactive runs; Full mode for pipeline runs.
9. `DataFrame.toArrow()` on Runtime 2.0; `df.count()` and the driver memory at your table size.
10. The UDF file/SQL client calls listed in section 6 and that `UserThrownError` messages
    and properties are shown to the pipeline.
11. The real Shape API (lane L1) behaves like the stub the tests used: `shape.profile(table,
    name=...)`, `shape.check(p, contractPath)`, `shape.diff(base, cur)`, `shape.save/load`.
    If `pytest tests/demo/fabric` fails after the merge, that is the first thing to read.

## 11. Owner live dry-run checklist

Record timings as measured in `demo/LIVE_TIMINGS.md` (machine, vCores, row counts,
seconds); do not extrapolate.

- [ ] Prerequisites met (section 1); `pytest tests/demo/fabric -q` passes locally and says `real`
- [ ] Lakehouse `shape_demo` created; demo Parquet and contracts uploaded
- [ ] `shape_setup` ran; Delta tables `*_day1` and `*_day2` exist
- [ ] `shape_profile` on `orders_day1`: session shows 8 vCores; `passed: true`; report renders; artifacts written
- [ ] `shape_profile` on `orders_day2` with the day-1 baseline: `passed: false`, expected violations, `drifted: true`
- [ ] Environment `shape-env` published on Runtime 2.0 (Quick); version check ok
- [ ] `shape_profile_spark` day 1 and day 2 give the same exit JSON as the Python notebook
- [ ] Environment re-published in **Full** mode before pipeline (a2)
- [ ] `shape_udf` published; every function in the section 6 table returns as expected
- [ ] Pipeline `shape_gate_notebook`: day 1 succeeds; day 2 fails with violations visible (exit-value expression **verified**)
- [ ] Pipeline `shape_gate_spark`: same
- [ ] Pipeline `shape_gate_udf`: day 1 succeeds; day 2 fails at `CheckContract` with violations
- [ ] Timings for each step recorded in `demo/LIVE_TIMINGS.md`
- [ ] Anything in section 10 that needed a correction is written down (open an issue or tell the lead)
