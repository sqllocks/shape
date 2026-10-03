"""ISS2-dbt: the Fabric pipeline pattern for dbt: a dbt job, the Shape notebook on the model
outputs, and the contract and drift gate, with one report for a failed dbt test and a Shape finding.

The notebook's code cells run locally against a directory standing in for the lakehouse (Delta
tables of the dbt models, the dbt run output as files), the pipeline definition is checked at
schema level and its expressions are evaluated on the notebook's real exit values. Not covered,
because it needs a Fabric workspace with the dbt job preview: the dbt job activity (``[VERIFY]``),
where the job writes ``run_results.json`` and ``manifest.json``, and how Fabric evaluates the
exit-value expressions. Those are the live dry-run checklist in integrations/fabric/RUNBOOK.md.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import jsonschema
import nbformat
import numpy as np
import pyarrow as pa
import pytest
from adf_expr import evaluate
from deltalake import write_deltalake
from fabric_helpers import NOTEBOOKS, PIPELINES, run_notebook

import shape

NOTEBOOK = NOTEBOOKS / "shape_profile_dbt.ipynb"
PIPELINE = PIPELINES / "shape_dbt_gate.DataPipeline" / "pipeline-content.json"
EXIT_KEYS = {
    "models",
    "rows",
    "passed",
    "dbtFailed",
    "dbtTotal",
    "violations",
    "drifted",
    "changes",
    "byColumn",
    "artifactPath",
    "reportPath",
    "truncated",
    "kernel",
}


def pipeline() -> dict:
    return json.loads(PIPELINE.read_text(encoding="utf-8"))


def activities(p: dict) -> dict:
    out = {}

    def walk(items):
        for a in items:
            out[a["name"]] = a
            tp = a.get("typeProperties", {})
            walk(tp.get("ifTrueActivities", []))
            walk(tp.get("ifFalseActivities", []))

    walk(p["properties"]["activities"])
    return out


def expressions(node):
    if isinstance(node, dict):
        if node.get("type") == "Expression":
            yield node["value"]
        for v in node.values():
            yield from expressions(v)
    elif isinstance(node, list):
        for v in node:
            yield from expressions(v)


# ------------------------------------------------------------------- structure


def test_the_notebook_is_a_fabric_python_notebook_that_installs_the_dbt_plugin():
    nb = nbformat.read(NOTEBOOK, as_version=4)
    nbformat.validate(nb)
    assert nb.metadata["microsoft"]["language"] == "python"
    assert nb.metadata["kernel_info"]["jupyter_kernel_name"] in ("python3.11", "python3.12")
    code = [c.source for c in nb.cells if c.cell_type == "code"]
    assert code[0].startswith("%%configure") and '"vCores": 8' in code[0]
    install = next(s for s in code if "%pip install" in s)
    assert 'find-links builtin "sqllocks-shape==' in install
    assert '"sqllocks-shape-dbt==' in install
    assert "_inlineInstallationEnabled" in install
    assert "[VERIFY]" in nb.cells[0].source


def test_the_pipeline_graph_runs_dbt_then_the_notebook_then_the_gate():
    p = pipeline()
    top = p["properties"]["activities"]
    assert [a["name"] for a in top] == ["RunDbt", "ProfileDbtOutputs", "CheckGate"]
    dbt, profile, gate = top
    assert dbt["type"] == "DbtJob" and dbt["dependsOn"] == []
    assert dbt["typeProperties"]["dbtJobId"] == "<<DBT_JOB_ID>>"
    # a failed dbt test must still produce the combined report: the notebook runs after the dbt
    # job *completes*, and the gate fails the run
    assert profile["dependsOn"] == [{"activity": "RunDbt", "dependencyConditions": ["Completed"]}]
    assert profile["typeProperties"]["notebookId"] == "<<NOTEBOOK_ID:shape_profile_dbt>>"
    assert profile["typeProperties"]["parameters"]["_inlineInstallationEnabled"] == {
        "value": True,
        "type": "bool",
    }
    assert gate["dependsOn"] == [
        {"activity": "ProfileDbtOutputs", "dependencyConditions": ["Succeeded"]}
    ]
    (fail,) = gate["typeProperties"]["ifFalseActivities"]
    assert fail["type"] == "Fail" and fail["typeProperties"]["errorCode"] == "ShapeDbtGateFailed"


def test_the_dbt_job_activity_is_flagged_for_verification():
    p = pipeline()
    assert "[VERIFY]" in p["properties"]["description"]
    runbook = (PIPELINES.parent / "RUNBOOK.md").read_text(encoding="utf-8")
    assert "shape_dbt_gate" in runbook and "DbtJob" in runbook and "[VERIFY]" in runbook


def test_expressions_reference_real_parameters_and_activities():
    p = pipeline()
    params, acts = set(p["properties"]["parameters"]), set(activities(p))
    for e in expressions(p):
        for ref in re.findall(r"pipeline\(\)\.parameters\.(\w+)", e):
            assert ref in params, e
        for ref in re.findall(r"activity\('(\w+)'\)", e):
            assert ref in acts, e


def test_the_notebook_parameters_match_the_parameters_cell():
    nb = nbformat.read(NOTEBOOK, as_version=4)
    cell = next(c for c in nb.cells if "parameters" in c.metadata.get("tags", []))
    defaults = {
        t.targets[0].id: ast.literal_eval(t.value)
        for t in ast.parse(cell.source).body
        if isinstance(t, ast.Assign)
    }
    p = pipeline()
    passed = activities(p)["ProfileDbtOutputs"]["typeProperties"]["parameters"]
    passed = {k: v for k, v in passed.items() if k != "_inlineInstallationEnabled"}
    assert set(passed) == set(defaults)
    kinds = {"string": str, "bool": bool}
    for name, spec in passed.items():
        assert spec["value"]["value"] == f"@pipeline().parameters.{name}"
        assert isinstance(defaults[name], kinds[spec["type"]])
        assert p["properties"]["parameters"][name]["type"] == spec["type"]


def test_the_exit_value_fields_the_pipeline_reads_exist_in_the_notebook_result():
    nb = nbformat.read(NOTEBOOK, as_version=4)
    keys: set[str] = set()
    for c in nb.cells:
        if c.cell_type != "code":
            continue
        code = "\n".join(ln for ln in c.source.splitlines() if not ln.lstrip().startswith("%"))
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "result":
                keys = {k.value for k in node.value.keys}
    assert keys == EXIT_KEYS
    read = set()
    for e in expressions(pipeline()):
        read |= set(re.findall(r"exitValue\)\.(\w+)", e))
    assert read == {"passed", "reportPath", "dbtFailed", "violations"}
    assert read <= keys


def test_committed_definitions_match_the_generator():
    spec = importlib.util.spec_from_file_location(
        "build_pipelines", PIPELINES / "build_pipelines.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.build()["shape_dbt_gate"] == pipeline()


def test_bind_fills_in_the_dbt_job_and_the_notebook(tmp_path):
    out = tmp_path / "bound"
    subprocess.run(
        [
            sys.executable, str(PIPELINES / "build_pipelines.py"), "bind", str(out),
            "--workspace-id", "11111111-1111-1111-1111-111111111111",
            "--notebook", "shape_profile_dbt=77777777-7777-7777-7777-777777777777",
            "--function-set", "44444444-4444-4444-4444-444444444444",
            "--dbt-job", "88888888-8888-8888-8888-888888888888",
        ],
        check=True, capture_output=True,
    )  # fmt: skip
    text = (out / "shape_dbt_gate.DataPipeline" / "pipeline-content.json").read_text()
    assert "88888888-8888-8888-8888-888888888888" in text
    assert "77777777-7777-7777-7777-777777777777" in text


def test_the_schema_of_the_definition():
    activity = {
        "type": "object",
        "required": ["name", "type", "dependsOn"],
        "properties": {
            "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]*$"},
            "type": {"enum": ["DbtJob", "TridentNotebook", "IfCondition", "Fail"]},
        },
    }
    jsonschema.validate(
        pipeline(),
        {
            "type": "object",
            "properties": {
                "properties": {
                    "required": ["activities", "parameters"],
                    "properties": {"activities": {"type": "array", "items": activity}},
                }
            },
        },
    )
    plat = json.loads((PIPELINE.parent / ".platform").read_text())
    assert plat["metadata"] == {"type": "DataPipeline", "displayName": "shape_dbt_gate"}


# ------------------------------------------------------------- the notebook, locally

RUN_RESULTS = {
    "metadata": {"dbt_version": "1.11.0", "generated_at": "2026-10-02T00:00:00Z"},
    "results": [
        {"unique_id": "model.p.orders", "status": "success", "message": "OK", "failures": None},
        {"unique_id": "test.p.range", "status": "pass", "message": None, "failures": 0},
    ],
}
MANIFEST = {
    "nodes": {
        "model.p.orders": {"resource_type": "model", "name": "orders"},
        "test.p.range": {
            "resource_type": "test",
            "name": "dbt_utils_accepted_range_orders_amount",
            "attached_node": "model.p.orders",
            "column_name": "amount",
            "tags": ["shape"],
            "test_metadata": {"name": "accepted_range", "namespace": "dbt_utils", "kwargs": {}},
        },
    }
}


def models(scale: float = 1.0) -> dict[str, pa.Table]:
    rng = np.random.default_rng(3)
    n = 300
    return {
        "orders": pa.table(
            {
                "order_id": list(range(n)),
                "status": rng.choice(["placed", "shipped"], n),
                "amount": rng.lognormal(3, 0.4, n) * scale,
            }
        ),
        "customers": pa.table({"customer_id": list(range(60))}),
    }


@pytest.fixture()
def lh(tmp_path: Path) -> Path:
    root = tmp_path / "lakehouse"
    (root / "Files" / "dbt").mkdir(parents=True)
    (root / "Files" / "contracts").mkdir()
    (root / "Files" / "baselines").mkdir()
    (root / "Tables").mkdir()
    for name, table in models().items():
        write_deltalake(str(root / "Tables" / name), table)
    base = shape.profile(models(), name="dbt")
    shape.save(base, str(root / "Files" / "baselines" / "dbt.shape"))
    contract = {
        "tables": {
            "orders": {
                "columns": {
                    "order_id": {"nullable": False, "unique": True},
                    "status": {"allowed_values": ["placed", "shipped"]},
                    "amount": {"min": 0, "max": 400},
                }
            }
        }
    }
    (root / "Files" / "contracts" / "dbt_models.json").write_text(json.dumps(contract))
    (root / "Files" / "dbt" / "manifest.json").write_text(json.dumps(MANIFEST))
    (root / "Files" / "dbt" / "run_results.json").write_text(json.dumps(RUN_RESULTS))
    return root


def params(lh: Path, **kw) -> dict:
    """What the pipeline passes. ``tableRoot`` is a parameter, so the stand-in lakehouse is not
    substituted into it as it is into the notebook's own paths."""
    p = {
        "models": "orders,customers",
        "tableRoot": str(lh / "Tables"),
        "contractPath": "contracts/dbt_models.json",
        "baselinePath": "baselines/dbt.shape",
        "runResultsPath": "dbt/run_results.json",
        "manifestPath": "dbt/manifest.json",
        "outputDir": "shape",
        "failOnDrift": True,
    }
    p.update(kw)
    return p


def run(lh: Path, **kw) -> dict:
    raw, _ = run_notebook(NOTEBOOK, lh, params(lh, **kw))
    assert raw is not None, "the notebook never called notebookutils.notebook.exit"
    assert len(raw.encode()) < 1_000_000
    out = json.loads(raw)
    assert EXIT_KEYS == set(out)
    return out


def test_a_clean_run_passes_and_writes_the_report(lh):
    out = run(lh)
    assert out["passed"] is True and out["dbtFailed"] == 0 and out["dbtTotal"] == 2
    assert out["models"] == {"orders": 300, "customers": 60} and out["rows"] == 360
    report = lh / "Files" / out["reportPath"]
    assert report.name == "report.md" and report.read_text().startswith("# dbt and Shape: PASS")
    assert json.loads(report.with_name("report.json").read_text())["ok"] is True
    assert (lh / "Files" / out["artifactPath"]).is_file()


def test_a_failed_dbt_test_fails_the_gate_with_the_failure_in_the_exit_value(lh):
    failed = json.loads(json.dumps(RUN_RESULTS))
    failed["results"][1].update(status="fail", message="Got 4 results", failures=4)
    (lh / "Files" / "dbt" / "run_results.json").write_text(json.dumps(failed))
    out = run(lh)
    assert out["passed"] is False and out["dbtFailed"] == 1
    text = (lh / "Files" / out["reportPath"]).read_text()
    assert "| fail | orders | amount | dbt_utils.accepted_range |" in text


def test_drift_and_a_failed_dbt_test_on_one_column_appear_together(lh):
    for name, table in models(scale=10.0).items():  # day 2: every amount is ten times as large
        write_deltalake(str(lh / "Tables" / name), table, mode="overwrite")
    failed = json.loads(json.dumps(RUN_RESULTS))
    failed["results"][1].update(status="fail", message="Got 120 results", failures=120)
    (lh / "Files" / "dbt" / "run_results.json").write_text(json.dumps(failed))
    out = run(lh)
    assert out["passed"] is False
    assert out["drifted"] is True and out["byColumn"] == ["orders.amount"]
    assert {v["rule"] for v in out["violations"]} == {"max"}
    text = (lh / "Files" / out["reportPath"]).read_text()
    assert "## Columns flagged by more than one check" in text and "orders.amount" in text


def test_drift_alone_fails_only_with_fail_on_drift(lh):
    for name, table in models(scale=0.5).items():
        write_deltalake(str(lh / "Tables" / name), table, mode="overwrite")
    assert run(lh, failOnDrift=False)["passed"] is True
    assert run(lh, failOnDrift=True)["passed"] is False


def test_without_a_dbt_run_the_report_has_shape_findings_only(lh):
    out = run(lh, runResultsPath="", manifestPath="", failOnDrift=False)
    assert out["passed"] is True and out["dbtTotal"] == 0


def test_a_model_name_that_is_not_a_name_is_refused(lh):
    with pytest.raises(ValueError, match="not a model name"):
        run_notebook(NOTEBOOK, lh, params(lh, models="orders,../x"))


def test_the_pipeline_expressions_on_real_exit_values(lh):
    p = pipeline()
    acts = activities(p)
    gate = acts["CheckGate"]["typeProperties"]
    fail = gate["ifFalseActivities"][0]["typeProperties"]
    ctx = {
        "parameters": {k: v["defaultValue"] for k, v in p["properties"]["parameters"].items()},
        "activities": {},
    }
    raw, _ = run_notebook(NOTEBOOK, lh, params(lh))
    ctx["activities"]["ProfileDbtOutputs"] = {"result": {"exitValue": raw}}
    assert evaluate(gate["expression"], ctx) is True

    failed = json.loads(json.dumps(RUN_RESULTS))
    failed["results"][1].update(status="fail", message="Got 4 results", failures=4)
    (lh / "Files" / "dbt" / "run_results.json").write_text(json.dumps(failed))
    raw, _ = run_notebook(NOTEBOOK, lh, params(lh))
    ctx["activities"]["ProfileDbtOutputs"] = {"result": {"exitValue": raw}}
    assert evaluate(gate["expression"], ctx) is False
    message = evaluate(fail["message"], ctx)
    assert message.startswith("dbt or Shape gate failed (see shape/dbt/")
    assert "report.md): 1 dbt failure(s); " in message
