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
| `shape_profile_spark` | PySpark notebook (driver-side exact profile) | `notebooks/shape_profile_spark.ipynb` |
| `shape_profile_distributed` | PySpark notebook (per-partition profile on the executors; or driver-only exact) | `notebooks/shape_profile_distributed.ipynb` |
| `shape_udf` | User Data Functions item | `udf/function_app.py` |
| `shape_gate_notebook`, `shape_gate_spark`, `shape_gate_udf` | Data pipelines | `pipelines/` |
| `shape_generate` | Python notebook: a domain to lakehouse Delta tables, with its contract (PF-06) | `notebooks/shape_generate.ipynb` |
| `shape_profile_domain` | Python notebook: profile the generated tables, check the domain's contract (PF-06) | `notebooks/shape_profile_domain.ipynb` |
| `shape_generate_gate` | Data pipeline: generate, then profile, then check (PF-06) | `pipelines/shape_generate_gate.DataPipeline` |
| `generateSample` | a function of `shape_udf`: rows of one table of a domain (PF-06) | `udf/function_app.py` |
| `shape_profile_dbt` | Python notebook: profile the dbt models' tables, check the contract and drift, one report with the dbt run results (ISS2-dbt) | `notebooks/shape_profile_dbt.ipynb` |
| `shape_dbt_gate` | Data pipeline: dbt job, then `shape_profile_dbt`, then the gate (ISS2-dbt) | `pipelines/shape_dbt_gate.DataPipeline` |

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

> **The saved profiles contain data values.** A `.shape` file keeps up to the 500 most
> frequent values per column, and each column's minimum and maximum (see the README). The
> notebooks write them under the lakehouse `Files/shape/` folder, so anyone who can read
> that folder can read those values. Grant access as you would to the source tables.

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
3. Open **Resources > builtin** in the notebook's left pane and upload the wheel(s): the
   pure-Python wheel `sqllocks_shape-0.9.0-py3-none-any.whl` and, to run on the Rust kernel,
   the platform wheel for Linux x86_64 (an `abi3` `manylinux` x86_64 `.whl`,
   from the release). The install cell runs `%pip install --find-links builtin
   "sqllocks-shape==0.9.0"`: pip takes the platform wheel when `builtin/` holds one that fits,
   and the pure wheel otherwise. The printed line `Shape 0.9.0, kernel: rust` (and the
   `kernel` key of the exit value) says which one you got; `python` means the pure wheel.
   Without outbound access to PyPI add `--no-index`; numpy and pyarrow come from the runtime. **[VERIFY]**
   **In a pipeline run this cell does nothing unless the activity enables it:** Microsoft
   documents that inline `%pip` is disabled by default in notebook pipeline runs, and that a
   Python notebook cannot attach an Environment (so section 5 does not help here). The shipped
   pipelines `shape_gate_notebook` and `shape_generate_gate` therefore pass the Boolean
   notebook-activity parameter `_inlineInstallationEnabled = true` on every notebook that has
   a `%pip` cell (`pytest tests/demo/fabric/test_inline_install.py` enforces it). Limits from
   the same page: not supported in High Concurrency mode (do not set a session tag on the
   activity), not supported in a reference run (`notebookutils.notebook.run`), and libraries
   are installed again on every run, so keep the pinned `==` versions and the `builtin`
   wheels. **[VERIFY]** (section 10, item 13). For a pipeline that must not install at run
   time, use the PySpark notebook with the Environment (pipeline (a2), section 5).
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
   | `generateSample` | `domain = retail`, `table = customer`, `rows = 100`, `seed = 7` (needs the domains wheel, section 12.1) | a 100-row DataFrame, the same on every call |

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
   For `shape_profile` (not for `shape_profile_spark`) add one more base parameter,
   `_inlineInstallationEnabled` (**Bool**) `true`: without it the notebook's `%pip install` cell
   is skipped in a pipeline run and `import shape` fails. **[VERIFY]**
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

### 5.1 Distributed PySpark notebook: `shape_profile_distributed`

For tables too large for the driver. Import `shape_profile_distributed.ipynb`, attach
`shape_demo` and `shape-env` (Shape must be installed on the executors, which the Environment
does; for the best speed upload the Linux x86_64 platform wheel to the Environment as well as
the pure wheel, so executors run the Rust kernel).

- `mode = "distributed"` (default): every partition is profiled on the executors in bounded mode
  through `mapInArrow`; the driver receives the small partial profiles one partition at a time
  and merges them. No rows reach the driver. The result is a **bounded** profile: counts, min,
  max, mean, variance and null counts are exact; distinct counts (HyperLogLog, about 0.8%),
  quantiles (KLL, rank error about 1%) and top values (SpaceSaving, 64 slots, with error terms)
  are within the bounds recorded in the profile's `error_models`. `partitions` repartitions first.
- `mode = "exact"`: the driver-only exact profile (the same as `shape_profile_spark`), for tables
  that fit in driver memory, and the only mode that checks a contract or diffs a baseline.
- The distributed mode **does not check contracts**: it raises if `contractPath` or `baselinePath`
  is set, and its exit value carries `"checked": false`. Do not use it as a pipeline gate; use
  `shape_profile_spark` or `mode = "exact"` for gates.
- Artifacts (distributed): `<table>.profile.json` (the full bounded profile, strict JSON) and
  `<table>.summary.json` (one line per column). The exit value has the keys of section 8 plus
  `kernel`, `mode` (`bounded` or `exact`) and `checked`.
- Each partition's partial profile is about 20 KB per column; merging is sequential on the driver.
- Merge order is Spark's partition order, so a given partitioning always gives the same profile.

Expected, on the day-1 table: `rows` equals the table's row count, `mode: "bounded"`,
`checked: false`, and the column statistics agree with `shape_profile_spark`'s summary (null
counts exactly, distinct counts within about 1%). **[VERIFY]** `df.rdd.getNumPartitions()`,
`mapInArrow` and `toLocalIterator` on your Runtime, and that the executors can `import shape`.

## 8. Parameters reference

| Parameter | Used by | Meaning |
|---|---|---|
| `tableName` | notebooks | Delta table in the default lakehouse; `schema.table` accepted |
| `contractPath` | notebooks | contract JSON, relative to `Files/` (or absolute); empty means no contract |
| `baselinePath` | notebooks | earlier `.shape`, relative to `Files/`; empty means no diff |
| `outputDir` | notebooks | artifacts go to `Files/<outputDir>/<table>/<timestamp>/` |
| `failOnDrift` | notebooks | `true`/`True`/`1` make drift against the baseline fail the gate (strings from a pipeline are accepted) |
| `mode`, `partitions` | `shape_profile_distributed` | `distributed` (default) or `exact`; repartition count for the distributed mode (0 keeps the table's own) |
| `filePath`, `outputPath`, `maxMegabytes` | UDF | file relative to `Files/`; where to write the `.shape`; size cap in MB (default 50) |
| `tableName`, `maxRows`, `outputPath` | UDF | table via the SQL endpoint; row cap (default 1,000,000, max 5,000,000) |
| `profilePath`, `contract`, `failOnViolation` | UDF | saved `.shape`; contract dict; raise on violations |
| `baselinePath`, `currentPath`, `failOnDrift` | UDF | two saved `.shape` files; raise on drift |

Notebook exit value (compact, well under 1 MB):
`{table, rows, passed, violations, drifted, changes, artifactPath, sampled, truncated, kernel}`
(`shape_profile_distributed` adds `mode` and `checked`).
`violations` and `changes` are capped at 100 entries each (`truncated: true` if cut).

## 9. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Exit value says `"kernel": "python"` but you wanted Rust | the platform wheel is not in *Resources > builtin* (or does not match: Linux x86_64, Python 3.11+); upload it next to the pure wheel and rerun the install cell |
| `ModuleNotFoundError: shape` in a Spark executor (distributed notebook) | Shape is not installed on the executors: attach the Environment and publish it; in Quick mode check the session started after the publish |
| `ModuleNotFoundError: shape` in the Python notebook | the `%pip install` cell did not run or the wheel is not in *Resources > builtin*; kernel must be Python, not PySpark. In a **pipeline run** inline `%pip` is off unless the notebook activity passes the Boolean parameter `_inlineInstallationEnabled = true` (section 4) |
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
2. `%pip install --find-links builtin "sqllocks-shape==<version>"` resolves the uploaded
   resources, and picks the platform wheel (kernel `rust`) when it is uploaded.
3. **The exit-value expression** `@json(activity('ProfileTable').output.result.exitValue).passed`
   evaluates in the If Condition (and the fallback with `@equals(...)`).
4. The Fail activity's message expression and `errorCode` field.
5. The Functions-activity JSON property names in the imported pipeline, and that its
   parameters accept the `contract` object.
6. Notebook base parameters: `failOnDrift` arriving as Bool or string (the notebook accepts both).
7. Runtime 2.0's bundled numpy, pyarrow and pandas satisfy `numpy>=2,<3` and `pyarrow>=14`.
8. Quick mode installs the wheel for interactive runs; Full mode for pipeline runs.
9. `DataFrame.toArrow()` on Runtime 2.0; for `shape_profile_distributed`: `mapInArrow` and
   `toLocalIterator` with Shape installed on the executors; `df.count()` and the driver memory at your table size.
10. The UDF file/SQL client calls listed in section 6 and that `UserThrownError` messages
    and properties are shown to the pipeline.
11. The real Shape API (lane L1) behaves like the stub the tests used: `shape.profile(table,
    name=...)`, `shape.check(p, contractPath)`, `shape.diff(base, cur)`, `shape.save/load`.
    If `pytest tests/demo/fabric` fails after the merge, that is the first thing to read.
12. The generation items (PF-06): section 12.6.
13. **`_inlineInstallationEnabled` (issue #7).** Run `shape_gate_notebook` from the pipeline (not
    interactively): the `%pip install --find-links builtin ...` cell must install Shape (the
    run output shows `Shape <version>, kernel: ...`) with the Boolean base parameter
    `_inlineInstallationEnabled = true`; without it the run must fail at `import shape`
    (that is the documented default, which confirms the parameter is what turns it on). Also
    check that the builtin wheels install in a pipeline run, that the parameter is accepted as
    a Boolean in the imported definition, and that no High Concurrency session tag is set.
    If a pipeline run still cannot install, run the gate with the PySpark notebook and the
    Environment (pipeline (a2)) instead.

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
- [ ] Pipeline run of `shape_profile` installs Shape with `_inlineInstallationEnabled = true` (section 10, item 13); without the parameter it fails at `import shape`
- [ ] `shape_profile` exit value reports `"kernel": "rust"` with the platform wheel uploaded (and `python` with only the pure wheel)
- [ ] `shape_profile_distributed` (`distributed`) on a large table: finishes, `rows` equals the table's count, `checked: false`, `<table>.profile.json` written; with a contract set it fails with the explanatory error
- [ ] `shape_profile_distributed` (`exact`) equals `shape_profile_spark` on day 1 and day 2
- [ ] Environment re-published in **Full** mode before pipeline (a2)
- [ ] `shape_udf` published; every function in the section 6 table returns as expected
- [ ] Pipeline `shape_gate_notebook`: day 1 succeeds; day 2 fails with violations visible (exit-value expression **verified**)
- [ ] Pipeline `shape_gate_spark`: same
- [ ] Pipeline `shape_gate_udf`: day 1 succeeds; day 2 fails at `CheckContract` with violations
- [ ] Timings for each step recorded in `demo/LIVE_TIMINGS.md`
- [ ] The generation checklist (section 12.7)
- [ ] Anything in section 10 that needed a correction is written down (open an issue or tell the lead)


## 12. Generating data in pipelines (PF-06)

Everything here runs on the generation engine, so what a notebook or function generates is what
`shape generate` writes for the same domain, scale and seed. **Built and tested locally
(`pytest tests/demo/fabric/test_generate.py`), not run in a Fabric workspace.**

### 12.1 Install the domains

A domain is a plugin: next to the Shape wheel, upload the wheel `sqllocks_shape_domains-0.9.0-py3-none-any.whl`
(`pip wheel --no-deps plugins/shape-domains`, or the release) to *Resources > builtin* of each
notebook (a Python notebook cannot attach an Environment). The install cell of both notebooks is
`%pip install --find-links builtin "sqllocks-shape==0.9.0" "sqllocks-shape-domains==0.9.0"`. For
Both notebooks run from the pipeline `shape_generate_gate`, which passes
`_inlineInstallationEnabled = true` to each of them (inline `%pip` is off in pipeline runs
otherwise; see section 4). For
`generateSample`, add the same wheel as a **private library** of `shape_udf` (it is
`py3-none-any` and about 2 MB, far under the 28.6 MB limit). **[VERIFY]** that the UDF library
resolver accepts a private library whose requirement `sqllocks-shape==0.9.0` is another private
library (otherwise list `sqllocks-shape==0.9.0` as a public library, which works once it is on PyPI).

### 12.2 `shape_generate`: a domain into Delta tables

1. Import `shape_generate.ipynb`, attach the default lakehouse (`shape_demo`).
2. Run it with the defaults (`domain = "retail"`, `scale = "small"`, `seed = 42`).
   - Expected: nine Delta tables (`customer`, `address`, `product_category`, `product`, `store`,
     `promotion`, `order`, `order_line`, `return`) under *Tables*, 21,750 rows in all; the exit
     value lists each table with its row count and `contractPath = "shape/retail/contract.json"`.
   - `Files/shape/retail/contract.json` is the contract that the domain's own schema implies for
     these tables (exact row counts, required columns, no extra columns, types, `nullable: false`,
     null-rate limits, primary-key uniqueness, enumerated values); `generation.json` records the run.
3. Parameters: `domain`; `scale` (a preset of the domain: `small`, `medium`, ...; `shape presets`);
   `seed` (the same seed always gives the same rows); `mode` (`3nf` or `star`; empty is the domain's
   default); `tablePrefix` (tables are `<tablePrefix><table>`); `writeMode` (`overwrite` is repeatable,
   `append` adds the rows again); `outputDir`. Names are checked: letters, digits and underscores only.
4. The data is generated in the notebook's memory: a Python notebook with 8 vCores (64 GB) takes
   `small` and `medium` comfortably; use a smaller scale, or `shape generate` on a Batch node
   (ADF runbook), for more.
5. Timestamps are stored as microseconds (Delta has no nanosecond type), so a table read back has
   `timestamp[us]` where the engine's own output has `timestamp[ns]`; the values are equal.

### 12.3 `shape_profile_domain`: profile, then check the contract

1. Import `shape_profile_domain.ipynb`, attach `shape_demo`, run it after `shape_generate` with the
   same `tablePrefix`.
   - Expected: `"passed": true`, `"violations": []`, `rows` 21,750, a `tables` object with each
     table's row count; the HTML report renders (all nine tables, with the relationships the
     profiler detected); artifacts in `Files/shape/retail/<timestamp>/`.
2. To see it fail: overwrite a table (for example delete rows from `customer`), run again. Expected:
   `"passed": false` and a violation such as `customer:row_count.min`; a missing table makes the
   notebook fail with an error naming it; it never passes without reading every table in the contract.
3. `baselinePath` (an earlier `.shape` artifact) and `failOnDrift` work as in section 4.

### 12.4 Pipeline `shape_generate_gate`

`GenerateDomain` (notebook `shape_generate`) -> `ProfileAndCheck` (notebook `shape_profile_domain`,
which is given the contract path the first notebook returned) -> `CheckGate` (If Condition on
`passed`; **False** runs `FailGate`, error code `ShapeContractFailed`).

- Import it as in section 7.1: add `--notebook shape_generate=<id> --notebook shape_profile_domain=<id>`
  to the `bind` command. Or build it by hand as in 7.2 with the activities above.
- Parameters: `domain`, `scale`, `seed`, `mode`, `tablePrefix`, `writeMode`, `outputDir`,
  `baselinePath`, `failOnDrift`. The contract path is not a parameter: the second notebook reads it
  from the first one's exit value, **[VERIFY]**:

  ```
  @json(activity('GenerateDomain').output.result.exitValue).contractPath
  ```
- The gate expression is `@json(activity('ProfileAndCheck').output.result.exitValue).passed`
  (**[VERIFY]**, the same expression family as section 7.2).
- Expected: with the defaults it succeeds and the If Condition takes the True branch. After
  damaging a table between the two notebooks (or running only `shape_profile_domain` on a damaged
  table) it fails at `FailGate`, with the violations in the message.

### 12.5 `generateSample` (User Data Function)

`generateSample(domain: str, table: str, rows: int = 10000, seed: int = 42) -> pd.DataFrame`.
Test it from the portal, for example `domain = retail`, `table = customer`, `rows = 100`, `seed = 7`:

- Expected: a 100-row DataFrame with the customer columns, identical on every call with the same
  arguments; `rows = 0`, an unknown domain or table, or a name with characters other than letters,
  digits and underscores gives an error that names the problem.
- It generates the whole domain at its default scale (the computed columns and business rules need the
  related tables), with the requested table at `rows` rows, and returns that one table. With `rows` equal to
  the table's count at that scale (`customer` 1,000, `order` 5,000, `order_line` 12,500, ... for `retail`) it
  is exactly that table of a full run with the same seed: the tests assert this for all nine retail tables.
  With another `rows` the foreign keys are valid and the context tables are unchanged.
- `rows` is capped at 500,000, and the response is cut to the leading rows that fit in 25 MB of JSON
  (the response limit is 30 MB). Nothing says the cut happened except the row count: compare it with
  `rows`.
- The domain and table are looked up among the installed domains and the schema's tables; nothing the
  caller writes is used to build a path or a statement.
- **[VERIFY]** that a `pd.DataFrame` return of this size is serialized and returned within the limits
  (30 MB response, 240 s), and the time of the first call (it imports the domain's reference data).

### 12.6 Verify in the workspace on first run (PF-06)

1. The two-wheel install line resolves in a notebook (section 12.1), and the exit value reports the kernel.
   In the pipeline run both notebooks install with `_inlineInstallationEnabled = true` (section 10, item 13).
2. The Delta tables the notebook writes under `/lakehouse/default/Tables` appear in the lakehouse
   explorer and the SQL endpoint without a refresh step (a Python notebook writes the files itself).
3. `/lakehouse/default/Files/...` is writable from the Python notebook for `contract.json`.
4. Both exit-value expressions of section 12.4.
5. `generateSample` returns a DataFrame this size through the portal and the Functions activity.

### 12.7 Owner live dry-run checklist (PF-06)

- [ ] The domains wheel is installed (notebooks and `shape_udf`)
- [ ] `shape_generate` (defaults): nine tables, 21,750 rows, `contract.json` written; a second run with the same seed leaves the same tables
- [ ] `shape_profile_domain`: `passed: true`; after damaging a table, `passed: false` with the named violation
- [ ] Pipeline `shape_generate_gate`: succeeds with the defaults (both exit-value expressions **verified**); fails at `FailGate` on a damaged table
- [ ] `generateSample` called from the portal and from a Functions activity: row count, determinism, and the errors above
- [ ] Timings (generate, profile, `generateSample`) recorded in `demo/LIVE_TIMINGS.md`


## 13. dbt in pipelines (ISS2-dbt)

The pattern, the commands and the report are in [docs/DBT.md](../../docs/DBT.md). **Built and
tested locally, and against DuckDB (`pytest -m dbt plugins/shape-dbt/tests`,
`pytest tests/demo/fabric/test_dbt.py`); not run in a Fabric workspace.** The dbt job is a Fabric
preview (tenant setting "dbt jobs (preview)").

`RunDbt` (the dbt job) -> `ProfileDbtOutputs` (notebook `shape_profile_dbt`, run when the dbt job
has *completed*, so a failed dbt test still gives the report) -> `CheckGate` (If Condition on
`passed`; **False** runs `FailGate`, error code `ShapeDbtGateFailed`).

1. Upload `sqllocks_shape_dbt-0.9.0-py3-none-any.whl` (`pip wheel --no-deps plugins/shape-dbt`) to
   *Resources > builtin* next to the Shape wheel; the notebook installs both with `%pip` and the
   pipeline passes `_inlineInstallationEnabled = true` (section 4).
2. Import `shape_profile_dbt.ipynb`, attach the lakehouse that holds the dbt models' tables.
3. Create the pipeline with `bind` as in section 7.1, adding `--notebook shape_profile_dbt=<id>
   --dbt-job <id>`, or build it by hand as in 7.2 with the activities above.
4. Parameters: `dbtCommand` (`build`), `models` (comma-separated model names; each is a Delta table
   `<tableRoot>/<model>`), `tableRoot`, `contractPath` (a multi-table contract; `shape to-dbt-tests`
   compiles the same contract to dbt tests), `baselinePath` (an earlier `.shape` of the models),
   `runResultsPath` and `manifestPath` (the dbt job's `run_results.json` and `manifest.json`, under
   `Files/` or absolute), `outputDir`, `failOnDrift`.
5. Expected exit value: `{models, rows, passed, dbtFailed, dbtTotal, violations, drifted, changes,
   byColumn, artifactPath, reportPath, truncated, kernel}`; `Files/shape/dbt/<timestamp>/` has
   `report.md`, `report.json`, `dbt.shape` and `dbt.html`.

### 13.1 Verify in the workspace on first run (ISS2-dbt)

1. **The dbt job activity.** `RunDbt` has the type `DbtJob` with `dbtJobId`, `workspaceId` and
   `command`. These are the builder's reading of the preview's item model and are **not** taken
   from a real export: replace the activity with one exported from a workspace where the dbt job
   preview is enabled (and keep its name, `RunDbt`, and the `Completed` dependency of the next
   activity). If the preview has no pipeline activity, run the dbt job on its own schedule and start
   `shape_profile_dbt` after it.
2. Where the dbt job writes `run_results.json` and `manifest.json` in OneLake. Microsoft Learn names
   `manifest.json` and `catalog.json` for `docs generate`; whether a `build` or `test` run leaves
   `run_results.json` next to them is not confirmed. The notebook takes both paths as parameters.
3. How a notebook reads the model tables: Lakehouse tables (`dbt-fabricspark`) are Delta tables of
   the default lakehouse; Warehouse tables (`dbt-fabric`) need an absolute OneLake path in
   `tableRoot`, which was not tried.
4. The exit-value expressions of `CheckGate` and `FailGate` (as in section 10, item 3 and 4), and
   `_inlineInstallationEnabled` for this notebook (item 13).
5. The dbt runtime's version of dbt Core (documented as 1.11) reads the `arguments:` form of test
   arguments that `shape to-dbt-tests` writes (dbt 1.10 and later), and that `dbt_utils` and
   `dbt_expectations` install from the package hub in the job. If not, use `--args-style inline`.
6. Seed load time of a Shape seed on `dbt-fabric` and `dbt-fabricspark` (docs/DBT.md, size guidance).

### 13.2 Owner live dry-run checklist (ISS2-dbt)

- [ ] The dbt preview is enabled; a dbt job with the sample project (`examples/dbt_jaffle_shop`, seeds from `shape dbt-seeds`) runs `build` and passes
- [ ] The location of `run_results.json` and `manifest.json` is written down (13.1 item 2)
- [ ] `shape_profile_dbt` run by hand after the job: `passed: true`, `dbtFailed: 0`, `report.md` renders
- [ ] A failing dbt test (for example a tampered model): `passed: false`, `dbtFailed >= 1`, the column appears in `report.md`
- [ ] Pipeline `shape_dbt_gate`: succeeds on the clean run; fails with `ShapeDbtGateFailed` on the failing one, and the report path is in the message
- [ ] Anything in 13.1 that needed a correction is written down (open an issue or tell the lead)
## 14. Profile a semantic model (W2-06)

In a Fabric notebook (a Python notebook with `semantic-link-sempy`, which Fabric runtimes ship, or
`%pip install 'sqllocks-shape-fabric[semantic-link]'`), one cell reads a table of a semantic model
and one profiles the whole model with its relationships:

```python
import shape

# one table: semantic-model://<workspace>/<model>/<table>
prof = shape.profile("semantic-model://Sales/Retail/Customer")
shape.save(prof, "/lakehouse/default/Files/shape/customer.shape")

# the whole model, with its declared relationships (same as `shape profile-model Sales/Retail`)
from shape_fabric.semantic_profile import profile_model

model = profile_model("Sales", "Retail", max_rows=100_000)
shape.save(model, "/lakehouse/default/Files/shape/retail.shape")
```

Workspace and model are names or GUIDs. See [cloud-sources.md](../../docs/plugins/cloud-sources.md#semantic-models-semantic-model)
and [fabric-commands.md](../../docs/plugins/fabric-commands.md#profile-model). **[VERIFY]** the
first run in a real tenant (section 11): the column and relationship tables `sempy` returns are
read by the names the fake in the tests uses.
