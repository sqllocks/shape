"""DM-07: schema-level tests of the Fabric pipeline definitions.

These check structure and expressions only. Whether Fabric evaluates
``activity('ProfileTable').output.result.exitValue`` as written is verified in the
workspace on first run (see integrations/fabric/RUNBOOK.md).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys

import jsonschema
import nbformat
import pytest
from fabric_helpers import NOTEBOOKS, PIPELINES, UDF_DIR, run_notebook

NAMES = ["shape_gate_notebook", "shape_gate_spark", "shape_gate_udf"]

EXPRESSION = {
    "type": "object",
    "required": ["value", "type"],
    "properties": {"value": {"type": "string", "pattern": "^@"}, "type": {"const": "Expression"}},
}
ACTIVITY = {
    "type": "object",
    "required": ["name", "type", "dependsOn"],
    "properties": {
        "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]*$"},
        "type": {"enum": ["TridentNotebook", "IfCondition", "Fail", "UserDataFunctions"]},
        "dependsOn": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["activity", "dependencyConditions"],
                "properties": {
                    "activity": {"type": "string"},
                    "dependencyConditions": {
                        "type": "array",
                        "items": {"enum": ["Succeeded", "Failed", "Skipped", "Completed"]},
                    },
                },
            },
        },
        "typeProperties": {"type": "object"},
    },
}
PIPELINE_SCHEMA = {
    "type": "object",
    "required": ["properties"],
    "properties": {
        "properties": {
            "type": "object",
            "required": ["activities", "parameters"],
            "properties": {
                "activities": {"type": "array", "minItems": 1, "items": ACTIVITY},
                "parameters": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "required": ["type"],
                        "properties": {"type": {"enum": ["string", "bool", "int", "object"]}},
                    },
                },
            },
        }
    },
}
PARAM_TYPES = {"string": str, "bool": bool, "int": int, "object": dict}


def load(name: str) -> dict:
    return json.loads((PIPELINES / f"{name}.DataPipeline" / "pipeline-content.json").read_text())


def walk(activities):
    for a in activities:
        yield a
        tp = a.get("typeProperties", {})
        yield from walk(tp.get("ifTrueActivities", []))
        yield from walk(tp.get("ifFalseActivities", []))


def by_name(pipeline: dict) -> dict:
    return {a["name"]: a for a in walk(pipeline["properties"]["activities"])}


def expressions(node):
    if isinstance(node, dict):
        if node.get("type") == "Expression":
            yield node["value"]
        for v in node.values():
            yield from expressions(v)
    elif isinstance(node, list):
        for v in node:
            yield from expressions(v)


def load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_pipelines", PIPELINES / "build_pipelines.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------- structure


@pytest.mark.parametrize("name", NAMES)
def test_schema_and_platform_file(name):
    p = load(name)
    jsonschema.validate(p, PIPELINE_SCHEMA)
    plat = json.loads((PIPELINES / f"{name}.DataPipeline" / ".platform").read_text())
    assert plat["metadata"] == {"type": "DataPipeline", "displayName": name}
    assert re.fullmatch(r"[0-9a-f-]{36}", plat["config"]["logicalId"])
    names = [a["name"] for a in walk(p["properties"]["activities"])]
    assert len(names) == len(set(names))
    for a in walk(p["properties"]["activities"]):
        for dep in a["dependsOn"]:
            assert dep["activity"] in names


@pytest.mark.parametrize("name", NAMES)
def test_expressions_reference_real_parameters_and_activities(name):
    p = load(name)
    params = set(p["properties"]["parameters"])
    acts = set(by_name(p))
    for e in expressions(p):
        for ref in re.findall(r"pipeline\(\)\.parameters\.(\w+)", e):
            assert ref in params, (name, e)
        for ref in re.findall(r"activity\('(\w+)'\)", e):
            assert ref in acts, (name, e)


@pytest.mark.parametrize(
    "name, notebook",
    [("shape_gate_notebook", "shape_profile"), ("shape_gate_spark", "shape_profile_spark")],
)
def test_notebook_gate_graph(name, notebook):
    p = load(name)
    top = p["properties"]["activities"]
    assert [a["name"] for a in top] == ["ProfileTable", "CheckGate"]
    profile, gate = top
    assert profile["type"] == "TridentNotebook" and profile["dependsOn"] == []
    assert profile["typeProperties"]["notebookId"] == f"<<NOTEBOOK_ID:{notebook}>>"
    assert gate["type"] == "IfCondition"
    assert gate["dependsOn"] == [
        {"activity": "ProfileTable", "dependencyConditions": ["Succeeded"]}
    ]
    assert gate["typeProperties"]["expression"]["value"] == (
        "@json(activity('ProfileTable').output.result.exitValue).passed"
    )
    assert gate["typeProperties"]["ifTrueActivities"] == []
    (fail,) = gate["typeProperties"]["ifFalseActivities"]
    assert fail["type"] == "Fail" and fail["name"] == "FailGate"
    msg = fail["typeProperties"]["message"]["value"]
    assert "violations" in msg and "exitValue" in msg and "tableName" in msg


@pytest.mark.parametrize("name", ["shape_gate_notebook", "shape_gate_spark"])
def test_notebook_parameters_match_the_notebook_parameters_cell(name):
    nb_file = (
        "shape_profile.ipynb" if name == "shape_gate_notebook" else "shape_profile_spark.ipynb"
    )
    nb = nbformat.read(NOTEBOOKS / nb_file, as_version=4)
    cell = next(c for c in nb.cells if "parameters" in c.metadata.get("tags", []))
    tree = ast.parse(cell.source)
    defaults = {
        t.targets[0].id: ast.literal_eval(t.value) for t in tree.body if isinstance(t, ast.Assign)
    }
    p = load(name)
    passed = by_name(p)["ProfileTable"]["typeProperties"]["parameters"]
    assert set(passed) == set(defaults)
    for pname, spec in passed.items():
        assert spec["value"]["value"] == f"@pipeline().parameters.{pname}"
        assert isinstance(defaults[pname], PARAM_TYPES[spec["type"]])
        declared = p["properties"]["parameters"][pname]
        assert declared["type"] == spec["type"]


def test_exit_value_fields_used_by_the_pipelines_exist_in_the_notebook_result():
    for nb_file in ("shape_profile.ipynb", "shape_profile_spark.ipynb"):
        nb = nbformat.read(NOTEBOOKS / nb_file, as_version=4)
        keys = set()
        for c in nb.cells:
            if c.cell_type != "code":
                continue
            code = "\n".join(ln for ln in c.source.splitlines() if not ln.lstrip().startswith("%"))
            for node in ast.walk(ast.parse(code)):
                if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "result":
                    keys = {k.value for k in node.value.keys}
        assert {"passed", "violations"} <= keys


def test_udf_gate_graph_matches_function_signatures():
    p = load("shape_gate_udf")
    top = p["properties"]["activities"]
    assert [a["name"] for a in top] == ["ProfileFile", "CheckContract"]
    first, second = top
    assert first["type"] == second["type"] == "UserDataFunctions"
    assert first["typeProperties"]["functionName"] == "profileLakehouseFile"
    assert second["typeProperties"]["functionName"] == "checkProfile"
    assert second["dependsOn"] == [
        {"activity": "ProfileFile", "dependencyConditions": ["Succeeded"]}
    ]
    assert second["typeProperties"]["parameters"]["failOnViolation"] == {
        "value": True,
        "type": "bool",
    }
    # the second activity reads what the first wrote
    assert (
        first["typeProperties"]["parameters"]["outputPath"]["value"]["value"]
        == second["typeProperties"]["parameters"]["profilePath"]["value"]["value"]
    )
    # every parameter passed exists in the real function signature
    tree = ast.parse((UDF_DIR / "function_app.py").read_text())
    sigs = {
        n.name: {a.arg for a in n.args.args} for n in tree.body if isinstance(n, ast.FunctionDef)
    }
    for act in top:
        fn_name = act["typeProperties"]["functionName"]
        assert set(act["typeProperties"]["parameters"]) <= sigs[fn_name] - {"lakehouse"}


# ------------------------------------------------- generator, binding, evaluation


def test_committed_definitions_match_generator():
    mod = load_builder()
    for name, content in mod.build().items():
        assert load(name) == content


def test_bind_replaces_every_placeholder(tmp_path):
    out = tmp_path / "bound"
    subprocess.run(
        [
            sys.executable, str(PIPELINES / "build_pipelines.py"), "bind", str(out),
            "--workspace-id", "11111111-1111-1111-1111-111111111111",
            "--notebook", "shape_profile=22222222-2222-2222-2222-222222222222",
            "--notebook", "shape_profile_spark=33333333-3333-3333-3333-333333333333",
            "--notebook", "shape_generate=55555555-5555-5555-5555-555555555555",
            "--notebook", "shape_profile_domain=66666666-6666-6666-6666-666666666666",
            "--function-set", "44444444-4444-4444-4444-444444444444",
        ],
        check=True, capture_output=True,
    )  # fmt: skip
    for f in out.rglob("pipeline-content.json"):
        assert "<<" not in f.read_text(), f
        json.loads(f.read_text())


def _eval(expr: str, exit_value: str, params: dict) -> object:
    """Evaluate the two expression shapes used here against a notebook exit value."""
    exit_json = json.loads(exit_value)
    m = re.fullmatch(r"@json\(activity\('ProfileTable'\)\.output\.result\.exitValue\)\.(\w+)", expr)
    if m:
        return exit_json[m.group(1)]
    m = re.fullmatch(
        r"@concat\('([^']*)', pipeline\(\)\.parameters\.(\w+), '([^']*)', "
        r"string\(json\(activity\('ProfileTable'\)\.output\.result\.exitValue\)\.(\w+)\)\)",
        expr,
    )
    assert m, expr
    return m.group(1) + str(params[m.group(2)]) + m.group(3) + json.dumps(exit_json[m.group(4)])


def test_expressions_on_real_notebook_exit_values(lakehouse):
    """Locally simulate the gate on the day-1 and day-2 exit values."""
    p = load("shape_gate_notebook")
    gate = by_name(p)["CheckGate"]["typeProperties"]
    fail = by_name(p)["FailGate"]["typeProperties"]
    for table, expect_pass in (("orders_day1", True), ("orders_day2", False)):
        raw, _ = run_notebook(
            NOTEBOOKS / "shape_profile.ipynb",
            lakehouse,
            {
                "tableName": table,
                "contractPath": "contracts/orders.json",
                "baselinePath": "",
                "outputDir": "shape",
                "failOnDrift": False,
            },
        )
        assert _eval(gate["expression"]["value"], raw, {}) is expect_pass
        if not expect_pass:
            message = _eval(fail["message"]["value"], raw, {"tableName": table})
            assert f"Shape gate failed for {table}" in message
            assert "allowed_values" in message and "max_null_rate" in message


def test_runbook_flags_the_exit_value_expression_and_documents_all_pipelines():
    runbook = (PIPELINES.parent / "RUNBOOK.md").read_text()
    expr = "@json(activity('ProfileTable').output.result.exitValue).passed"
    assert expr in runbook
    line = next(
        ln for ln in runbook.splitlines() if expr in ln and "[VERIFY]" in ln or "**[VERIFY]**" in ln
    )
    assert line
    assert "verify in the workspace on first run" in runbook.lower()
    for name in NAMES:
        assert name in runbook
    assert "Owner live dry-run checklist" in runbook
