"""PF-06: the Azure Data Factory generate-then-check gate (``batch/run_generate_gate.py``).

Schema-level tests of the new pipeline, the script run against the real Shape CLI (generate,
profile, check) with local storage, and the pipeline's expressions evaluated on the gate documents
the script writes. Not covered here, because it needs an Azure subscription: Batch pool behaviour,
Docker on the node, how ADF stores ``extendedProperties`` and what the Lookup returns. Those are the
live dry-run checklist in integrations/adf/RUNBOOK.md.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import textwrap
from pathlib import Path

import jsonschema
import pyarrow.parquet as pq
import pytest
from adf_expr import evaluate
from fabric_helpers import REPO

import shape

ADF = REPO / "integrations" / "adf"
FACTORY = ADF / "factory"
SHAPE_CLI = [sys.executable, "-m", "shape.cli.main"]
RETAIL_ROWS = {
    "customer": 1000,
    "address": 1500,
    "product_category": 50,
    "product": 500,
    "store": 150,
    "promotion": 200,
    "order": 5000,
    "order_line": 12500,
    "return": 850,
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


builder = _load(ADF / "build_adf.py", "pf06_build_adf")
script = _load(ADF / "batch" / "run_generate_gate.py", "pf06_run_generate_gate")

EXPRESSION = {
    "type": "object",
    "required": ["value", "type"],
    "properties": {"value": {"type": "string", "pattern": "^@"}, "type": {"const": "Expression"}},
}
DEPENDENCY = {
    "type": "object",
    "required": ["activity", "dependencyConditions"],
    "properties": {
        "activity": {"type": "string"},
        "dependencyConditions": {
            "type": "array",
            "minItems": 1,
            "items": {"enum": ["Succeeded", "Failed", "Skipped", "Completed"]},
        },
    },
}
REFERENCE = {
    "type": "object",
    "required": ["referenceName", "type"],
    "properties": {"referenceName": {"type": "string"}, "type": {"type": "string"}},
}


def _activity(kind: str, type_properties: dict, extra_required=()) -> dict:
    return {
        "type": "object",
        "required": ["name", "type", "dependsOn", "typeProperties", *extra_required],
        "properties": {
            "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]*$"},
            "type": {"const": kind},
            "dependsOn": {"type": "array", "items": DEPENDENCY},
            "typeProperties": type_properties,
        },
    }


FAIL = _activity(
    "Fail",
    {
        "type": "object",
        "required": ["message", "errorCode"],
        "properties": {"message": EXPRESSION, "errorCode": {"type": "string"}},
    },
)
CUSTOM = _activity(
    "Custom",
    {
        "type": "object",
        "required": ["command", "resourceLinkedService", "folderPath", "extendedProperties"],
        "properties": {
            "command": EXPRESSION,
            "resourceLinkedService": REFERENCE,
            "folderPath": EXPRESSION,
            "extendedProperties": {"type": "object", "additionalProperties": EXPRESSION},
        },
    },
    extra_required=["linkedServiceName"],
)
LOOKUP = _activity(
    "Lookup",
    {
        "type": "object",
        "required": ["source", "dataset"],
        "properties": {
            "source": {
                "type": "object",
                "required": ["type"],
                "properties": {"type": {"const": "JsonSource"}},
            },
            "dataset": {
                "allOf": [REFERENCE],
                "properties": {"type": {"const": "DatasetReference"}},
            },
            "firstRowOnly": {"type": "boolean"},
        },
    },
)
IF = _activity(
    "IfCondition",
    {
        "type": "object",
        "required": ["expression", "ifTrueActivities", "ifFalseActivities"],
        "properties": {
            "expression": EXPRESSION,
            "ifTrueActivities": {"type": "array"},
            "ifFalseActivities": {"type": "array", "items": FAIL},
        },
    },
)
PIPELINE_SCHEMA = {
    "type": "object",
    "required": ["name", "properties"],
    "properties": {
        "name": {"const": "shape_generate_gate_batch"},
        "properties": {
            "type": "object",
            "required": ["activities", "parameters"],
            "properties": {
                "activities": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "prefixItems": [CUSTOM, LOOKUP, IF],
                },
                "parameters": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "required": ["type", "defaultValue"],
                        "properties": {"type": {"enum": ["string", "bool", "int", "object"]}},
                    },
                },
            },
        },
    },
}


def load(rel: str) -> dict:
    return json.loads((FACTORY / rel).read_text(encoding="utf-8"))


def pipeline() -> dict:
    return load("pipeline/shape_generate_gate_batch.json")


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


# ---------------------------------------------------------------------------- definitions


def test_committed_definitions_match_the_generator():
    files = builder.build()
    assert "pipeline/shape_generate_gate_batch.json" in files
    for rel, doc in files.items():
        assert load(rel) == doc, f"{rel} is stale: rerun integrations/adf/build_adf.py"


def test_pipeline_is_schema_valid_with_the_documented_activity_graph():
    p = pipeline()
    jsonschema.validate(p, PIPELINE_SCHEMA)
    custom, lookup, gate = p["properties"]["activities"]
    assert (custom["name"], lookup["name"], gate["name"]) == (
        "GenerateAndCheck",
        "ReadGate",
        "CheckGate",
    )
    assert custom["dependsOn"] == []
    # the Lookup also runs after a failed gate (the exit code fails the Custom activity)
    assert lookup["dependsOn"] == [
        {"activity": "GenerateAndCheck", "dependencyConditions": ["Completed"]}
    ]
    assert gate["dependsOn"] == [{"activity": "ReadGate", "dependencyConditions": ["Succeeded"]}]
    (fail,) = gate["typeProperties"]["ifFalseActivities"]
    assert fail["name"] == "FailGate"
    assert fail["typeProperties"]["errorCode"] == "ShapeContractFailed"


def test_the_existing_gate_pipeline_is_unchanged_by_the_shared_activity_builders():
    original = load("pipeline/shape_gate_batch.json")
    assert [a["name"] for a in original["properties"]["activities"]] == [
        "ProfileAndCheck",
        "ReadGate",
        "CheckGate",
    ]
    assert (
        original["properties"]["activities"][2]["typeProperties"]["ifFalseActivities"][0][
            "typeProperties"
        ]["errorCode"]
        == "ShapeGateFailed"
    )


def test_references_resolve_and_expressions_use_real_parameters_and_activities():
    p = pipeline()
    params, acts = set(p["properties"]["parameters"]), set(activities(p))
    for e in expressions(p):
        for ref in re.findall(r"pipeline\(\)\.parameters\.(\w+)", e):
            assert ref in params, e
        for ref in re.findall(r"activity\('(\w+)'\)", e):
            assert ref in acts, e
    custom = activities(p)["GenerateAndCheck"]
    refs = {
        custom["linkedServiceName"]["referenceName"],
        custom["typeProperties"]["resourceLinkedService"]["referenceName"],
    }
    assert refs <= {path.stem for path in (FACTORY / "linkedService").glob("*.json")}
    dataset = activities(p)["ReadGate"]["typeProperties"]["dataset"]
    assert (FACTORY / "dataset" / f"{dataset['referenceName']}.json").exists()
    assert set(dataset["parameters"]) == set(
        load("dataset/ShapeGateJson.json")["properties"]["parameters"]
    )
    # every pipeline parameter is used
    used = set(re.findall(r"pipeline\(\)\.parameters\.(\w+)", json.dumps(p)))
    assert used == params


def test_the_command_runs_the_script_and_passes_settings_only_through_activity_json():
    custom = activities(pipeline())["GenerateAndCheck"]["typeProperties"]
    command = custom["command"]["value"]
    assert "docker run" in command
    assert "run_generate_gate.py --activity /work/activity.json" in command
    # no user-supplied value is spliced into the shell command: only the image name
    assert re.findall(r"pipeline\(\)\.parameters\.(\w+)", command) == ["image"]
    # every setting the script reads is supplied, and nothing else is
    assert set(custom["extendedProperties"]) == {
        "domain",
        "scale",
        "seed",
        "mode",
        "baselineUrl",
        "failOnDrift",
        "managedIdentityClientId",
        "outputUrl",
    }
    assert custom["folderPath"]["value"] == "@pipeline().parameters.scriptsFolder"
    assert (ADF / "batch" / "run_generate_gate.py").exists()
    assert (ADF / "batch" / "run_gate.py").exists()  # shipped with it: the script imports it


def test_the_script_reads_only_the_settings_the_pipeline_supplies():
    source = (ADF / "batch" / "run_generate_gate.py").read_text(encoding="utf-8")
    read = set(re.findall(r'settings\.get\("(\w+)"\)', source)) | set(
        re.findall(r'_setting\(settings, "(\w+)"', source)
    )
    supplied = set(
        activities(pipeline())["GenerateAndCheck"]["typeProperties"]["extendedProperties"]
    )
    assert read - {"maxRows"} <= supplied  # maxRows keeps its default: not a pipeline parameter
    assert {"domain", "outputUrl"} <= read


# ----------------------------------------------------------------------------- the script


@pytest.fixture()
def out(tmp_path: Path) -> Path:
    return tmp_path / "out"


def _settings(out: Path, name: str = "run-1", **kw) -> dict:
    s = {
        "domain": "retail",
        "scale": "small",
        "seed": 42,
        "mode": "",
        "baselineUrl": "",
        "failOnDrift": False,
        "managedIdentityClientId": "",
        "outputUrl": str(out / name),
    }
    s.update(kw)
    return s


def _run(settings: dict, cli=None) -> tuple[int, dict]:
    code = script.run(settings, cli or SHAPE_CLI)
    gate = json.loads((Path(settings["outputUrl"]) / "gate.json").read_text())
    return code, gate


def _lookup_context(gate: dict, **params) -> dict:
    base = {"domain": "retail", "outputFolder": "generated/retail", "outputFileSystem": "shape"}
    return {
        "parameters": {**base, **params},
        "activities": {"ReadGate": {"firstRow": gate}},
        "run_id": "run-1",
    }


def test_a_passing_gate_exits_0_and_publishes_the_data_the_contract_and_the_artifacts(out):
    code, gate = _run(_settings(out))
    assert code == 0 and gate["passed"] is True and gate["exitCode"] == 0
    assert gate["domain"] == "retail" and gate["tables"] == RETAIL_ROWS
    assert gate["rowCount"] == 21750 and gate["violations"] == [] and gate["error"] is None
    run = out / "run-1"
    assert {p.name for p in run.iterdir()} == {
        "data",
        "contract.json",
        "gate.json",
        "profile.shape",
        "summary.json",
    }
    assert {p.stem for p in (run / "data").glob("*.parquet")} == set(RETAIL_ROWS)
    assert gate["artifactUrl"] == str(run / "profile.shape")
    assert gate["contractUrl"] == str(run / "contract.json")
    contract = json.loads((run / "contract.json").read_text())
    assert set(contract["tables"]) == set(RETAIL_ROWS)
    assert contract["tables"]["customer"]["row_count"] == {"min": 1000, "max": 1000}
    assert set(shape.load(str(run / "profile.shape")).tables) == set(RETAIL_ROWS)


def test_the_published_tables_are_the_engine_output_for_the_seed(out):
    _run(_settings(out, seed=42))
    expected = shape.generate("retail", scale="small", seed=42)
    for name in RETAIL_ROWS:
        written = pq.read_table(out / "run-1" / "data" / f"{name}.parquet")
        assert written.num_rows == expected[name].num_rows
        assert written.column_names == expected[name].column_names
    assert (
        pq.read_table(out / "run-1" / "data" / "customer.parquet")
        .to_pandas()
        .equals(expected["customer"].to_pandas())
    )


def test_the_mode_and_scale_settings_are_honoured_and_blank_ones_default(out):
    code, gate = _run(_settings(out, scale="", seed="", mode="star"))
    assert code == 0 and gate["tables"] == RETAIL_ROWS  # blank scale and seed: small and 42
    code, gate = _run(_settings(out, "r2", scale="fabric_demo", seed=7))
    assert code == 0 and gate["tables"]["customer"] != 1000


WRAPPER = textwrap.dedent(
    """
    import subprocess, sys
    from pathlib import Path

    proc = subprocess.run([sys.executable, "-m", "shape.cli.main", *sys.argv[1:]])
    if proc.returncode == 0 and sys.argv[1] == "generate":
        out = Path(sys.argv[sys.argv.index("-o") + 1])
        import pyarrow.parquet as pq
        customer = pq.read_table(out / "customer.parquet")
        pq.write_table(customer.slice(0, 900), out / "customer.parquet")
    sys.exit(proc.returncode)
    """
)


def test_data_that_breaks_the_contract_exits_1_but_still_writes_the_gate_for_the_lookup(
    out, tmp_path
):
    wrapper = tmp_path / "damage.py"
    wrapper.write_text(WRAPPER)
    code, gate = _run(_settings(out), [sys.executable, str(wrapper)])
    assert code == 1 and gate["passed"] is False and gate["exitCode"] == 1
    assert gate["error"] is None
    assert "customer:row_count.min" in {v["rule"] for v in gate["violations"]}
    assert gate["tables"]["customer"] == 900
    assert (out / "run-1" / "profile.shape").exists()


def test_drift_against_a_baseline_fails_only_with_fail_on_drift(out):
    assert _run(_settings(out, "base"))[0] == 0
    baseline = str(out / "base" / "profile.shape")
    code, gate = _run(_settings(out, "r1", seed=99, baselineUrl=baseline))
    assert code == 0 and gate["passed"] is True and gate["drifted"] is True and gate["changes"]
    code, gate = _run(_settings(out, "r2", seed=99, baselineUrl=baseline, failOnDrift="true"))
    assert code == 1 and gate["violations"][-1]["rule"] == "drift"
    code, gate = _run(_settings(out, "r3", seed=42, baselineUrl=baseline, failOnDrift="true"))
    assert code == 0 and gate["drifted"] is False


@pytest.mark.parametrize(
    "bad, message",
    [
        ({"domain": "nope"}, "no domain named 'nope'"),
        ({"domain": "../etc"}, "domain must be"),
        ({"domain": "retail; rm -rf /"}, "domain must be"),
        ({"domain": ""}, "domain must be"),
        ({"scale": "huge"}, "no scale preset 'huge'"),
        ({"scale": "../x"}, "scale must be"),
        ({"mode": "snowflake"}, "mode must be"),
        ({"seed": "abc"}, "invalid literal"),
        ({"maxRows": 100}, "above maxRows=100"),
        ({"baselineUrl": "missing.shape"}, "does not exist"),
    ],
)
def test_errors_exit_2_with_the_reason_in_the_gate_and_nothing_is_generated(out, bad, message):
    code, gate = _run(_settings(out, **bad))
    assert code == 2 and gate["passed"] is False and gate["exitCode"] == 2
    assert message in gate["error"]
    assert gate["violations"] == [] and gate["tables"] == {}
    assert {p.name for p in (out / "run-1").iterdir()} == {"gate.json"}


def test_a_failing_command_exits_2_with_its_stderr_tail(out, tmp_path):
    failing = tmp_path / "fail.py"
    failing.write_text("import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n")
    code, gate = _run(_settings(out), [sys.executable, str(failing)])
    assert code == 2 and "shape generate exited 3" in gate["error"] and "boom" in gate["error"]


def test_main_reads_activity_json_and_returns_the_exit_code(out, tmp_path, capsys):
    path = tmp_path / "activity.json"
    path.write_text(
        json.dumps(
            {"name": "GenerateAndCheck", "typeProperties": {"extendedProperties": _settings(out)}}
        )
    )
    assert script.main(["--activity", str(path), "--shape", " ".join(SHAPE_CLI)]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    path.write_text(
        json.dumps({"typeProperties": {"extendedProperties": _settings(out, "r2", domain="nope")}})
    )
    assert script.main(["--activity", str(path), "--shape", " ".join(SHAPE_CLI)]) == 2
    path.write_text(json.dumps({"typeProperties": {}}))
    assert script.main(["--activity", str(path)]) == 2
    assert script.main(["--activity", str(tmp_path / "absent.json")]) == 2


def test_the_output_url_is_required():
    with pytest.raises(script.GateError, match="outputUrl"):
        script.run({"domain": "retail", "outputUrl": ""}, SHAPE_CLI)


def test_the_gate_document_is_compact(out):
    _, gate = _run(_settings(out))
    assert len((out / "run-1" / "gate.json").read_bytes()) < 100_000
    assert set(gate) >= {"passed", "exitCode", "rowCount", "violations", "drifted", "changes"}


# --------------------------------------------------- the pipeline's expressions, evaluated


def test_branch_expressions_on_real_gate_documents(out, tmp_path):
    p = pipeline()
    gate_activity = activities(p)["CheckGate"]["typeProperties"]
    fail = activities(p)["FailGate"]["typeProperties"]

    _, ok = _run(_settings(out, "ok"))
    assert evaluate(gate_activity["expression"], _lookup_context(ok)) is True

    wrapper = tmp_path / "damage.py"
    wrapper.write_text(WRAPPER)
    _, bad = _run(_settings(out, "bad"), [sys.executable, str(wrapper)])
    ctx = _lookup_context(bad)
    assert evaluate(gate_activity["expression"], ctx) is False
    message = evaluate(fail["message"], ctx)
    assert message.startswith("Shape generated data broke the contract of retail: [")
    assert "customer:row_count.min" in message

    _, err = _run(_settings(out, "err", domain="nope"))
    ctx = _lookup_context(err)
    assert evaluate(gate_activity["expression"], ctx) is False
    assert evaluate(fail["message"], ctx).startswith(
        "Shape generated data broke the contract of retail: no domain named 'nope'"
    )


def test_output_url_and_dataset_location_point_at_the_same_gate_file():
    custom = activities(pipeline())["GenerateAndCheck"]["typeProperties"]
    lookup = activities(pipeline())["ReadGate"]["typeProperties"]["dataset"]["parameters"]
    params = {
        "storageAccount": "acct",
        "outputFileSystem": "shape",
        "outputFolder": "generated/retail",
        "domain": "retail",
    }
    ctx = {"parameters": params, "activities": {}, "run_id": "r-42"}
    url = evaluate(custom["extendedProperties"]["outputUrl"], ctx)
    assert url == "abfss://shape@acct.dfs.core.windows.net/generated/retail/r-42"
    file_system = evaluate(lookup["fileSystem"], ctx)
    folder = evaluate(lookup["folderPath"], ctx)
    assert url == f"abfss://{file_system}@acct.dfs.core.windows.net/{folder}"


def test_runbook_documents_the_generation_pipeline_and_flags_what_was_not_verified():
    text = (ADF / "RUNBOOK.md").read_text(encoding="utf-8")
    for needle in (
        "shape_generate_gate_batch",
        "run_generate_gate.py",
        "GenerateAndCheck",
        "maxRows",
        "exit code",
    ):
        assert needle in text, needle
    assert "[VERIFY]" in text and "- [ ]" in text and "not run in a Data Factory" in text
