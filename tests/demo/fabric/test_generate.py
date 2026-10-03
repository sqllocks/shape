"""PF-06: generation in Fabric: the generate and profile-domain notebooks, the
generate-then-profile pipeline and the ``generateSample`` User Data Function.

The notebooks' code cells run locally against a directory standing in for the lakehouse (Delta
tables written with ``deltalake``), the pipeline definition is checked at schema level and its
expressions are evaluated on the notebooks' real exit values, and the function is called through
the real Fabric SDK. Not covered, because it needs a Fabric workspace: how Fabric evaluates the
exit-value expressions, the Functions activity, and the real lakehouse mount. Those are the live
dry-run checklist in integrations/fabric/RUNBOOK.md.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import jsonschema
import nbformat
import pyarrow as pa
import pytest
from adf_expr import evaluate
from deltalake import DeltaTable, write_deltalake
from fabric_helpers import NOTEBOOKS, PIPELINES, run_notebook

import shape
from shape.integrations.fabric import generation

GEN_NB = NOTEBOOKS / "shape_generate.ipynb"
DOMAIN_NB = NOTEBOOKS / "shape_profile_domain.ipynb"
PIPELINE = PIPELINES / "shape_generate_gate.DataPipeline" / "pipeline-content.json"

GENERATE_KEYS = {
    "domain",
    "scale",
    "seed",
    "mode",
    "tablePrefix",
    "tables",
    "totalRows",
    "contractPath",
    "kernel",
}
DOMAIN_KEYS = {
    "domain",
    "tables",
    "rows",
    "passed",
    "violations",
    "drifted",
    "changes",
    "artifactPath",
    "truncated",
    "kernel",
}
RETAIL_TABLES = 9


@pytest.fixture()
def lh(tmp_path: Path) -> Path:
    """A directory standing in for /lakehouse/default."""
    (tmp_path / "lakehouse" / "Files").mkdir(parents=True)
    (tmp_path / "lakehouse" / "Tables").mkdir()
    return tmp_path / "lakehouse"


def _generate_params(**kw) -> dict:
    p = {
        "domain": "retail",
        "scale": "small",
        "seed": 42,
        "mode": "",
        "tablePrefix": "",
        "writeMode": "overwrite",
        "outputDir": "shape",
    }
    p.update(kw)
    return p


def _domain_params(**kw) -> dict:
    p = {
        "domain": "retail",
        "contractPath": "shape/retail/contract.json",
        "tablePrefix": "",
        "baselinePath": "",
        "outputDir": "shape",
        "failOnDrift": False,
    }
    p.update(kw)
    return p


def _generate(lh: Path, **kw) -> dict:
    raw, _ = run_notebook(GEN_NB, lh, _generate_params(**kw))
    assert raw is not None, "the generate notebook never called notebookutils.notebook.exit"
    assert len(raw.encode()) < 1_000_000
    out = json.loads(raw)
    assert GENERATE_KEYS <= set(out)
    return out


def _check(lh: Path, **kw) -> dict:
    raw, _ = run_notebook(DOMAIN_NB, lh, _domain_params(**kw))
    assert raw is not None, "the profile notebook never called notebookutils.notebook.exit"
    assert len(raw.encode()) < 1_000_000
    out = json.loads(raw)
    assert DOMAIN_KEYS <= set(out)
    assert isinstance(out["passed"], bool) and isinstance(out["violations"], list)
    return out


def _delta(lh: Path, name: str) -> pa.Table:
    return DeltaTable(str(lh / "Tables" / name)).to_pyarrow_table()


# ----------------------------------------------------------------- notebook structure


@pytest.mark.parametrize("path", [GEN_NB, DOMAIN_NB])
def test_valid_nbformat_with_fabric_metadata(path):
    nb = nbformat.read(path, as_version=4)
    nbformat.validate(nb)
    assert nb.metadata["microsoft"]["language"] == "python"
    assert nb.metadata["kernel_info"]["jupyter_kernel_name"] in ("python3.11", "python3.12")
    src = [c.source for c in nb.cells if c.cell_type == "code"]
    assert src[0].startswith("%%configure") and '"vCores": 8' in src[0]
    install = next(s for s in src if "%pip install" in s)
    assert 'find-links builtin "sqllocks-shape==' in install
    assert '"sqllocks-shape-domains==' in install  # the domains come from their own wheel


@pytest.mark.parametrize(
    "path, names",
    [
        (GEN_NB, {"domain", "scale", "seed", "mode", "tablePrefix", "writeMode", "outputDir"}),
        (
            DOMAIN_NB,
            {"domain", "contractPath", "tablePrefix", "baselinePath", "outputDir", "failOnDrift"},
        ),
    ],
)
def test_parameters_cell_and_exit_placement(path, names):
    nb = nbformat.read(path, as_version=4)
    code = [c for c in nb.cells if c.cell_type == "code"]
    tagged = [c for c in code if "parameters" in c.metadata.get("tags", [])]
    assert len(tagged) == 1
    assigned = {
        t.targets[0].id for t in ast.parse(tagged[0].source).body if isinstance(t, ast.Assign)
    }
    assert assigned == names

    # exit: exactly once, in the last cell, at top level, never inside try/except
    calls = 0
    for c in code:
        tree = ast.parse(
            "\n".join(ln for ln in c.source.splitlines() if not ln.lstrip().startswith("%"))
        )
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and ast.unparse(node.func) == "notebookutils.notebook.exit"
            ):
                calls += 1
                assert c is code[-1]
                assert node in [getattr(s, "value", None) for s in tree.body]
    assert calls == 1
    assert not any(isinstance(n, ast.Try) for n in ast.walk(ast.parse(code[-1].source)))


# ----------------------------------------------------------------- generate notebook


def test_the_notebook_writes_every_table_as_delta_with_the_engine_output(lh):
    out = _generate(lh)
    assert out["domain"] == "retail" and out["seed"] == 42 and out["scale"] == "small"
    assert [t["table"] for t in out["tables"]] == list(
        generation.generate_domain("retail").generation_order
    )
    assert len(out["tables"]) == RETAIL_TABLES
    assert out["totalRows"] == sum(t["rows"] for t in out["tables"]) == 21750
    expected = shape.generate("retail", scale="small", seed=42)
    for entry in out["tables"]:
        written = _delta(lh, entry["deltaTable"])
        assert entry["rows"] == written.num_rows == expected[entry["table"]].num_rows
        # the Delta table holds exactly the engine's rows (timestamps stored as microseconds)
        assert written.equals(generation.delta_ready(expected[entry["table"]]).cast(written.schema))


def test_delta_tables_have_no_nanosecond_timestamps(lh):
    _generate(lh)
    for table in ("customer", "order", "order_line"):
        for field in _delta(lh, table).schema:
            assert not (pa.types.is_timestamp(field.type) and field.type.unit == "ns")


def test_the_notebook_writes_the_contract_and_a_manifest(lh):
    out = _generate(lh)
    contract = json.loads((lh / "Files" / out["contractPath"]).read_text(encoding="utf-8"))
    assert out["contractPath"] == "shape/retail/contract.json"
    assert set(contract["tables"]) == {t["table"] for t in out["tables"]}
    manifest = json.loads(
        (lh / "Files" / "shape" / "retail" / "generation.json").read_text(encoding="utf-8")
    )
    assert manifest["seed"] == 42 and len(manifest["tables"]) == RETAIL_TABLES


def test_table_prefix_mode_and_scale_are_honoured(lh):
    out = _generate(lh, tablePrefix="r_", mode="star", scale="fabric_demo", seed=7)
    assert out["mode"] == "star" and out["scale"] == "fabric_demo" and out["seed"] == 7
    assert all(t["deltaTable"] == f"r_{t['table']}" for t in out["tables"])
    assert (lh / "Tables" / "r_customer" / "_delta_log").is_dir()
    assert not (lh / "Tables" / "customer").exists()
    expected = shape.generate("retail", scale="fabric_demo", seed=7, mode="star")
    assert _delta(lh, "r_customer").num_rows == expected["customer"].num_rows


def test_write_mode_overwrite_is_idempotent_and_append_adds_rows(lh):
    first = _generate(lh)
    again = _generate(lh)
    assert first["totalRows"] == again["totalRows"]
    assert _delta(lh, "customer").num_rows == 1000
    appended = _generate(lh, writeMode="append")
    assert appended["totalRows"] == first["totalRows"]
    assert _delta(lh, "customer").num_rows == 2000


def test_the_same_seed_gives_the_same_tables_and_another_seed_does_not(lh, tmp_path):
    _generate(lh, tablePrefix="a_")
    _generate(lh, tablePrefix="b_")
    _generate(lh, tablePrefix="c_", seed=43)
    assert _delta(lh, "a_order").equals(_delta(lh, "b_order"))
    assert not _delta(lh, "a_order").equals(_delta(lh, "c_order"))


@pytest.mark.parametrize(
    "bad",
    [
        {"domain": "../etc"},
        {"domain": "nope"},
        {"scale": "../x"},
        {"scale": "huge"},
        {"mode": "snowflake"},
        {"tablePrefix": "../x"},
        {"tablePrefix": "a b"},
        {"outputDir": "../escape"},
        {"writeMode": "merge"},
    ],
)
def test_bad_parameters_fail_before_anything_is_written(lh, bad):
    with pytest.raises(generation.GenerationRequestError):
        run_notebook(GEN_NB, lh, _generate_params(**bad))
    assert list((lh / "Tables").iterdir()) == []
    assert list((lh / "Files").iterdir()) == []


# ------------------------------------------------------------ profile-domain notebook


def test_generated_data_passes_the_domains_contract(lh):
    _generate(lh)
    out = _check(lh)
    assert out["passed"] is True and out["violations"] == []
    assert out["domain"] == "retail" and out["rows"] == 21750 and out["drifted"] is False
    assert out["tables"]["customer"] == 1000 and len(out["tables"]) == RETAIL_TABLES
    artifact = lh / "Files" / out["artifactPath"]
    assert artifact.suffix == ".shape"
    assert artifact.with_suffix(".html").exists() and artifact.with_suffix(".summary.json").exists()
    profile = shape.load(str(artifact))
    assert set(profile.tables) == set(out["tables"])
    assert profile.tables["order"]["row_count"] == 5000


def test_the_profile_finds_the_foreign_keys_between_the_generated_tables(lh):
    _generate(lh)
    out = _check(lh)
    profile = shape.load(str(lh / "Files" / out["artifactPath"]))
    found = {(r["parent"], r["child"]) for r in profile.to_dict()["relationships"]}
    assert {("customer", "order"), ("order", "order_line"), ("product", "order_line")} <= found


def test_tables_that_break_the_contract_fail_the_gate(lh):
    _generate(lh)
    # a table with the wrong number of rows
    customer = _delta(lh, "customer")
    write_deltalake(str(lh / "Tables" / "customer"), customer.slice(0, 900), mode="overwrite")
    # a null in a column the schema says is never null, and a value outside an enumerated set
    store = _delta(lh, "store")
    names = store.column_names
    nulled = store["store_name"].to_pylist()
    nulled[0] = None
    store = store.set_column(
        names.index("store_name"), "store_name", pa.array(nulled, pa.large_string())
    )
    write_deltalake(str(lh / "Tables" / "store"), store, mode="overwrite")
    out = _check(lh)
    rules = {(v["column"] or "", v["rule"]) for v in out["violations"]}
    assert out["passed"] is False
    assert ("", "customer:row_count.min") in rules
    assert ("store.store_name", "nullable") in rules


def test_a_missing_table_fails_instead_of_passing_vacuously(lh):
    _generate(lh)
    import shutil

    shutil.rmtree(lh / "Tables" / "return")
    with pytest.raises(Exception, match="return"):
        run_notebook(DOMAIN_NB, lh, _domain_params())


def test_baseline_drift_is_reported_and_fails_only_with_fail_on_drift(lh):
    _generate(lh, tablePrefix="a_")
    base = _check(lh, tablePrefix="a_", contractPath="shape/retail/contract.json")
    assert base["passed"] is True
    # artifact folders are named by the second: keep the baseline apart from later runs' output
    baseline = "baselines/retail_base.shape"
    (lh / "Files" / "baselines").mkdir()
    (lh / "Files" / baseline).write_bytes((lh / "Files" / base["artifactPath"]).read_bytes())
    same = _check(lh, tablePrefix="a_", baselinePath=baseline, failOnDrift="true")
    assert same["passed"] is True and same["drifted"] is False
    # another seed's tables give a different profile: reported as drift, a failure only on request
    _generate(lh, tablePrefix="a_", seed=99)
    kw = {"tablePrefix": "a_", "baselinePath": baseline}
    seen = _check(lh, **kw)
    assert seen["drifted"] is True and seen["changes"] and seen["passed"] is True
    gated = _check(lh, failOnDrift="True", **kw)
    assert gated["passed"] is False and gated["violations"][-1]["rule"] == "drift"


# ---------------------------------------------------------------------- the pipeline

ACTIVITY = {
    "type": "object",
    "required": ["name", "type", "dependsOn", "typeProperties"],
    "properties": {
        "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]*$"},
        "type": {"enum": ["TridentNotebook", "IfCondition", "Fail"]},
        "dependsOn": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["activity", "dependencyConditions"],
                "properties": {
                    "dependencyConditions": {
                        "type": "array",
                        "items": {"enum": ["Succeeded", "Failed", "Skipped", "Completed"]},
                    }
                },
            },
        },
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
                "activities": {"type": "array", "minItems": 3, "maxItems": 3, "items": ACTIVITY},
                "parameters": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "required": ["type", "defaultValue"],
                        "properties": {"type": {"enum": ["string", "bool", "int", "object"]}},
                    },
                },
            },
        }
    },
}
PARAM_TYPES = {"string": str, "bool": bool, "int": int, "object": dict}


def _pipeline() -> dict:
    return json.loads(PIPELINE.read_text(encoding="utf-8"))


def _cell_defaults(path: Path) -> dict:
    nb = nbformat.read(path, as_version=4)
    cell = next(c for c in nb.cells if "parameters" in c.metadata.get("tags", []))
    return {
        t.targets[0].id: ast.literal_eval(t.value)
        for t in ast.parse(cell.source).body
        if isinstance(t, ast.Assign)
    }


def test_the_pipeline_is_schema_valid_and_generates_then_profiles_then_checks():
    p = _pipeline()
    jsonschema.validate(p, PIPELINE_SCHEMA)
    generate, profile, gate = p["properties"]["activities"]
    assert [a["name"] for a in (generate, profile, gate)] == [
        "GenerateDomain",
        "ProfileAndCheck",
        "CheckGate",
    ]
    assert generate["dependsOn"] == []
    assert generate["typeProperties"]["notebookId"] == "<<NOTEBOOK_ID:shape_generate>>"
    assert profile["dependsOn"] == [
        {"activity": "GenerateDomain", "dependencyConditions": ["Succeeded"]}
    ]
    assert profile["typeProperties"]["notebookId"] == "<<NOTEBOOK_ID:shape_profile_domain>>"
    assert gate["type"] == "IfCondition"
    assert gate["dependsOn"] == [
        {"activity": "ProfileAndCheck", "dependencyConditions": ["Succeeded"]}
    ]
    assert gate["typeProperties"]["ifTrueActivities"] == []
    (fail,) = gate["typeProperties"]["ifFalseActivities"]
    assert fail["type"] == "Fail" and fail["typeProperties"]["errorCode"] == "ShapeContractFailed"
    platform = json.loads((PIPELINE.parent / ".platform").read_text(encoding="utf-8"))
    assert platform["metadata"] == {"type": "DataPipeline", "displayName": "shape_generate_gate"}


@pytest.mark.parametrize(
    "activity, notebook, from_exit_value",
    [
        ("GenerateDomain", GEN_NB, set()),
        ("ProfileAndCheck", DOMAIN_NB, {"contractPath"}),
    ],
)
def test_notebook_parameters_match_the_notebook_parameters_cell(
    activity, notebook, from_exit_value
):
    p = _pipeline()
    acts = {a["name"]: a for a in p["properties"]["activities"]}
    passed = acts[activity]["typeProperties"]["parameters"]
    # read by Fabric, not by the notebook (test_inline_install.py)
    passed = {k: v for k, v in passed.items() if k != "_inlineInstallationEnabled"}
    defaults = _cell_defaults(notebook)
    assert set(passed) == set(defaults)
    for name, spec in passed.items():
        assert isinstance(defaults[name], PARAM_TYPES[spec["type"]]), name
        if name in from_exit_value:
            continue
        assert spec["value"]["value"] == f"@pipeline().parameters.{name}"
        declared = p["properties"]["parameters"][name]
        assert declared["type"] == spec["type"]
        assert declared["defaultValue"] == defaults[name]
    # every pipeline parameter is passed to some notebook
    used = set(re.findall(r"pipeline\(\)\.parameters\.(\w+)", json.dumps(p)))
    assert used == set(p["properties"]["parameters"])


def test_expressions_reference_real_parameters_and_activities():
    p = _pipeline()
    text = json.dumps(p)
    for ref in re.findall(r"pipeline\(\)\.parameters\.(\w+)", text):
        assert ref in p["properties"]["parameters"], ref
    assert set(re.findall(r"activity\('(\w+)'\)", text)) == {"GenerateDomain", "ProfileAndCheck"}


def test_exit_value_fields_the_pipeline_reads_exist_in_the_notebook_results():
    def result_keys(path: Path) -> set[str]:
        keys: set[str] = set()
        for c in nbformat.read(path, as_version=4).cells:
            if c.cell_type != "code":
                continue
            code = "\n".join(ln for ln in c.source.splitlines() if not ln.lstrip().startswith("%"))
            for node in ast.walk(ast.parse(code)):
                if (
                    isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Dict)
                    and getattr(node.targets[0], "id", "") in ("result", "result_value")
                ):
                    keys |= {k.value for k in node.value.keys}
        return keys

    text = json.dumps(_pipeline())
    read = {
        "GenerateDomain": set(
            re.findall(r"activity\('GenerateDomain'\)\.output\.result\.exitValue\)\.(\w+)", text)
        ),
        "ProfileAndCheck": set(
            re.findall(r"activity\('ProfileAndCheck'\)\.output\.result\.exitValue\)\.(\w+)", text)
        ),
    }
    assert read["GenerateDomain"] == {"contractPath"}
    assert {"passed", "violations"} <= read["ProfileAndCheck"]
    assert read["GenerateDomain"] <= result_keys(GEN_NB) == GENERATE_KEYS
    assert read["ProfileAndCheck"] <= result_keys(DOMAIN_NB) == DOMAIN_KEYS


def test_the_pipeline_expressions_on_real_exit_values(lh):
    p = _pipeline()
    acts = {a["name"]: a for a in p["properties"]["activities"]}
    gate = acts["CheckGate"]["typeProperties"]
    fail = gate["ifFalseActivities"][0]["typeProperties"]
    params = {k: v["defaultValue"] for k, v in p["properties"]["parameters"].items()}

    raw_generate, _ = run_notebook(GEN_NB, lh, _generate_params())
    contract_expr = acts["ProfileAndCheck"]["typeProperties"]["parameters"]["contractPath"]["value"]
    ctx = {
        "parameters": params,
        "activities": {"GenerateDomain": {"result": {"exitValue": raw_generate}}},
    }
    contract_path = evaluate(contract_expr, ctx)
    assert contract_path == "shape/retail/contract.json"

    # the profile notebook, given exactly what the pipeline passes it
    raw_profile, _ = run_notebook(DOMAIN_NB, lh, _domain_params(contractPath=contract_path))
    ctx["activities"]["ProfileAndCheck"] = {"result": {"exitValue": raw_profile}}
    assert evaluate(gate["expression"], ctx) is True

    # break a table: the same expressions fail the gate and the message names the violation
    customer = _delta(lh, "customer")
    write_deltalake(str(lh / "Tables" / "customer"), customer.slice(0, 5), mode="overwrite")
    raw_bad, _ = run_notebook(DOMAIN_NB, lh, _domain_params(contractPath=contract_path))
    ctx["activities"]["ProfileAndCheck"] = {"result": {"exitValue": raw_bad}}
    assert evaluate(gate["expression"], ctx) is False
    message = evaluate(fail["message"], ctx)
    assert message.startswith("Shape generated data broke the contract of retail: [")
    assert "customer:row_count.min" in message


def test_bind_replaces_every_placeholder_of_the_new_pipeline(tmp_path):
    out = tmp_path / "bound"
    subprocess.run(
        [
            sys.executable, str(PIPELINES / "build_pipelines.py"), "bind", str(out),
            "--workspace-id", "11111111-1111-1111-1111-111111111111",
            "--notebook", "shape_generate=22222222-2222-2222-2222-222222222222",
            "--notebook", "shape_profile_domain=33333333-3333-3333-3333-333333333333",
            "--notebook", "shape_profile=44444444-4444-4444-4444-444444444444",
            "--notebook", "shape_profile_spark=55555555-5555-5555-5555-555555555555",
            "--function-set", "66666666-6666-6666-6666-666666666666",
        ],
        check=True, capture_output=True,
    )  # fmt: skip
    bound = json.loads(
        (out / "shape_generate_gate.DataPipeline" / "pipeline-content.json").read_text(
            encoding="utf-8"
        )
    )
    assert "<<" not in json.dumps(bound)
    ids = {a["typeProperties"].get("notebookId") for a in bound["properties"]["activities"]}
    assert "22222222-2222-2222-2222-222222222222" in ids
    assert "33333333-3333-3333-3333-333333333333" in ids


def test_the_runbook_documents_the_generation_pipeline_and_flags_what_was_not_verified():
    text = (PIPELINES.parent / "RUNBOOK.md").read_text(encoding="utf-8")
    for needle in (
        "shape_generate_gate",
        "shape_generate.ipynb",
        "shape_profile_domain.ipynb",
        "generateSample",
        "sqllocks-shape-domains",
    ):
        assert needle in text, needle
    expr = "@json(activity('GenerateDomain').output.result.exitValue).contractPath"
    assert expr in text
    assert "[VERIFY]" in text and "Owner live dry-run checklist" in text
