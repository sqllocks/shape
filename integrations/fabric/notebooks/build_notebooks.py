"""Generate the Fabric notebooks (.ipynb) from readable cell sources.

Run from the repo root:  python integrations/fabric/notebooks/build_notebooks.py
The generated files are committed; tests/demo/fabric/test_notebooks.py fails if they
drift from this script.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
WHEEL = "sqllocks_shape-0.9.0-py3-none-any.whl"

# --------------------------------------------------------------------------- setup

SETUP_CELLS = [
    (
        "markdown",
        """# Shape demo: load the demo Parquet files into Delta tables

Run once after uploading the demo data (produced by `demo/make_data.py`) to the
lakehouse `Files/demo/` folder. Every `*.parquet` file (one folder level down is also
accepted) becomes a Delta table named after the file, for example
`Files/demo/day1/orders.parquet` becomes the table `orders_day1`, and
`Files/demo/orders.parquet` becomes `orders`.

Attach the default lakehouse before running. Uses the preinstalled `deltalake` package.
""",
    ),
    (
        "code",
        """from pathlib import Path

import pyarrow.parquet as pq
from deltalake import write_deltalake

LAKEHOUSE = "/lakehouse/default"
DEMO_DIR = Path(LAKEHOUSE) / "Files" / "demo"
TABLES_DIR = Path(LAKEHOUSE) / "Tables"
""",
    ),
    (
        "code",
        """def table_name_for(parquet_file: Path) -> str:
    \"\"\"<sub>/<n>.parquet -> <n>_<sub>;  <n>.parquet -> <n>.\"\"\"
    rel = parquet_file.relative_to(DEMO_DIR)
    parts = [p.lower().replace("-", "_").replace(" ", "_") for p in rel.parts]
    stem = Path(parts[-1]).stem
    return "_".join([stem, *parts[:-1]]) if len(parts) > 1 else stem


files = sorted(DEMO_DIR.rglob("*.parquet"))
if not files:
    raise FileNotFoundError(
        f"No .parquet files under {DEMO_DIR}. Upload the demo data to Files/demo/ first."
    )
""",
    ),
    (
        "code",
        """created = []
for f in files:
    name = table_name_for(f)
    table = pq.read_table(f)
    write_deltalake(str(TABLES_DIR / name), table, mode="overwrite")
    created.append({"table": name, "rows": table.num_rows, "source": str(f.relative_to(DEMO_DIR))})

for row in created:
    print(f"{row['table']:<32} {row['rows']:>10,} rows   <- {row['source']}")
""",
    ),
]

# --------------------------------------------------------------- shared profile cells

PARAMETERS = """# Parameters cell (toggle "parameter cell" in Fabric). A pipeline overrides these.
# Paths are relative to the lakehouse Files/ folder, or absolute.
tableName = "orders"      # Delta table in the default lakehouse ("schema.table" also accepted)
contractPath = ""         # optional contract JSON, for example "contracts/orders.json"
baselinePath = ""         # optional earlier .shape artifact to diff against
outputDir = "shape"       # artifacts go to Files/<outputDir>/<table>/<timestamp>/
failOnDrift = False       # True: drift against the baseline also fails the gate
"""

HELPERS = """import json
import os
from datetime import UTC, datetime
from pathlib import Path

import shape

LAKEHOUSE = "/lakehouse/default"
FILES = f"{LAKEHOUSE}/Files"


def _as_bool(value) -> bool:
    \"\"\"Pipeline parameters may arrive as strings.\"\"\"
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


def _resolve(path: str) -> str:
    return path if os.path.isabs(path) else f"{FILES}/{path}"


failOnDrift = _as_bool(failOnDrift)
safe_name = str(tableName).replace(".", "_")
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
out_rel = f"{outputDir.strip('/')}/{safe_name}/{stamp}"
out_dir = Path(FILES) / out_rel
MAX_LISTED = 100  # keep the exit value small (well under 1 MB)
"""

CHECK_AND_DIFF = """violations, changes, drifted = [], [], False

if contractPath:
    check_result = shape.check(profile, _resolve(contractPath))
    violations = list(check_result.violations)

if baselinePath:
    diff_result = shape.diff(shape.load(_resolve(baselinePath)), profile)
    drifted = bool(diff_result.drifted)
    changes = list(diff_result.changes)
    if drifted and failOnDrift:
        violations.append(
            {
                "column": "*",
                "rule": "drift",
                "expected": "no drift against baseline",
                "observed": f"{len(changes)} change(s)",
            }
        )

passed = not violations
"""

WRITE_ARTIFACTS = """out_dir.mkdir(parents=True, exist_ok=True)
shape_file = out_dir / f"{safe_name}.shape"
shape.save(profile, str(shape_file))
(out_dir / f"{safe_name}.html").write_text(profile.to_html(), encoding="utf-8")
(out_dir / f"{safe_name}.summary.json").write_text(json.dumps(profile.summary()), encoding="utf-8")

artifact_path = f"{out_rel}/{safe_name}.shape"  # relative to Files/, usable as baselinePath
print("Artifacts written to", out_dir)
"""

DISPLAY = """from IPython.display import HTML, display

display(HTML(profile.to_html()))
"""

BUILD_RESULT = """result = {
    "table": tableName,
    "rows": total_rows,
    "passed": passed,
    "violations": violations[:MAX_LISTED],
    "drifted": drifted,
    "changes": changes[:MAX_LISTED],
    "artifactPath": artifact_path,
    "sampled": sampled,
    "truncated": len(violations) > MAX_LISTED or len(changes) > MAX_LISTED,
}
print(json.dumps(result, indent=2, default=str)[:4000])
"""

# NOTE: notebookutils.notebook.exit must be at top level: never inside try/except.
EXIT = """import notebookutils

notebookutils.notebook.exit(json.dumps(result, default=str))
"""

# ------------------------------------------------------------------ python notebook

PYTHON_CELLS = [
    (
        "markdown",
        """# Shape: profile, check and diff a lakehouse table (Python notebook)

Kernel: **Python 3.11 or 3.12** (not PySpark). Reads the Delta table with `deltalake`,
profiles it with Shape, checks it against a contract, optionally diffs it against a
baseline, writes the artifacts to `Files/<outputDir>/<table>/<timestamp>/`, shows the
HTML report, and returns a compact JSON result to the calling pipeline.

The exit value is `{table, rows, passed, violations, drifted, changes, artifactPath}`
(plus `sampled` and `truncated`). `notebookutils.notebook.exit` is the last statement
and is deliberately outside any `try`/`except`.
""",
    ),
    ("code", '%%configure\n{"vCores": 8}\n'),
    (
        "code",
        f"""# Wheel uploaded to this notebook's built-in resources folder ("Resources" > "builtin").
# Alternative once the package is on PyPI:  %pip install sqllocks-shape==0.9.0
%pip install builtin/{WHEEL}
""",
    ),
    ("code", PARAMETERS),
    ("code", HELPERS),
    (
        "code",
        """from deltalake import DeltaTable

delta_dir = f"{LAKEHOUSE}/Tables/{str(tableName).replace('.', '/')}"
table = DeltaTable(delta_dir).to_pyarrow_table()
total_rows = table.num_rows
sampled = False  # the Python notebook profiles every row
profile = shape.profile(table, name=str(tableName))
print(f"Profiled {total_rows:,} rows x {table.num_columns} columns from {delta_dir}")
""",
    ),
    ("code", CHECK_AND_DIFF),
    ("code", WRITE_ARTIFACTS),
    ("code", DISPLAY),
    ("code", BUILD_RESULT),
    ("code", EXIT),
]

# -------------------------------------------------------------------- spark notebook

SPARK_CELLS = [
    (
        "markdown",
        """# Shape: profile, check and diff a lakehouse table (PySpark notebook)

Attach the **Shape Environment** (Runtime 2.0, custom library `sqllocks_shape` wheel;
see `integrations/fabric/environment/README.md`) and the default lakehouse. Nothing is
installed in the notebook itself.

The table is read with `spark.read.table(...)` and profiled on the **driver**
(Shape's exact mode). Driver-side profiling is bounded by `DRIVER_ROW_LIMIT` rows (see
the next cells): above it, the table is sampled and the result carries `sampled: true`.
The distributed bounded mode arrives in PF-02.

Same parameters, artifacts and exit value as `shape_profile.ipynb`.
""",
    ),
    ("code", PARAMETERS),
    (
        "code",
        HELPERS.replace("import json\n", "import inspect\nimport json\n").replace(
            "import shape\n", "import pyarrow as pa\n\nimport shape\n"
        )
        + """
# Driver-side profiling limit. The Arrow copy of the table lives in driver memory
# (roughly 100-200 bytes per cell for mixed types), so the default is 5,000,000 rows,
# which is comfortable on a Medium (8 vCore / 64 GB) node. Lower it for wide tables.
DRIVER_ROW_LIMIT = 5_000_000
SAMPLE_SEED = 42
""",
    ),
    (
        "code",
        """df = spark.read.table(str(tableName))  # noqa: F821 (`spark` is predefined in Fabric)
total_rows = df.count()
sampled = total_rows > DRIVER_ROW_LIMIT
if sampled:
    df = df.sample(withReplacement=False, fraction=DRIVER_ROW_LIMIT / total_rows, seed=SAMPLE_SEED)

# Spark 4 (Runtime 2.0) has DataFrame.toArrow(); Spark 3.5 (Runtime 1.3) goes through pandas.
if int(spark.version.split(".")[0]) >= 4:  # noqa: F821
    table = df.toArrow()
else:
    table = pa.Table.from_pandas(df.toPandas(), preserve_index=False)

# The section 12.2 API is profile(source, *, name=None). Ask for exact mode only when the
# installed Shape offers it.
kwargs = {"name": str(tableName)}
if "exact" in inspect.signature(shape.profile).parameters:
    kwargs["exact"] = True
profile = shape.profile(table, **kwargs)
print(f"Profiled {table.num_rows:,} of {total_rows:,} rows (sampled={sampled})")
print(f"Spark {spark.version}")  # noqa: F821
""",
    ),
    ("code", CHECK_AND_DIFF),
    ("code", WRITE_ARTIFACTS),
    ("code", DISPLAY),
    ("code", BUILD_RESULT),
    ("code", EXIT),
]

# --------------------------------------------------------------------------- output


def _cell(kind: str, source: str, index: int) -> dict:
    lines = source.splitlines(keepends=True)
    cell: dict = {"cell_type": kind, "id": f"cell-{index:02d}", "metadata": {}, "source": lines}
    if kind == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def _notebook(cells: list[tuple[str, str]], *, spark: bool, tag_parameters: bool = True) -> dict:
    out = []
    for i, (kind, source) in enumerate(cells):
        c = _cell(kind, source, i)
        if kind == "code" and source.startswith("# Parameters cell") and tag_parameters:
            c["metadata"] = {"tags": ["parameters"]}
        out.append(c)
    if spark:
        kernel = {"name": "synapse_pyspark", "display_name": "Synapse PySpark"}
        microsoft = {"language": "python", "language_group": "synapse_pyspark"}
        kernel_info = {"name": "synapse_pyspark"}
    else:
        kernel = {"name": "jupyter", "display_name": "Jupyter"}
        microsoft = {"language": "python", "language_group": "jupyter_python"}
        kernel_info = {"name": "jupyter", "jupyter_kernel_name": "python3.11"}
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": out,
        "metadata": {
            "kernel_info": kernel_info,
            "kernelspec": kernel,
            "language_info": {"name": "python"},
            "microsoft": microsoft,
            "nteract": {"version": "nteract-front-end@1.0.0"},
            "dependencies": {"lakehouse": {"default_lakehouse_name": "shape_demo"}},
        },
    }


def build() -> dict[str, dict]:
    return {
        "shape_setup.ipynb": _notebook(SETUP_CELLS, spark=False, tag_parameters=False),
        "shape_profile.ipynb": _notebook(PYTHON_CELLS, spark=False),
        "shape_profile_spark.ipynb": _notebook(SPARK_CELLS, spark=True),
    }


def main() -> None:
    for name, nb in build().items():
        (HERE / name).write_text(json.dumps(nb, indent=1) + "\n", encoding="utf-8")
        print("wrote", HERE / name)


if __name__ == "__main__":
    main()
