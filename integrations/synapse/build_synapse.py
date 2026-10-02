"""Generate the Azure Synapse artifacts: the PySpark notebook and the gate pipeline.

Run from the repo root:
    python integrations/synapse/build_synapse.py && ruff format integrations

The generated files are committed; tests/demo/fabric/test_synapse.py fails if they drift from this
script. The notebook is the PF-02 PySpark notebook (``shape_profile_distributed``) adapted to
Synapse: storage is reached through ``mssparkutils.fs`` and the Spark session's linked-service
token provider, and the result goes back through ``mssparkutils.notebook.exit``.

Two things could not be checked against a live workspace and are marked "verify in the workspace
on first run" in RUNBOOK.md: the notebook-activity exit-value expression and the notebook
activity's parameter encoding.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOTEBOOK_NAME = "shape_profile_synapse"
PIPELINE_NAME = "shape_gate_synapse"

# --------------------------------------------------------------------------- notebook cells

PARAMETERS = """# Parameters cell (Synapse: toggle "parameter cell"). A pipeline overrides these.
# Paths are abfss://<container>@<account>.dfs.core.windows.net/<path> URLs.
sourcePath = "abfss://data@<account>.dfs.core.windows.net/tables/orders"  # folder or file
sourceFormat = "delta"  # delta | parquet | csv
tableName = ""  # optional: a lake database / Spark catalog table instead of sourcePath
contractPath = ""  # optional contract JSON (abfss URL)
baselinePath = ""  # optional earlier .shape artifact (abfss URL) to diff against
outputPath = "abfss://shape@<account>.dfs.core.windows.net/shape"  # artifacts go below this
failOnDrift = False  # True: drift against the baseline also fails the gate
mode = "exact"  # "exact": driver-side profile, checks and diffs; "distributed": bounded, unchecked
partitions = 0  # distributed only: repartition first (0 keeps the data's partitioning)
linkedServiceName = ""  # optional: Synapse linked service that grants access to the storage
"""

HELPERS = """import inspect
import json
import math
import tempfile
from pathlib import Path

import pyarrow as pa

import shape
from shape.integrations.run_folder import unique_run_name
from shape.kernel.dispatch import get_kernel

try:  # Synapse Spark pools predefine `mssparkutils`; newer runtimes also offer the import
    from notebookutils import mssparkutils
except ImportError:
    pass

KERNEL = get_kernel().NAME  # "rust" with a platform wheel, "python" with the pure-Python wheel
print(f"Shape {shape.__version__}, kernel: {KERNEL}")


def _as_bool(value) -> bool:
    \"\"\"Pipeline parameters may arrive as strings.\"\"\"
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


failOnDrift = _as_bool(failOnDrift)
mode = str(mode).strip().lower()
if mode not in ("distributed", "exact"):
    raise ValueError(f"mode must be 'distributed' or 'exact', got {mode!r}")
if mode == "distributed" and (contractPath or baselinePath):
    raise ValueError(
        "contractPath and baselinePath need the exact profile: the distributed mode builds a "
        "bounded profile, which the contract check and the diff do not read. "
        "Set mode = 'exact', or clear both parameters."
    )
if not tableName and not sourcePath:
    raise ValueError("set sourcePath or tableName")

# Spark reads the data with the notebook user's identity, or with a linked service's identity.
if linkedServiceName:
    spark.conf.set("spark.storage.synapse.linkedServiceName", linkedServiceName)  # noqa: F821
    spark.conf.set(  # noqa: F821
        "fs.azure.account.oauth.provider.type",
        "com.microsoft.azure.synapse.tokenlibrary.LinkedServiceBasedTokenProvider",
    )

# Storage outside Spark (contract, baseline, artifacts) goes through mssparkutils.fs, which
# uses the same Synapse identity. The driver's local disk is staging.
LOCAL = Path(tempfile.mkdtemp(prefix="shape_"))


def _read_text(url: str) -> str:
    return mssparkutils.fs.head(url, 64 * 1024 * 1024)  # noqa: F821


def _fetch(url: str, name: str) -> str:
    local = LOCAL / name
    mssparkutils.fs.cp(url, f"file:{local}", False)  # noqa: F821
    return str(local)


def _publish_file(local: Path, url: str) -> None:
    mssparkutils.fs.cp(f"file:{local}", url, True)  # noqa: F821


def _publish_text(text: str, url: str) -> None:
    mssparkutils.fs.put(url, text, True)  # noqa: F821


def _json_safe(value):
    \"\"\"NaN and infinities become null so the artifact is strict JSON.\"\"\"
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


label = str(tableName) or str(sourcePath).rstrip("/").rsplit("/", 1)[-1]
safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label) or "table"
out_parent = f"{str(outputPath).rstrip('/')}/{safe_name}"  # one new folder per run below it
MAX_LISTED = 100  # keep the exit value small (well under 1 MB)

# Exact mode only: the Arrow copy of the table lives in driver memory.
DRIVER_ROW_LIMIT = 5_000_000
SAMPLE_SEED = 42
"""

PROFILE = """if tableName:
    df = spark.read.table(str(tableName))  # noqa: F821
else:
    df = spark.read.format(str(sourceFormat)).load(str(sourcePath))  # noqa: F821
print(f"Spark {spark.version}, {df.rdd.getNumPartitions()} partition(s)")  # noqa: F821

if mode == "exact":
    total_rows = df.count()
    sampled = total_rows > DRIVER_ROW_LIMIT
    if sampled:
        df = df.sample(
            withReplacement=False, fraction=DRIVER_ROW_LIMIT / total_rows, seed=SAMPLE_SEED
        )
    # Spark 4 has DataFrame.toArrow(); Spark 3.x goes through pandas.
    if int(spark.version.split(".")[0]) >= 4:  # noqa: F821
        table = df.toArrow()
    else:
        table = pa.Table.from_pandas(df.toPandas(), preserve_index=False)
    kwargs = {"name": label}
    if "exact" in inspect.signature(shape.profile).parameters:
        kwargs["exact"] = True
    profile = shape.profile(table, **kwargs)
    print(f"Exact profile of {table.num_rows:,} of {total_rows:,} rows (sampled={sampled})")
else:
    from shape.integrations.fabric.spark import profile_distributed

    doc = profile_distributed(df, name=label, partitions=int(partitions) or None)
    entry = doc["tables"][label]
    total_rows = entry["rows"]
    sampled = False  # every row of every partition is read
    print(f"Bounded profile of {total_rows:,} rows x {len(entry['columns'])} columns")
"""

CHECK_AND_DIFF = """violations, changes, drifted = [], [], False

if mode == "exact":
    if contractPath:
        contract = json.loads(_read_text(contractPath))
        violations = list(shape.check(profile, contract).violations)
    if baselinePath:
        diff_result = shape.diff(shape.load(_fetch(baselinePath, "baseline.shape")), profile)
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

ARTIFACTS = """out_root = f"{out_parent}/{unique_run_name(out_parent, mssparkutils.fs.exists)}"
SUMMARY_KEYS = (
    "arrow_type",
    "kind",
    "count",
    "null_count",
    "distinct",
    "min",
    "max",
    "mean",
)
if mode == "exact":
    local_shape = LOCAL / f"{safe_name}.shape"
    shape.save(profile, str(local_shape))
    artifact_path = f"{out_root}/{safe_name}.shape"
    _publish_file(local_shape, artifact_path)
    _publish_text(profile.to_html(), f"{out_root}/{safe_name}.html")
    _publish_text(json.dumps(profile.summary()), f"{out_root}/{safe_name}.summary.json")
else:
    summary = {
        "name": entry["name"],
        "rows": entry["rows"],
        "mode": "bounded",
        "columns": {c["name"]: {k: c.get(k) for k in SUMMARY_KEYS} for c in entry["columns"]},
    }
    artifact_path = f"{out_root}/{safe_name}.profile.json"
    _publish_text(json.dumps(_json_safe(doc)), artifact_path)
    _publish_text(json.dumps(_json_safe(summary)), f"{out_root}/{safe_name}.summary.json")
print("Artifacts written to", out_root)
"""

DISPLAY = """if mode == "exact":
    displayHTML(profile.to_html())  # noqa: F821 (predefined in Synapse notebooks)
else:
    print(json.dumps(_json_safe(summary), indent=1)[:4000])
"""

BUILD_RESULT = """result = {
    "table": label,
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

# NOTE: mssparkutils.notebook.exit must be at top level: never inside try/except.
EXIT = """mssparkutils.notebook.exit(json.dumps(result, default=str))  # noqa: F821
"""

CELLS = [
    (
        "markdown",
        """# Shape: profile, check and diff data in ADLS Gen2 (Synapse PySpark notebook)

Runs on a Synapse **Apache Spark pool** with the Shape wheel installed (pool packages or workspace
package; see `integrations/synapse/RUNBOOK.md`). Shape needs **Python 3.11 or newer**, so the pool
must use a runtime with it.

Reads a Delta, Parquet or CSV folder (`sourcePath`, `sourceFormat`) or a Spark catalog table
(`tableName`), profiles it, optionally checks a contract and diffs a baseline (both `abfss://`
URLs), writes `.shape`, `.html` and `.summary.json` below `outputPath/<name>/<timestamp>/`, and
returns a compact JSON result to the calling pipeline.

`mode = "exact"` (default) profiles on the driver and is the only mode that checks a contract or
a baseline. `mode = "distributed"` profiles every partition on the executors in bounded mode and
merges the partial profiles on the driver, for data too large for the driver; its exit value says
`"checked": false` and it refuses a contract, so it cannot pass a gate it did not evaluate.

Set `linkedServiceName` to read with a linked service's identity; otherwise the notebook user's
identity (or the pool's) is used. The exit value is
`{table, rows, passed, violations, drifted, changes, artifactPath, sampled, truncated, kernel,
mode, checked}`. `mssparkutils.notebook.exit` is the last statement and is deliberately outside any
`try`/`except`.
""",
    ),
    ("code", PARAMETERS),
    ("code", HELPERS),
    ("code", PROFILE),
    ("code", CHECK_AND_DIFF),
    ("code", ARTIFACTS),
    ("code", DISPLAY),
    ("code", BUILD_RESULT),
    ("code", EXIT),
]

# ------------------------------------------------------------- generation notebooks (PF-06)

GEN_NOTEBOOK_NAME = "shape_generate_synapse"
GEN_PROFILE_NOTEBOOK_NAME = "shape_profile_domain_synapse"
GEN_PIPELINE_NAME = "shape_generate_gate_synapse"

GEN_PARAMETERS = """# Parameters cell (Synapse: toggle "parameter cell"). A pipeline overrides these.
# Paths are abfss://<container>@<account>.dfs.core.windows.net/<path> URLs.
domain = "retail"  # an installed domain (`shape list`)
scale = "small"  # a scale preset of the domain (`shape presets`)
seed = 42  # the same seed always generates the same rows
mode = ""  # "3nf" or "star"; empty keeps the domain's default schema
outputPath = "abfss://shape@<account>.dfs.core.windows.net/generated"  # below: tables/, contract.json
tablePrefix = ""  # tables are written as <outputPath>/tables/<tablePrefix><table>
tableFormat = "delta"  # delta | parquet
writeMode = "overwrite"  # overwrite | append
maxRows = 20_000_000  # refuse a scale with more rows: the data is generated on the driver
linkedServiceName = ""  # optional: Synapse linked service that grants access to the storage
"""

GEN_HELPERS = """import json
import tempfile
from pathlib import Path

import shape
from shape.integrations.fabric import generation
from shape.kernel.dispatch import get_kernel

try:  # Synapse Spark pools predefine `mssparkutils`; newer runtimes also offer the import
    from notebookutils import mssparkutils
except ImportError:
    pass

KERNEL = get_kernel().NAME  # "rust" with a platform wheel, "python" with the pure-Python wheel
print(f"Shape {shape.__version__}, kernel: {KERNEL}")

MAX_LISTED = 100  # keep the exit value small (well under 1 MB)
tableFormat = str(tableFormat).strip().lower()
if tableFormat not in ("delta", "parquet"):
    raise ValueError(f"tableFormat must be 'delta' or 'parquet', got {tableFormat!r}")
if str(writeMode) not in ("overwrite", "append"):
    raise ValueError(f"writeMode must be 'overwrite' or 'append', got {writeMode!r}")
# Names reach storage paths and table names: only identifiers are accepted.
generation.check_name(str(domain), "domain")
generation.check_name(str(scale), "scale")
if tablePrefix:
    generation.check_name(str(tablePrefix), "tablePrefix")
if not str(outputPath).startswith(("abfss://", "abfs://")):
    raise ValueError("outputPath must be an abfss:// URL")

# Spark writes with the notebook user's identity, or with a linked service's identity.
if linkedServiceName:
    spark.conf.set("spark.storage.synapse.linkedServiceName", linkedServiceName)  # noqa: F821
    spark.conf.set(  # noqa: F821
        "fs.azure.account.oauth.provider.type",
        "com.microsoft.azure.synapse.tokenlibrary.LinkedServiceBasedTokenProvider",
    )

out_root = str(outputPath).rstrip("/")
tables_root = f"{out_root}/tables"
contract_url = f"{out_root}/{domain}/contract.json"
"""

GEN_RUN = """planned = generation.plan_row_counts(str(domain), str(scale), str(mode) or None)
if sum(planned.values()) > int(maxRows):
    raise ValueError(
        f"{domain!r} at scale {scale!r} is {sum(planned.values()):,} rows, above maxRows="
        f"{int(maxRows):,}: the data is generated on the driver. Use a smaller scale."
    )
result = generation.generate_domain(
    str(domain), scale=str(scale), seed=int(seed), mode=str(mode) or None
)
row_counts = {name: table.num_rows for name, table in result.tables.items()}
print(f"Generated {sum(row_counts.values()):,} rows in {len(row_counts)} tables")
"""

GEN_WRITE = """tables = []
for name in result.generation_order:
    arrow_table = generation.delta_ready(result.tables[name])
    target = f"{tables_root}/{tablePrefix}{name}"
    sdf = spark.createDataFrame(arrow_table.to_pandas())  # noqa: F821
    sdf.write.format(tableFormat).mode(str(writeMode)).save(target)
    tables.append({"table": name, "path": target, "rows": arrow_table.num_rows})
    print(f"  {name:<24} {arrow_table.num_rows:>10,} rows -> {target}")

# The contract the domain's own schema implies for these tables; the profile notebook checks
# the written tables against it.
contract = generation.domain_contract(result.schema, planned)  # the planned rows, not the written
mssparkutils.fs.put(contract_url, json.dumps(contract), True)  # noqa: F821
print("Contract written to", contract_url)
"""

GEN_RESULT = """result_value = {
    "domain": domain,
    "scale": scale,
    "seed": int(seed),
    "mode": str(mode) or "default",
    "tableFormat": tableFormat,
    "tablesPath": tables_root,
    "tablePrefix": str(tablePrefix),
    "tables": tables[:MAX_LISTED],
    "totalRows": sum(t["rows"] for t in tables),
    "contractPath": contract_url,
    "kernel": KERNEL,
}
print(json.dumps(result_value, indent=2)[:4000])
"""

GEN_EXIT = """mssparkutils.notebook.exit(json.dumps(result_value))  # noqa: F821
"""

GEN_CELLS = [
    (
        "markdown",
        """# Shape: generate a domain into ADLS Gen2 tables (Synapse PySpark notebook)

Runs on a Synapse **Apache Spark pool** with the Shape wheel and the `sqllocks-shape-domains`
wheel installed (pool packages or workspace packages; see `integrations/synapse/RUNBOOK.md`).
Shape needs **Python 3.11 or newer**, so the pool must use a runtime with it.

Generates every table of an installed domain at a scale preset with a seed **on the driver**
(`maxRows` refuses a scale too large for it), writes each as a Delta or Parquet folder
`<outputPath>/tables/<tablePrefix><table>`, and writes the contract that the domain's own schema
implies for these tables to `<outputPath>/<domain>/contract.json`.
`shape_profile_domain_synapse` profiles the tables and checks them against that contract.

The exit value is `{domain, scale, seed, mode, tableFormat, tablesPath, tablePrefix, tables,
totalRows, contractPath, kernel}`. `mssparkutils.notebook.exit` is the last statement, outside any
`try`/`except`.
""",
    ),
    ("code", GEN_PARAMETERS),
    ("code", GEN_HELPERS),
    ("code", GEN_RUN),
    ("code", GEN_WRITE),
    ("code", GEN_RESULT),
    ("code", GEN_EXIT),
]

GEN_PROFILE_PARAMETERS = """# Parameters cell (Synapse: toggle "parameter cell"). A pipeline overrides these.
# Paths are abfss://<container>@<account>.dfs.core.windows.net/<path> URLs.
domain = "retail"  # labels the artifacts
tablesPath = "abfss://shape@<account>.dfs.core.windows.net/generated/tables"  # shape_generate_synapse's
tablePrefix = ""  # the tables are <tablesPath>/<tablePrefix><table>
tableFormat = "delta"  # delta | parquet
contractPath = "abfss://shape@<account>.dfs.core.windows.net/generated/retail/contract.json"
baselinePath = ""  # optional earlier .shape artifact (abfss URL) to diff against
outputPath = "abfss://shape@<account>.dfs.core.windows.net/shape"  # artifacts go below this
failOnDrift = False  # True: drift against the baseline also fails the gate
linkedServiceName = ""  # optional: Synapse linked service that grants access to the storage
"""

GEN_PROFILE_HELPERS = """import json
import tempfile
from pathlib import Path

import pyarrow as pa

import shape
from shape.integrations.fabric import generation
from shape.integrations.run_folder import unique_run_name
from shape.kernel.dispatch import get_kernel

try:  # Synapse Spark pools predefine `mssparkutils`; newer runtimes also offer the import
    from notebookutils import mssparkutils
except ImportError:
    pass

KERNEL = get_kernel().NAME
print(f"Shape {shape.__version__}, kernel: {KERNEL}")


def _as_bool(value) -> bool:
    \"\"\"Pipeline parameters may arrive as strings.\"\"\"
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


failOnDrift = _as_bool(failOnDrift)
tableFormat = str(tableFormat).strip().lower()
if tableFormat not in ("delta", "parquet"):
    raise ValueError(f"tableFormat must be 'delta' or 'parquet', got {tableFormat!r}")
generation.check_name(str(domain), "domain")
if tablePrefix:
    generation.check_name(str(tablePrefix), "tablePrefix")

if linkedServiceName:
    spark.conf.set("spark.storage.synapse.linkedServiceName", linkedServiceName)  # noqa: F821
    spark.conf.set(  # noqa: F821
        "fs.azure.account.oauth.provider.type",
        "com.microsoft.azure.synapse.tokenlibrary.LinkedServiceBasedTokenProvider",
    )

LOCAL = Path(tempfile.mkdtemp(prefix="shape_"))


def _read_text(url: str) -> str:
    return mssparkutils.fs.head(url, 64 * 1024 * 1024)  # noqa: F821


def _fetch(url: str, name: str) -> str:
    local = LOCAL / name
    mssparkutils.fs.cp(url, f"file:{local}", False)  # noqa: F821
    return str(local)


def _publish_file(local: Path, url: str) -> None:
    mssparkutils.fs.cp(f"file:{local}", url, True)  # noqa: F821


def _publish_text(text: str, url: str) -> None:
    mssparkutils.fs.put(url, text, True)  # noqa: F821


safe_name = str(domain)
out_parent = f"{str(outputPath).rstrip('/')}/{safe_name}"  # one new folder per run below it
MAX_LISTED = 100  # keep the exit value small (well under 1 MB)
DRIVER_ROW_LIMIT = 20_000_000  # the Arrow copy of every table lives in driver memory
"""

GEN_PROFILE_PROFILE = """contract = json.loads(_read_text(contractPath))

# The contract names the tables: every one of them is read and profiled, whole (a sample would
# fail the contract's row counts).
sources = {}
for name in contract["tables"]:
    generation.check_name(name, "table")
    df = spark.read.format(tableFormat).load(  # noqa: F821
        f"{str(tablesPath).rstrip('/')}/{tablePrefix}{name}"
    )
    if int(spark.version.split(".")[0]) >= 4:  # noqa: F821
        sources[name] = df.toArrow()
    else:
        sources[name] = pa.Table.from_pandas(df.toPandas(), preserve_index=False)
    if sum(t.num_rows for t in sources.values()) > DRIVER_ROW_LIMIT:
        raise ValueError(f"more than {DRIVER_ROW_LIMIT:,} rows: profile a smaller scale")
total_rows = sum(t.num_rows for t in sources.values())
profile = shape.profile(sources, name=str(domain))
print(f"Profiled {total_rows:,} rows in {len(sources)} tables")
"""

GEN_PROFILE_CHECK = """violations, changes, drifted = list(shape.check(profile, contract).violations), [], False

if baselinePath:
    diff_result = shape.diff(shape.load(_fetch(baselinePath, "baseline.shape")), profile)
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

GEN_PROFILE_ARTIFACTS = """out_root = f"{out_parent}/{unique_run_name(out_parent, mssparkutils.fs.exists)}"
local_shape = LOCAL / f"{safe_name}.shape"
shape.save(profile, str(local_shape))
artifact_path = f"{out_root}/{safe_name}.shape"
_publish_file(local_shape, artifact_path)
_publish_text(profile.to_html(), f"{out_root}/{safe_name}.html")
_publish_text(json.dumps(profile.summary()), f"{out_root}/{safe_name}.summary.json")
print("Artifacts written to", out_root)
displayHTML(profile.to_html())  # noqa: F821 (predefined in Synapse notebooks)
"""

GEN_PROFILE_RESULT = """result = {
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

GEN_PROFILE_CELLS = [
    (
        "markdown",
        """# Shape: profile a generated domain and check it against its contract (Synapse PySpark notebook)

Run after `shape_generate_synapse`. Reads every table named in the domain's contract (Delta or
Parquet folders under `tablesPath`), profiles them together on the driver (so keys and foreign keys
between tables are detected), checks the profile against the contract, optionally diffs it against
a baseline, writes `.shape`, `.html` and `.summary.json` below `outputPath/<domain>/<timestamp>/`,
and returns a compact JSON result to the calling pipeline.

The exit value is `{domain, tables, rows, passed, violations, drifted, changes, artifactPath,
truncated, kernel}`. `passed` is false when any table breaks the contract. `mssparkutils.notebook.exit`
is the last statement, outside any `try`/`except`.
""",
    ),
    ("code", GEN_PROFILE_PARAMETERS),
    ("code", GEN_PROFILE_HELPERS),
    ("code", GEN_PROFILE_PROFILE),
    ("code", GEN_PROFILE_CHECK),
    ("code", GEN_PROFILE_ARTIFACTS),
    ("code", GEN_PROFILE_RESULT),
    ("code", EXIT),
]


def _cell(kind: str, source: str, index: int) -> dict:
    cell: dict = {
        "cell_type": kind,
        "id": f"cell-{index:02d}",
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }
    if kind == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
        if source.startswith("# Parameters cell"):
            cell["metadata"] = {"tags": ["parameters"]}
    return cell


def notebook(cells: list[tuple[str, str]] | None = None, description: str | None = None) -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": [_cell(kind, src, i) for i, (kind, src) in enumerate(cells or CELLS)],
        "metadata": {
            "kernelspec": {"name": "synapse_pyspark", "display_name": "Synapse PySpark"},
            "language_info": {"name": "python"},
            "description": description
            or "Shape profile, contract check and drift diff for ADLS Gen2 data",
            "save_output": True,
        },
    }


# ---------------------------------------------------------------------------------- pipeline

# The one expression the gate hinges on. VERIFY IN THE WORKSPACE ON FIRST RUN.
EXIT_VALUE = "json(activity('ProfileTable').output.status.Output.result.exitValue)"
PASSED_EXPRESSION = f"@{EXIT_VALUE}.passed"
FAIL_MESSAGE_EXPRESSION = (
    "@concat('Shape gate failed for ', pipeline().parameters.sourcePath, ': ', "
    f"string({EXIT_VALUE}.violations))"
)

POLICY = {
    "timeout": "0.12:00:00",
    "retry": 0,
    "retryIntervalInSeconds": 30,
    "secureInput": False,
    "secureOutput": False,
}

PARAMETER_TYPES = {
    "sourcePath": "string",
    "sourceFormat": "string",
    "tableName": "string",
    "contractPath": "string",
    "baselinePath": "string",
    "outputPath": "string",
    "failOnDrift": "bool",
    "linkedServiceName": "string",
}
PARAMETER_DEFAULTS = {
    "sourcePath": "abfss://data@<<STORAGE_ACCOUNT>>.dfs.core.windows.net/tables/orders",
    "sourceFormat": "delta",
    "tableName": "",
    "contractPath": "abfss://shape@<<STORAGE_ACCOUNT>>.dfs.core.windows.net/contracts/orders.json",
    "baselinePath": "",
    "outputPath": "abfss://shape@<<STORAGE_ACCOUNT>>.dfs.core.windows.net/shape",
    "failOnDrift": False,
    "linkedServiceName": "",
}


def _expr(text: str) -> dict:
    return {"value": text, "type": "Expression"}


def pipeline() -> dict:
    notebook_parameters = {
        name: {"value": _expr(f"@pipeline().parameters.{name}"), "type": kind}
        for name, kind in PARAMETER_TYPES.items()
    }
    return {
        "name": PIPELINE_NAME,
        "properties": {
            "description": (
                "Shape quality gate for ADLS Gen2 data: profile in a Synapse Spark notebook, "
                "check the contract, fail the pipeline when it does not hold."
            ),
            "activities": [
                {
                    "name": "ProfileTable",
                    "type": "SynapseNotebook",
                    "dependsOn": [],
                    "policy": POLICY,
                    "userProperties": [],
                    "typeProperties": {
                        "notebook": {"referenceName": NOTEBOOK_NAME, "type": "NotebookReference"},
                        "parameters": notebook_parameters,
                        "snapshot": True,
                        "sparkPool": {
                            "referenceName": "<<SPARK_POOL>>",
                            "type": "BigDataPoolReference",
                        },
                    },
                },
                {
                    "name": "CheckGate",
                    "type": "IfCondition",
                    "dependsOn": [
                        {"activity": "ProfileTable", "dependencyConditions": ["Succeeded"]}
                    ],
                    "userProperties": [],
                    "typeProperties": {
                        "expression": _expr(PASSED_EXPRESSION),
                        "ifTrueActivities": [],
                        "ifFalseActivities": [
                            {
                                "name": "FailGate",
                                "type": "Fail",
                                "dependsOn": [],
                                "userProperties": [],
                                "typeProperties": {
                                    "message": _expr(FAIL_MESSAGE_EXPRESSION),
                                    "errorCode": "ShapeGateFailed",
                                },
                            }
                        ],
                    },
                },
            ],
            "parameters": {
                name: {"type": kind, "defaultValue": PARAMETER_DEFAULTS[name]}
                for name, kind in PARAMETER_TYPES.items()
            },
            "annotations": ["shape"],
        },
    }


# ---------------------------------------------------------------- generation pipeline (PF-06)

GENERATE_EXIT_VALUE = "json(activity('GenerateDomain').output.status.Output.result.exitValue)"
PROFILE_EXIT_VALUE = "json(activity('ProfileAndCheck').output.status.Output.result.exitValue)"

GEN_NOTEBOOK_PARAMETER_TYPES = {
    "domain": "string",
    "scale": "string",
    "seed": "int",
    "mode": "string",
    "outputPath": "string",
    "tablePrefix": "string",
    "tableFormat": "string",
    "writeMode": "string",
    "linkedServiceName": "string",
}
GEN_PROFILE_PARAMETER_TYPES = {
    "domain": "string",
    "tablesPath": "string",
    "tablePrefix": "string",
    "tableFormat": "string",
    "contractPath": "string",
    "baselinePath": "string",
    "outputPath": "string",
    "failOnDrift": "bool",
    "linkedServiceName": "string",
}
GEN_PIPELINE_PARAMETERS = {
    "domain": ("string", "retail"),
    "scale": ("string", "small"),
    "seed": ("int", 42),
    "mode": ("string", ""),
    "outputPath": ("string", "abfss://shape@<<STORAGE_ACCOUNT>>.dfs.core.windows.net/generated"),
    "reportPath": ("string", "abfss://shape@<<STORAGE_ACCOUNT>>.dfs.core.windows.net/shape"),
    "tablePrefix": ("string", ""),
    "tableFormat": ("string", "delta"),
    "writeMode": ("string", "overwrite"),
    "baselinePath": ("string", ""),
    "failOnDrift": ("bool", False),
    "linkedServiceName": ("string", ""),
}


def _notebook_activity(
    name: str,
    notebook_name: str,
    parameters: dict,
    depends: list[dict],
) -> dict:
    return {
        "name": name,
        "type": "SynapseNotebook",
        "dependsOn": depends,
        "policy": POLICY,
        "userProperties": [],
        "typeProperties": {
            "notebook": {"referenceName": notebook_name, "type": "NotebookReference"},
            "parameters": parameters,
            "snapshot": True,
            "sparkPool": {"referenceName": "<<SPARK_POOL>>", "type": "BigDataPoolReference"},
        },
    }


def generate_pipeline() -> dict:
    def param(name: str, kind: str, pipeline_name: str | None = None) -> dict:
        return {"value": _expr(f"@pipeline().parameters.{pipeline_name or name}"), "type": kind}

    generate_args = {name: param(name, kind) for name, kind in GEN_NOTEBOOK_PARAMETER_TYPES.items()}
    profile_args = {
        name: param(name, kind)
        for name, kind in GEN_PROFILE_PARAMETER_TYPES.items()
        if name not in ("tablesPath", "contractPath", "outputPath")
    }
    # what the generate notebook wrote, and where the reports go
    profile_args["tablesPath"] = {
        "value": _expr(f"@{GENERATE_EXIT_VALUE}.tablesPath"),
        "type": "string",
    }
    profile_args["contractPath"] = {
        "value": _expr(f"@{GENERATE_EXIT_VALUE}.contractPath"),
        "type": "string",
    }
    profile_args["outputPath"] = param("outputPath", "string", "reportPath")
    return {
        "name": GEN_PIPELINE_NAME,
        "properties": {
            "description": (
                "Shape: generate a domain into ADLS Gen2 tables in a Synapse Spark notebook, "
                "profile them, check the profile against the domain's contract, and fail the "
                "pipeline when it does not hold."
            ),
            "activities": [
                _notebook_activity("GenerateDomain", GEN_NOTEBOOK_NAME, generate_args, []),
                _notebook_activity(
                    "ProfileAndCheck",
                    GEN_PROFILE_NOTEBOOK_NAME,
                    profile_args,
                    [{"activity": "GenerateDomain", "dependencyConditions": ["Succeeded"]}],
                ),
                {
                    "name": "CheckGate",
                    "type": "IfCondition",
                    "dependsOn": [
                        {"activity": "ProfileAndCheck", "dependencyConditions": ["Succeeded"]}
                    ],
                    "userProperties": [],
                    "typeProperties": {
                        "expression": _expr(f"@{PROFILE_EXIT_VALUE}.passed"),
                        "ifTrueActivities": [],
                        "ifFalseActivities": [
                            {
                                "name": "FailGate",
                                "type": "Fail",
                                "dependsOn": [],
                                "userProperties": [],
                                "typeProperties": {
                                    "message": _expr(
                                        "@concat('Shape generated data broke the contract of ', "
                                        "pipeline().parameters.domain, ': ', "
                                        f"string({PROFILE_EXIT_VALUE}.violations))"
                                    ),
                                    "errorCode": "ShapeContractFailed",
                                },
                            }
                        ],
                    },
                },
            ],
            "parameters": {
                name: {"type": kind, "defaultValue": default}
                for name, (kind, default) in GEN_PIPELINE_PARAMETERS.items()
            },
            "annotations": ["shape"],
        },
    }


def build() -> dict[str, dict]:
    return {
        f"notebooks/{NOTEBOOK_NAME}.ipynb": notebook(),
        f"pipelines/{PIPELINE_NAME}.json": pipeline(),
        f"notebooks/{GEN_NOTEBOOK_NAME}.ipynb": notebook(
            GEN_CELLS, "Shape: generate a domain into ADLS Gen2 tables, with its contract"
        ),
        f"notebooks/{GEN_PROFILE_NOTEBOOK_NAME}.ipynb": notebook(
            GEN_PROFILE_CELLS, "Shape: profile a generated domain and check its contract"
        ),
        f"pipelines/{GEN_PIPELINE_NAME}.json": generate_pipeline(),
    }


def main() -> None:
    for rel, doc in build().items():
        path = HERE / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print("wrote", path)


if __name__ == "__main__":
    main()
