"""Generate the Fabric notebooks (.ipynb) from readable cell sources.

Run from the repo root:
    python integrations/fabric/notebooks/build_notebooks.py && ruff format integrations
The generated files are committed; tests/demo/fabric/test_notebooks.py fails if they
drift from this script.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSION = "0.9.0"
WHEEL = f"sqllocks_shape-{VERSION}-py3-none-any.whl"

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
from shape.kernel.dispatch import get_kernel

KERNEL = get_kernel().NAME  # "rust" with a platform wheel, "python" with the pure-Python wheel
print(f"Shape {shape.__version__}, kernel: {KERNEL}")

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
    "kernel": KERNEL,
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
        f"""# Upload the wheel(s) to this notebook's built-in resources folder (Resources > builtin)
# Both wheels have the same version: the platform wheel (Rust kernel, manylinux x86_64) and
# {WHEEL} (pure Python).
# pip takes the platform wheel when builtin/ holds one that fits this kernel and falls back to
# the pure-Python wheel otherwise, so one cell works either way. The exit value reports which.
# Once the package is on PyPI the same line works without the upload.
%pip install --find-links builtin "sqllocks-shape=={VERSION}"
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
For tables too large for the driver use `shape_profile_distributed`, which profiles every
partition on the executors instead.

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


# ------------------------------------------------------------- distributed spark notebook

DISTRIBUTED_PARAMETERS = (
    PARAMETERS
    + """mode = "distributed"  # "distributed": bounded profile per partition, merged on the driver
                         # "exact": driver-only exact profile (table must fit in driver memory)
partitions = 0            # distributed only: repartition first (0 keeps the table's partitioning)
"""
)

DISTRIBUTED_HELPERS = (
    HELPERS.replace("import json\n", "import inspect\nimport json\nimport math\n", 1)
    + """mode = str(mode).strip().lower()
if mode not in ("distributed", "exact"):
    raise ValueError(f"mode must be 'distributed' or 'exact', got {mode!r}")
if mode == "distributed" and (contractPath or baselinePath):
    raise ValueError(
        "contractPath and baselinePath need the exact profile: the distributed mode builds a "
        "bounded profile, which the contract check and the diff do not read. "
        "Set mode = 'exact', or clear both parameters."
    )


def _json_safe(value):
    \"\"\"NaN and infinities become null so the artifact is strict JSON.\"\"\"
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


# Exact mode only: the Arrow copy of the table lives in driver memory (see shape_profile_spark).
DRIVER_ROW_LIMIT = 5_000_000
SAMPLE_SEED = 42
"""
)

DISTRIBUTED_PROFILE = """# `spark` is predefined in Fabric
df = spark.read.table(str(tableName))  # noqa: F821
print(f"Spark {spark.version}, {df.rdd.getNumPartitions()} partition(s)")  # noqa: F821

if mode == "exact":
    total_rows = df.count()
    sampled = total_rows > DRIVER_ROW_LIMIT
    if sampled:
        df = df.sample(
            withReplacement=False, fraction=DRIVER_ROW_LIMIT / total_rows, seed=SAMPLE_SEED
        )
    if int(spark.version.split(".")[0]) >= 4:  # noqa: F821
        table = df.toArrow()
    else:
        table = pa.Table.from_pandas(df.toPandas(), preserve_index=False)
    kwargs = {"name": str(tableName)}
    if "exact" in inspect.signature(shape.profile).parameters:
        kwargs["exact"] = True
    profile = shape.profile(table, **kwargs)
    print(f"Exact profile of {table.num_rows:,} of {total_rows:,} rows (sampled={sampled})")
else:
    from shape.integrations.fabric.spark import profile_distributed

    doc = profile_distributed(
        df, name=str(tableName), partitions=int(partitions) or None
    )
    entry = doc["tables"][str(tableName)]
    total_rows = entry["rows"]
    sampled = False  # every row of every partition is read
    print(f"Bounded profile of {total_rows:,} rows x {len(entry['columns'])} columns")
"""

DISTRIBUTED_CHECK = """violations, changes, drifted = [], [], False

if mode == "exact":
    if contractPath:
        violations = list(shape.check(profile, _resolve(contractPath)).violations)
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

DISTRIBUTED_ARTIFACTS = """SUMMARY_KEYS = (
    "arrow_type", "kind", "count", "null_count", "distinct", "min", "max", "mean",
)
out_dir.mkdir(parents=True, exist_ok=True)
if mode == "exact":
    shape.save(profile, str(out_dir / f"{safe_name}.shape"))
    (out_dir / f"{safe_name}.html").write_text(profile.to_html(), encoding="utf-8")
    (out_dir / f"{safe_name}.summary.json").write_text(
        json.dumps(profile.summary()), encoding="utf-8"
    )
    artifact_path = f"{out_rel}/{safe_name}.shape"
else:
    # the full bounded profile document, plus a one-line-per-column summary
    (out_dir / f"{safe_name}.profile.json").write_text(
        json.dumps(_json_safe(doc)), encoding="utf-8"
    )
    summary = {
        "name": entry["name"],
        "rows": entry["rows"],
        "mode": "bounded",
        "columns": {
            c["name"]: {
                k: c.get(k)
                for k in SUMMARY_KEYS
            }
            for c in entry["columns"]
        },
    }
    (out_dir / f"{safe_name}.summary.json").write_text(
        json.dumps(_json_safe(summary)), encoding="utf-8"
    )
    artifact_path = f"{out_rel}/{safe_name}.profile.json"
print("Artifacts written to", out_dir)
"""

DISTRIBUTED_DISPLAY = """if mode == "exact":
    from IPython.display import HTML, display

    display(HTML(profile.to_html()))
else:
    print(json.dumps(_json_safe(summary), indent=1)[:4000])
"""

DISTRIBUTED_RESULT = """result = {
    "table": tableName,
    "rows": total_rows,
    "passed": passed,
    "violations": violations[:MAX_LISTED],
    "drifted": drifted,
    "changes": changes[:MAX_LISTED],
    "artifactPath": artifact_path,
    "sampled": sampled,
    "truncated": len(violations) > MAX_LISTED or len(changes) > MAX_LISTED,
    "kernel": KERNEL,
    "mode": "bounded" if mode == "distributed" else "exact",
    "checked": mode == "exact",
}
print(json.dumps(result, indent=2, default=str)[:4000])
"""

DISTRIBUTED_CELLS = [
    (
        "markdown",
        """# Shape: profile a large lakehouse table across the Spark cluster (PySpark notebook)

Attach the **Shape Environment** (`integrations/fabric/environment/README.md`) and the default
lakehouse; Shape must be installed on the executors, which the Environment does.

`mode = "distributed"` (the default): every partition is profiled on the executors in Shape's
bounded mode (sketches, memory independent of the partition's size) through `mapInArrow`, and the
driver merges the partial profiles. No rows reach the driver. The result is a bounded profile:
counts, min, max, mean, variance and null counts are exact; distinct counts, quantiles and top
values carry the error bounds in the profile's `error_models`. The merge is in partition order.

`mode = "exact"`: the driver-only exact profile of `shape_profile_spark`, for tables that fit in
driver memory. Only this mode can check a contract or diff against a baseline.

The exit value has the keys of `shape_profile.ipynb`, plus `mode` and `checked`.
**`checked` is false for the distributed mode**: nothing was checked against a contract, so
do not use it as a gate; use `mode = "exact"` for gates.
""",
    ),
    ("code", DISTRIBUTED_PARAMETERS),
    (
        "code",
        DISTRIBUTED_HELPERS.replace("import shape\n", "import pyarrow as pa\n\nimport shape\n", 1),
    ),
    ("code", DISTRIBUTED_PROFILE),
    ("code", DISTRIBUTED_CHECK),
    ("code", DISTRIBUTED_ARTIFACTS),
    ("code", DISTRIBUTED_DISPLAY),
    ("code", DISTRIBUTED_RESULT),
    ("code", EXIT),
]

# ------------------------------------------------------------ generation notebooks (PF-06)

INSTALL_CELL = (
    "code",
    f"""# Upload the wheel(s) to this notebook's built-in resources folder (Resources > builtin):
# the platform wheel (Rust kernel), {WHEEL} (pure Python) and, for a domain,
# sqllocks_shape_domains-{VERSION}-py3-none-any.whl. pip takes the platform wheel when one
# fits and falls back to the pure wheel; the exit value reports the kernel.
# Once the packages are on PyPI the same line works without the upload.
%pip install --find-links builtin "sqllocks-shape=={VERSION}" "sqllocks-shape-domains=={VERSION}"
""",
)

GENERATE_PARAMETERS = """# Parameters cell (toggle "parameter cell" in Fabric). A pipeline overrides these.
domain = "retail"         # an installed domain (`shape list`)
scale = "small"           # a scale preset of the domain (`shape presets`)
seed = 42                 # the same seed always generates the same rows
mode = ""                 # "3nf" or "star"; empty keeps the domain's default schema
tablePrefix = ""          # Delta tables are named <tablePrefix><table>, e.g. "retail_"
writeMode = "overwrite"   # "overwrite" or "append" for the Delta tables
outputDir = "shape"       # the contract and manifest go to Files/<outputDir>/<domain>/
"""

GENERATE_HELPERS = """import json
from pathlib import Path

import shape
from shape.integrations.fabric import generation
from shape.kernel.dispatch import get_kernel

KERNEL = get_kernel().NAME  # "rust" with a platform wheel, "python" with the pure-Python wheel
print(f"Shape {shape.__version__}, kernel: {KERNEL}")

LAKEHOUSE = "/lakehouse/default"
FILES = f"{LAKEHOUSE}/Files"
TABLES = f"{LAKEHOUSE}/Tables"
MAX_LISTED = 100

# Names reach paths and table names: only identifiers are accepted.
for _label, _value in (("domain", domain), ("scale", scale), ("outputDir", outputDir)):
    generation.check_name(str(_value).strip("/"), _label)
if tablePrefix:
    generation.check_name(str(tablePrefix), "tablePrefix")
"""

GENERATE_RUN = """result = generation.generate_domain(
    str(domain),
    scale=str(scale),
    seed=int(seed),
    mode=str(mode) or None,
)
row_counts = {name: table.num_rows for name, table in result.tables.items()}
print(f"Generated {sum(row_counts.values()):,} rows in {len(row_counts)} tables")
for name in result.generation_order:
    print(f"  {name:<24} {row_counts[name]:>10,} rows")
"""

GENERATE_WRITE = """tables = generation.write_delta_tables(
    result, TABLES, prefix=str(tablePrefix), mode=str(writeMode)
)

# The contract the domain's own schema implies for these tables: the pipeline checks the
# profile of the Delta tables against it (shape_profile_domain.ipynb).
contract = generation.domain_contract(result.schema, row_counts)
contract_rel = f"{str(outputDir).strip('/')}/{domain}/contract.json"
generation.write_contract(contract, Path(FILES) / contract_rel)
manifest = {
    "domain": domain,
    "scale": scale,
    "seed": int(seed),
    "mode": str(mode) or "default",
    "tablePrefix": str(tablePrefix),
    "tables": tables,
}
(Path(FILES) / contract_rel).with_name("generation.json").write_text(
    json.dumps(manifest, indent=1), encoding="utf-8"
)
print("Delta tables written under", TABLES)
print("Contract written to", Path(FILES) / contract_rel)
"""

GENERATE_RESULT = """result_value = {
    "domain": domain,
    "scale": scale,
    "seed": int(seed),
    "mode": str(mode) or "default",
    "tablePrefix": str(tablePrefix),
    "tables": tables[:MAX_LISTED],
    "totalRows": sum(t["rows"] for t in tables),
    "contractPath": contract_rel,
    "kernel": KERNEL,
}
print(json.dumps(result_value, indent=2)[:4000])
"""

GENERATE_EXIT = """import notebookutils

notebookutils.notebook.exit(json.dumps(result_value))
"""

GENERATE_CELLS = [
    (
        "markdown",
        """# Shape: generate a domain into lakehouse Delta tables (Python notebook)

Kernel: **Python 3.11 or 3.12** (not PySpark). Generates every table of an installed domain at a
scale preset with a seed, and writes each as a Delta table `Tables/<tablePrefix><table>` in the
default lakehouse. The same seed always generates the same rows.

Next to the tables it writes `Files/<outputDir>/<domain>/contract.json`, the contract that the
domain's own schema implies for these tables (row counts, columns, types, nullability, primary
keys, enumerated values), and `generation.json`. `shape_profile_domain.ipynb` profiles the
tables and checks them against that contract; `shape_generate_gate` runs both from a pipeline.

The exit value is `{domain, scale, seed, mode, tablePrefix, tables, totalRows, contractPath,
kernel}`. `notebookutils.notebook.exit` is the last statement, outside any `try`/`except`.
""",
    ),
    ("code", '%%configure\n{"vCores": 8}\n'),
    INSTALL_CELL,
    ("code", GENERATE_PARAMETERS),
    ("code", GENERATE_HELPERS),
    ("code", GENERATE_RUN),
    ("code", GENERATE_WRITE),
    ("code", GENERATE_RESULT),
    ("code", GENERATE_EXIT),
]

PROFILE_DOMAIN_PARAMETERS = """# Parameters cell (toggle "parameter cell" in Fabric). A pipeline overrides these.
# Paths are relative to the lakehouse Files/ folder, or absolute.
domain = "retail"         # labels the artifacts: Files/<outputDir>/<domain>/<timestamp>/
contractPath = "shape/retail/contract.json"  # the multi-table contract shape_generate wrote
tablePrefix = ""          # the Delta tables are <tablePrefix><table>, as shape_generate named them
baselinePath = ""         # optional earlier .shape artifact to diff against
outputDir = "shape"
failOnDrift = False       # True: drift against the baseline also fails the gate
"""

PROFILE_DOMAIN_HELPERS = """import json
import os
from datetime import UTC, datetime
from pathlib import Path

import shape
from shape.integrations.fabric import generation
from shape.kernel.dispatch import get_kernel

KERNEL = get_kernel().NAME
print(f"Shape {shape.__version__}, kernel: {KERNEL}")

LAKEHOUSE = "/lakehouse/default"
FILES = f"{LAKEHOUSE}/Files"
MAX_LISTED = 100


def _as_bool(value) -> bool:
    \"\"\"Pipeline parameters may arrive as strings.\"\"\"
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


def _resolve(path: str) -> str:
    return path if os.path.isabs(path) else f"{FILES}/{path}"


failOnDrift = _as_bool(failOnDrift)
generation.check_name(str(domain), "domain")
if tablePrefix:
    generation.check_name(str(tablePrefix), "tablePrefix")
safe_name = str(domain)
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
out_rel = f"{outputDir.strip('/')}/{safe_name}/{stamp}"
out_dir = Path(FILES) / out_rel
"""

PROFILE_DOMAIN_PROFILE = """from deltalake import DeltaTable

# The contract names the tables: every one of them is read and profiled.
with open(_resolve(contractPath), encoding="utf-8") as fh:
    contract_tables = list(json.load(fh)["tables"])

sources = {}
for name in contract_tables:
    generation.check_name(name, "table")
    delta_dir = f"{LAKEHOUSE}/Tables/{tablePrefix}{name}"
    sources[name] = DeltaTable(delta_dir).to_pyarrow_table()
total_rows = sum(t.num_rows for t in sources.values())
profile = shape.profile(sources, name=str(domain))
print(f"Profiled {total_rows:,} rows in {len(sources)} tables")
"""

PROFILE_DOMAIN_RESULT = """result = {
    "domain": domain,
    "tables": {name: t.num_rows for name, t in sources.items()},
    "rows": total_rows,
    "passed": passed,
    "violations": violations[:MAX_LISTED],
    "drifted": drifted,
    "changes": changes[:MAX_LISTED],
    "artifactPath": artifact_path,
    "truncated": len(violations) > MAX_LISTED or len(changes) > MAX_LISTED,
    "kernel": KERNEL,
}
print(json.dumps(result, indent=2, default=str)[:4000])
"""

PROFILE_DOMAIN_CELLS = [
    (
        "markdown",
        """# Shape: profile a generated domain and check it against its contract (Python notebook)

Run after `shape_generate.ipynb`. Reads every Delta table named in the domain's contract,
profiles them together (so keys and foreign keys between tables are detected), checks the profile
against the contract, optionally diffs it against a baseline, writes the artifacts to
`Files/<outputDir>/<domain>/<timestamp>/`, shows the HTML report and returns a compact JSON
result to the calling pipeline.

The exit value is `{domain, tables, rows, passed, violations, drifted, changes, artifactPath,
truncated, kernel}`. `passed` is false when any table breaks the contract: a row count that is not
the generated one, a missing or extra column, a null in a column the schema says is never null, a
value outside an enumerated set, a duplicated primary key.
""",
    ),
    ("code", '%%configure\n{"vCores": 8}\n'),
    INSTALL_CELL,
    ("code", PROFILE_DOMAIN_PARAMETERS),
    ("code", PROFILE_DOMAIN_HELPERS),
    ("code", PROFILE_DOMAIN_PROFILE),
    ("code", CHECK_AND_DIFF),
    ("code", WRITE_ARTIFACTS),
    ("code", DISPLAY),
    ("code", PROFILE_DOMAIN_RESULT),
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
        "shape_profile_distributed.ipynb": _notebook(DISTRIBUTED_CELLS, spark=True),
        "shape_generate.ipynb": _notebook(GENERATE_CELLS, spark=False),
        "shape_profile_domain.ipynb": _notebook(PROFILE_DOMAIN_CELLS, spark=False),
    }


def main() -> None:
    for name, nb in build().items():
        (HERE / name).write_text(json.dumps(nb, indent=1) + "\n", encoding="utf-8")
        print("wrote", HERE / name)


if __name__ == "__main__":
    main()
