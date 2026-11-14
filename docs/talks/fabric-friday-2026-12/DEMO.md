# Fabric Friday demo runbook

All source tables are invented, Shape-generated data. December 4 and 5 are **fixture
batch dates**, not a confirmed broadcast date. The guest is Jonathan Stewart on Data
on Wheels' Fabric Friday (Fridays, 8:00–9:30 AM ET, early December 2026).

The offline path below is the delivery gate. Fabric steps are **Fabric-only, awaiting
an owner workspace rehearsal**; local tests do not prove workspace authentication,
Environment publishing, Spark conversion, or pipeline expressions work in the tenant.
Do not claim a successful Fabric run until that rehearsal is recorded.

## Preparation

From the repository root run `make bootstrap`. All subsequent development commands use
`uv run --locked`; do not install packages during the presentation. Keep this checkout
and its populated uv cache on the fallback laptop. The verifier uses `UV_OFFLINE=1` and `--no-sync` to reuse the prepared environment.

Fixture provenance: `data/generate_samples.py` calls Shape with authored evidence,
seeds 1204 (240 orders) and 1206 (60 customers). Day 2 derives from day 1 with explicit
fault injection: 12 backdated dates, one invalid code, 24 new status values, and amounts
multiplied by 1.4. There is no production or third-party input. Fixtures are committed as text CSV for the talk text scan. Offline setup converts
them to Parquet; regeneration is verified against committed Arrow table values.

Create workspace `shape_friday`, source Lakehouse `shape_demo` and separate destination
Lakehouse `shape_dev`. Upload the three CSV files to source
`Files/fabric-friday/`; upload the contract created below as
`Files/contracts/friday_orders.json`.

**Environment install (Fabric-only):** follow
[Environment instructions](../../../integrations/fabric/environment/README.md): create
`shape-env`, choose Runtime 2.0, upload the exact tested Shape wheel and its dependencies
as custom libraries, publish in **Full** mode for pipelines, and start a fresh session.
Record `shape.__version__`, numpy and pyarrow versions. Import
`integrations/fabric/notebooks/shape_profile_spark.ipynb`, attach `shape-env` and default
Lakehouse `shape_demo`. Never use an inline install cell on this path. Python notebooks
cannot attach an Environment; the shipped `shape_generate.ipynb` is a Python notebook
and therefore needs the separate path explained in demo 4.

In a preparation PySpark cell, load each uploaded file into a source Delta table:

```python
for name in ("orders_day1", "orders_day2", "customers"):
    (
        spark.read.option("header", True)
        .option("inferSchema", True)
        .csv(f"Files/fabric-friday/{name}.csv")
        .write.format("delta")
        .mode("overwrite")
        .saveAsTable(name)
    )
```

## Offline setup — run once, in order

Run each `bash offline` block below in the same shell, from the repo root. The verifier
extracts these exact blocks, checks each declared exit, and asserts the JSON results.
`WORK` is a fresh scratch folder; keep it through the demonstration, then remove it.
The full rehearsal command is `bash docs/talks/fabric-friday-2026-12/verify_demo.sh`.
The expected failure commands are deliberate; do not run all blocks under bare `set -e`.

<!-- exit: 0 -->
```bash offline
export UV_OFFLINE=1
export TALK="$PWD/docs/talks/fabric-friday-2026-12"
export WORK="${WORK:-$(mktemp -d)}"
cp "$TALK"/data/*.csv "$WORK/"
cd "$WORK"
uv run --locked --no-sync --project "$TALK/../../.." python - <<'PYTHON'
from pathlib import Path
import pyarrow.csv as csv
import pyarrow.parquet as pq
for path in Path(".").glob("*.csv"):
    pq.write_table(csv.read_csv(path), path.with_suffix(".parquet"))
PYTHON
cat > orders.json <<'JSON'
{
  "columns": {
    "order_code": {"nullable": false, "regex": "ORD-[0-9]{3}"},
    "order_date": {
      "min": {"as_of_offset_days": 0},
      "max": {"as_of_offset_days": 0}
    },
    "status": {"allowed_values": ["shipped", "pending"]}
  }
}
JSON
```

The regex matches the whole non-null value; `nullable: false` separately forbids nulls.
Relative bounds select the entire explicit batch day (UTC by default), including the
max day's final instant. Never silently substitute the wall clock for a replay's batch date.

## 1. Profile Lakehouse tables from a notebook

**Fabric-only:** in the imported `shape_profile_spark` copy, set `tableName = "orders_day1"`,
`contractPath = ""`, `baselinePath = ""`, `outputDir = "friday"`, `failOnDrift = False`.
Run all. Open the inline report and save its `artifactPath` as the baseline. Repeat for
`customers` to show categorical distributions. This is the shipped driver-side notebook;
our small tables fit in memory. Do not present it as distributed contract evaluation.

**Offline fallback:** profile Parquet and open `day1.html`. Full capture is deliberate
for these synthetic fixtures; raw profiles and reports can expose source values.

<!-- exit: 0 -->
```bash offline
uv run --locked --no-sync --project "$TALK/../../.." shape profile orders_day1.parquet --name orders --capture full -o day1.shape --html day1.html
uv run --locked --no-sync --project "$TALK/../../.." shape profile customers.parquet --name customers --capture full -o customers.shape
uv run --locked --no-sync --project "$TALK/../../.." shape profile orders_day2.parquet --name orders --capture full -o day2.shape
```

## 2. Write a contract and gate the pipeline load

**Fabric-only, explicit talk customization:** upload `orders.json` as described above.
The shipped notebook's check cell has no batch-date parameter and uses default drift
thresholds. In the imported workspace copy, replace **only** its check/diff cell with:

```python
batchDate = "2026-12-04"  # use 2026-12-05 for orders_day2
check_result = shape.check(
    profile, _resolve(contractPath), as_of=batchDate, data={str(tableName): table}
)
violations = list(check_result.violations)
changes, drifted = [], False
if baselinePath:
    diff_result = shape.diff(
        load_baseline(baselinePath), profile, thresholds={"mean_shift_std": 0.25}
    )
    changes = list(diff_result.changes)
    drifted = bool(diff_result.drifted)
    if drifted and failOnDrift:
        violations.append(
            {"column": "*", "rule": "drift", "expected": "no drift", "observed": len(changes)}
        )
passed = check_result.passed and not violations
```

Keep the shipped artifact/display/result/exit cells after it. Set `contractPath =
"contracts/friday_orders.json"`; run day 1 with `batchDate = "2026-12-04"` (pass), then
`tableName = "orders_day2"`, `batchDate = "2026-12-05"` (fail). The notebook returns
JSON; a failed contract alone does not fail a Notebook activity.

Import `integrations/fabric/pipelines/shape_gate_spark.DataPipeline` following the
[runbook §7](../../../integrations/fabric/RUNBOOK.md). Bind ProfileTable to this workspace
notebook copy. For the two rehearsed runs use the corresponding table/date in its
parameters cell (stop overlapping runs). Keep the If Condition:
`@equals(json(activity('ProfileTable').output.result.exitValue).passed, true)`.
Put the actual Copy/load activity on the **true branch** after the gate; the false branch
uses Fail and exposes violations. The shipped pipeline is a gate scaffold, not an
already configured business load. Demonstrate day 2 never reaches that Copy activity.
Do not connect a dashboard refresh before this gate.

**Offline fallback:** use the same contract, exact source data for regex, and explicit
`--as-of`. Expected: day 1 exit 0; day 2 exit 1 with `order_code regex`, `order_date min`,
and `status allowed_values`. Missing batch date is a configuration error, exit 2.

<!-- exit: 0 -->
```bash offline
uv run --locked --no-sync --project "$TALK/../../.." shape check day1.shape orders.json --data orders_day1.parquet --as-of 2026-12-04 --json pass.json
```

<!-- exit: 1 -->
```bash offline
uv run --locked --no-sync --project "$TALK/../../.." shape check day2.shape orders.json --data orders_day2.parquet --as-of 2026-12-05 --json fail.json
```

<!-- exit: 2 -->
```bash offline
uv run --locked --no-sync --project "$TALK/../../.." shape check day1.shape orders.json --data orders_day1.parquet
```

## 3. Catch drift before the dashboard

**Fabric-only:** retain day 2/date 5, set `baselinePath` to the saved day-1 artifact,
`failOnDrift = True`, and rerun the customized notebook. Explain `mean_shift_std = 0.25`
is a demo sensitivity choice, not a universal recommendation. Compare known good batches
and tune false positives before enforcing it. Inspect `changes` in the activity output;
keep the dashboard load behind the If Condition. New categorical values and a shifted
amount distribution are separate findings from the date and regex contract violations.

**Offline fallback:** CLI supports tuned thresholds today. Expected exit 1 with
`mean_shift` on `order_total` and `new_categorical_values` on `status`.

<!-- exit: 1 -->
```bash offline
uv run --locked --no-sync --project "$TALK/../../.." shape diff day1.shape day2.shape --threshold mean_shift_std=0.25 --fail-on-drift --json drift.json
```

## 4. Generate a realistic synthetic copy into a dev Lakehouse

**Fabric-only, Environment path:** reuse the imported Spark notebook's successfully
profiled **day-1** `profile` in a separate notebook cell before its final exit, or a
separate PySpark notebook attached to `shape-env`. Attach **shape_dev as default** in
that separate notebook; copy only the day-1 `.shape` artifact into its Files area.
Run the following there. It reads the profile, not the source rows:

```python
import shape

p = shape.load("/lakehouse/default/Files/friday/day1.shape")
print(shape.plan(p))  # disclose preserved/approximate/not_modelled before generating
result = shape.generate(p, n=240, seed=1207)
for name, arrow in result.tables.items():
    (
        spark.createDataFrame(arrow.to_pandas())
        .write.format("delta")
        .mode("overwrite")
        .saveAsTable("friday_orders_dev")
    )
```

Inspect the destination table and explain that a statistical copy does not guarantee
identical rows, every dependency, or privacy. Only one table is generated here; the
customers fixture is for profiling, not a promised relational reconstruction.

**Shipped generation notebook alternative:** show
`integrations/fabric/notebooks/shape_generate.ipynb` and its documented `domain="retail"`,
`scale="small"`, `seed=42`, `tablePrefix="friday_"`, `writeMode="overwrite"` parameters.
It generates an authored domain, **not a copy from the profile**. It is a Python notebook
with inline installation and cannot attach `shape-env`; use the Environment Spark path
above for this presentation. Do not run its installation cells under this runbook.

**Offline fallback:** inspect the reconstruction plan, generate Parquet from day 1,
and verify it can be profiled. Expect 240 newly generated rows, not row equality.

<!-- exit: 0 -->
```bash offline
uv run --locked --no-sync --project "$TALK/../../.." shape plan day1.shape --json > plan.json
uv run --locked --no-sync --project "$TALK/../../.." shape generate --from day1.shape --rows 240 --seed 1207 --format parquet -o dev
uv run --locked --no-sync --project "$TALK/../../.." shape profile dev --capture full -o dev.shape
```

If any Fabric action fails, switch immediately to its matching offline block. Use locally
saved HTML and JSON when screen sharing is interrupted. Never label local output as a
Fabric result. No Fabric performance number is claimed by this kit.
