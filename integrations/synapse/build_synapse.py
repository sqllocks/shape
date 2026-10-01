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
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa

import shape
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
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
out_root = f"{str(outputPath).rstrip('/')}/{safe_name}/{stamp}"
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

ARTIFACTS = """SUMMARY_KEYS = (
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


def notebook() -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": [_cell(kind, src, i) for i, (kind, src) in enumerate(CELLS)],
        "metadata": {
            "kernelspec": {"name": "synapse_pyspark", "display_name": "Synapse PySpark"},
            "language_info": {"name": "python"},
            "description": "Shape profile, contract check and drift diff for ADLS Gen2 data",
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


def build() -> dict[str, dict]:
    return {
        f"notebooks/{NOTEBOOK_NAME}.ipynb": notebook(),
        f"pipelines/{PIPELINE_NAME}.json": pipeline(),
    }


def main() -> None:
    for rel, doc in build().items():
        path = HERE / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print("wrote", path)


if __name__ == "__main__":
    main()
