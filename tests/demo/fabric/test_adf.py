"""PF-04: the Azure Data Factory Batch Custom activity gate.

Schema-level tests of the factory definitions, the gate script (``batch/run_gate.py``) run
against the real Shape CLI, and the pipeline's expressions evaluated on the gate documents the
script writes. Not covered here, because it needs an Azure subscription: Batch pool behaviour,
Docker on the node, how ADF stores ``extendedProperties`` and what the Lookup returns. Those are
the live dry-run checklist in integrations/adf/RUNBOOK.md.

The required properties in the schemas below are those of the published ADF resource schema
(https://schema.management.azure.com/schemas/2018-06-01/Microsoft.DataFactory.json, retrieved
2026-10-01): Custom needs ``command``; Lookup needs ``dataset`` and ``source``; IfCondition needs
``expression``; Fail needs ``errorCode`` and ``message``; a dependency needs ``activity`` and
``dependencyConditions``; an AzureBatch linked service needs ``accountName``, ``batchUri``,
``linkedServiceName`` and ``poolName``.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path

import jsonschema
import pandas as pd
import pytest
from adf_expr import evaluate
from fabric_helpers import CONTRACT, REPO, make_orders

ADF = REPO / "integrations" / "adf"
FACTORY = ADF / "factory"
SHAPE_CLI = [sys.executable, "-m", "shape.cli.main"]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


builder = _load(ADF / "build_adf.py", "pf04_build_adf")
gate_script = _load(ADF / "batch" / "run_gate.py", "pf04_run_gate")

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
        "name": {"const": "shape_gate_batch"},
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
DATASET_SCHEMA = {
    "type": "object",
    "required": ["name", "properties"],
    "properties": {
        "properties": {
            "type": "object",
            "required": ["linkedServiceName", "type", "typeProperties", "parameters"],
            "properties": {
                "linkedServiceName": REFERENCE,
                "type": {"const": "Json"},
                "typeProperties": {
                    "type": "object",
                    "required": ["location"],
                    "properties": {
                        "location": {
                            "type": "object",
                            "required": ["type", "fileName", "folderPath", "fileSystem"],
                            "properties": {"type": {"const": "AzureBlobFSLocation"}},
                        }
                    },
                },
            },
        }
    },
}
LINKED_SERVICE_TYPES = {
    "ShapeAdls": ("AzureBlobFS", ["url"]),
    "ShapeBatchStorage": ("AzureBlobStorage", ["serviceEndpoint"]),
    "ShapeBatch": ("AzureBatch", ["accountName", "batchUri", "linkedServiceName", "poolName"]),
    "ShapeKeyVault": ("AzureKeyVault", ["baseUrl"]),
}


def load(rel: str) -> dict:
    return json.loads((FACTORY / rel).read_text(encoding="utf-8"))


def pipeline() -> dict:
    return load("pipeline/shape_gate_batch.json")


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
    assert set(files) == {
        str(p.relative_to(FACTORY)).replace("\\", "/") for p in FACTORY.rglob("*.json")
    }
    for rel, doc in files.items():
        assert load(rel) == doc, f"{rel} is stale: rerun integrations/adf/build_adf.py"


def test_pipeline_is_schema_valid_with_the_documented_activity_graph():
    p = pipeline()
    jsonschema.validate(p, PIPELINE_SCHEMA)
    custom, lookup, gate = p["properties"]["activities"]
    assert (custom["name"], lookup["name"], gate["name"]) == (
        "ProfileAndCheck",
        "ReadGate",
        "CheckGate",
    )
    assert custom["dependsOn"] == []
    # the Lookup also runs after a failed gate (the exit code fails the Custom activity)
    assert lookup["dependsOn"] == [
        {"activity": "ProfileAndCheck", "dependencyConditions": ["Completed"]}
    ]
    assert gate["dependsOn"] == [{"activity": "ReadGate", "dependencyConditions": ["Succeeded"]}]
    assert lookup["typeProperties"]["firstRowOnly"] is True
    (fail,) = gate["typeProperties"]["ifFalseActivities"]
    assert fail["name"] == "FailGate" and fail["typeProperties"]["errorCode"] == "ShapeGateFailed"
    assert custom["typeProperties"]["retentionTimeInDays"] == 1


def test_dataset_and_linked_services_are_schema_valid_and_hold_no_secrets():
    jsonschema.validate(load("dataset/ShapeGateJson.json"), DATASET_SCHEMA)
    for name, (kind, required) in LINKED_SERVICE_TYPES.items():
        doc = load(f"linkedService/{name}.json")
        assert doc["name"] == name and doc["properties"]["type"] == kind
        assert set(required) <= set(doc["properties"]["typeProperties"]), name
    text = "".join(p.read_text() for p in FACTORY.rglob("*.json"))
    assert "<<BATCH_KEY_SECRET_NAME>>" in text  # a Key Vault secret name, never a key
    assert not re.search(
        r"(?i)(accountkey|sharedkey|sas|password|secret)\s*\"\s*:\s*\"[A-Za-z0-9+/=]{20,}", text
    )
    batch = load("linkedService/ShapeBatch.json")["properties"]["typeProperties"]
    assert batch["accessKey"]["type"] == "AzureKeyVaultSecret"


def test_references_resolve_and_expressions_use_real_parameters_and_activities():
    p = pipeline()
    params, acts = set(p["properties"]["parameters"]), set(activities(p))
    for e in expressions(p):
        for ref in re.findall(r"pipeline\(\)\.parameters\.(\w+)", e):
            assert ref in params, e
        for ref in re.findall(r"activity\('(\w+)'\)", e):
            assert ref in acts, e
    refs = {
        a["linkedServiceName"]["referenceName"]
        for a in activities(p).values()
        if "linkedServiceName" in a
    } | {
        a["typeProperties"]["resourceLinkedService"]["referenceName"]
        for a in activities(p).values()
        if a["type"] == "Custom"
    }
    assert refs <= {path.stem for path in (FACTORY / "linkedService").glob("*.json")}
    dataset = activities(p)["ReadGate"]["typeProperties"]["dataset"]
    assert (FACTORY / "dataset" / f"{dataset['referenceName']}.json").exists()
    declared = set(load("dataset/ShapeGateJson.json")["properties"]["parameters"])
    assert set(dataset["parameters"]) == declared
    ds_ls = load("dataset/ShapeGateJson.json")["properties"]["linkedServiceName"]["referenceName"]
    assert ds_ls in LINKED_SERVICE_TYPES


def test_the_command_runs_the_script_and_passes_settings_only_through_activity_json():
    custom = activities(pipeline())["ProfileAndCheck"]["typeProperties"]
    command = custom["command"]["value"]
    assert "docker run" in command and "run_gate.py --activity /work/activity.json" in command
    assert "pipeline().parameters.image" in command
    # no user-supplied value is spliced into the shell command: only the image name
    assert re.findall(r"pipeline\(\)\.parameters\.(\w+)", command) == ["image"]
    # every setting the script reads is supplied, and nothing else is
    assert set(custom["extendedProperties"]) == {
        "sourceUrl",
        "contractUrl",
        "baselineUrl",
        "failOnDrift",
        "managedIdentityClientId",
        "outputUrl",
    }
    assert custom["folderPath"]["value"] == "@pipeline().parameters.scriptsFolder"
    assert (ADF / "batch" / "run_gate.py").exists()


# ----------------------------------------------------------------------------- the script


@pytest.fixture()
def data(tmp_path: Path) -> dict:
    d = tmp_path / "in"
    d.mkdir()
    for day in (1, 2):
        make_orders(day).to_parquet(d / f"orders_day{day}.parquet")
    (d / "contract.json").write_text(json.dumps(CONTRACT))
    (d / "bad_contract.json").write_text("{not json")
    return {"dir": d, "out": tmp_path / "out"}


def _settings(data: dict, day: int = 1, **kw) -> dict:
    s = {
        "sourceUrl": str(data["dir"] / f"orders_day{day}.parquet"),
        "contractUrl": str(data["dir"] / "contract.json"),
        "baselineUrl": "",
        "failOnDrift": False,
        "managedIdentityClientId": "",
        "outputUrl": str(data["out"] / "run-1"),
    }
    s.update(kw)
    return s


def _run(settings: dict) -> tuple[int, dict]:
    code = gate_script.run(settings, SHAPE_CLI)
    gate = json.loads((Path(settings["outputUrl"]) / "gate.json").read_text())
    return code, gate


def _lookup_context(gate: dict, **params) -> dict:
    base = {"sourceUrl": "src", "outputFolder": "gates/orders", "outputFileSystem": "shape"}
    return {
        "parameters": {**base, **params},
        "activities": {"ReadGate": {"firstRow": gate}},
        "run_id": "run-1",
    }


def test_a_passing_gate_exits_0_and_publishes_the_artifacts(data):
    code, gate = _run(_settings(data))
    assert code == 0 and gate["passed"] is True and gate["exitCode"] == 0
    assert gate["rowCount"] == 2000 and gate["violations"] == [] and gate["error"] is None
    out = Path(data["out"]) / "run-1"
    assert {p.name for p in out.iterdir()} == {"gate.json", "profile.shape", "summary.json"}
    assert gate["artifactUrl"] == str(out / "profile.shape")
    assert json.loads((out / "summary.json").read_text())["row_count"] == 2000


def test_a_failing_contract_exits_1_but_still_writes_the_gate_for_the_lookup(data):
    code, gate = _run(_settings(data, day=2))
    assert code == 1 and gate["passed"] is False and gate["exitCode"] == 1
    rules = {(v["column"], v["rule"]) for v in gate["violations"]}
    assert ("email", "max_null_rate") in rules and ("status", "allowed_values") in rules
    assert (Path(data["out"]) / "run-1" / "profile.shape").exists()


def test_drift_against_a_baseline_fails_only_with_fail_on_drift(data):
    base = Path(data["out"]) / "base"
    assert _run(_settings(data, 1, outputUrl=str(base)))[0] == 0
    baseline = str(base / "profile.shape")
    code, gate = _run(_settings(data, 2, contractUrl="", baselineUrl=baseline))
    assert code == 0 and gate["passed"] is True and gate["drifted"] is True and gate["changes"]
    code, gate = _run(
        _settings(
            data,
            2,
            contractUrl="",
            baselineUrl=baseline,
            failOnDrift="true",
            outputUrl=str(data["out"] / "r2"),
        )
    )
    assert code == 1 and gate["violations"][0]["rule"] == "drift"


def test_errors_exit_2_with_the_reason_in_the_gate(data):
    code, gate = _run(_settings(data, sourceUrl=str(data["dir"] / "missing.parquet")))
    assert code == 2 and gate["passed"] is False and "shape profile exited" in gate["error"]
    code, gate = _run(
        _settings(
            data, contractUrl=str(data["dir"] / "nope.json"), outputUrl=str(data["out"] / "r2")
        )
    )
    assert code == 2 and "does not exist" in gate["error"]
    code, gate = _run(
        _settings(
            data,
            contractUrl=str(data["dir"] / "bad_contract.json"),
            outputUrl=str(data["out"] / "r3"),
        )
    )
    assert code == 2 and "shape check exited" in gate["error"]


def test_the_gate_document_is_compact(data):
    wide = pd.DataFrame({f"c{i}": range(1500) for i in range(400)})
    wide.to_parquet(data["dir"] / "wide.parquet")
    (data["dir"] / "wide.json").write_text(
        json.dumps({"columns": {f"c{i}": {"max": -1} for i in range(400)}})
    )
    code, gate = _run(
        _settings(
            data,
            sourceUrl=str(data["dir"] / "wide.parquet"),
            contractUrl=str(data["dir"] / "wide.json"),
        )
    )
    assert code == 1 and len(gate["violations"]) == 100 and gate["truncated"] is True
    assert len((Path(data["out"]) / "run-1" / "gate.json").read_bytes()) < 100_000


def test_main_reads_activity_json_and_returns_the_exit_code(data, tmp_path, capsys):
    activity = {
        "name": "ProfileAndCheck",
        "typeProperties": {"extendedProperties": _settings(data, 2)},
    }
    path = tmp_path / "activity.json"
    path.write_text(json.dumps(activity))
    assert gate_script.main(["--activity", str(path), "--shape", " ".join(SHAPE_CLI)]) == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False
    path.write_text(json.dumps({"typeProperties": {}}))
    assert gate_script.main(["--activity", str(path)]) == 2
    assert gate_script.main(["--activity", str(tmp_path / "absent.json")]) == 2


def test_settings_require_a_source_and_an_output(data):
    with pytest.raises(gate_script.GateError, match="outputUrl"):
        gate_script.run(_settings(data, outputUrl=""), SHAPE_CLI)
    code, gate = _run(_settings(data, sourceUrl=""))
    assert code == 2 and "sourceUrl is required" in gate["error"]


# --------------------------------------------------------- storage: abfss through adlfs


class _FakeFs:
    """Stands in for adlfs: maps ``<container>/<path>`` onto a local directory."""

    def __init__(self, root: Path):
        self.root = root

    def _p(self, path: str) -> Path:
        return self.root / path

    def open(self, path, mode="rb"):
        return open(self._p(path), mode)

    def makedirs(self, path, exist_ok=False):
        self._p(path).mkdir(parents=True, exist_ok=exist_ok)


@pytest.fixture()
def fake_azure(monkeypatch, tmp_path):
    seen: dict = {"credentials": [], "filesystems": []}
    identity = types.ModuleType("azure.identity")

    class DefaultAzureCredential:
        def __init__(self, **kw):
            seen["credentials"].append(kw)

    identity.DefaultAzureCredential = DefaultAzureCredential
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    import fsspec

    real = fsspec.filesystem
    store = tmp_path / "adls"
    store.mkdir()

    def filesystem(protocol, **kw):
        if protocol != "abfss":
            return real(protocol, **kw)
        seen["filesystems"].append(kw)
        return _FakeFs(store)

    monkeypatch.setattr(fsspec, "filesystem", filesystem)
    return seen, store


def test_abfss_urls_use_the_managed_identity_through_adlfs(fake_azure, data):
    seen, store = fake_azure
    (store / "shape" / "contracts").mkdir(parents=True)
    (store / "shape" / "contracts" / "orders.json").write_text(json.dumps(CONTRACT))
    base = "abfss://shape@acct.dfs.core.windows.net"
    code, gate = _run_abfss(data, base, "11111111-2222-3333-4444-555555555555")
    assert code == 0 and gate["passed"] is True
    assert gate["artifactUrl"] == f"{base}/gates/orders/run-9/profile.shape"
    assert {p.name for p in (store / "shape" / "gates" / "orders" / "run-9").iterdir()} == {
        "gate.json",
        "profile.shape",
        "summary.json",
    }
    assert seen["credentials"] and all(
        c == {"managed_identity_client_id": "11111111-2222-3333-4444-555555555555"}
        for c in seen["credentials"]
    )
    assert all(f["account_name"] == "acct" for f in seen["filesystems"])


def _run_abfss(data, base: str, client_id: str):
    settings = _settings(
        data,
        contractUrl=f"{base}/contracts/orders.json",
        outputUrl=f"{base}/gates/orders/run-9",
        managedIdentityClientId=client_id,
    )
    code = gate_script.run(settings, SHAPE_CLI)
    out = gate_script._filesystem(settings["outputUrl"], client_id)
    gate = json.loads(out[0].open(out[1] + "/gate.json").read())
    return code, gate


def test_the_default_credential_is_used_when_no_identity_is_named(fake_azure):
    seen, _ = fake_azure
    gate_script._filesystem("abfss://c@acct.dfs.core.windows.net/x.json", "")
    assert seen["credentials"] == [{}]


@pytest.mark.parametrize("bad", ["abfss://acct.dfs.core.windows.net/x", "abfss:///x"])
def test_malformed_abfss_urls_are_refused(fake_azure, bad):
    with pytest.raises(gate_script.GateError, match="abfss://<container>@<account>"):
        gate_script._filesystem(bad, "")


# --------------------------------------------------- the pipeline's expressions, evaluated


def test_branch_expressions_on_real_gate_documents(data):
    p = pipeline()
    gate_activity = activities(p)["CheckGate"]["typeProperties"]
    fail = activities(p)["FailGate"]["typeProperties"]
    for day, expect in ((1, True), (2, False)):
        _, gate = _run(_settings(data, day, outputUrl=str(data["out"] / f"d{day}")))
        ctx = _lookup_context(gate, sourceUrl=f"orders_day{day}")
        assert evaluate(gate_activity["expression"], ctx) is expect
        if not expect:
            message = evaluate(fail["message"], ctx)
            assert message.startswith("Shape gate failed for orders_day2: [")
            assert "allowed_values" in message and "max_null_rate" in message


def test_the_fail_message_reports_an_error_gate_instead_of_an_empty_violation_list(data):
    _, gate = _run(_settings(data, sourceUrl=str(data["dir"] / "missing.parquet")))
    ctx = _lookup_context(gate, sourceUrl="missing")
    assert (
        evaluate(activities(pipeline())["CheckGate"]["typeProperties"]["expression"], ctx) is False
    )
    message = evaluate(activities(pipeline())["FailGate"]["typeProperties"]["message"], ctx)
    assert message.startswith("Shape gate failed for missing: shape profile exited")


def test_output_url_and_dataset_location_point_at_the_same_gate_file():
    custom = activities(pipeline())["ProfileAndCheck"]["typeProperties"]
    lookup = activities(pipeline())["ReadGate"]["typeProperties"]["dataset"]["parameters"]
    params = {
        "storageAccount": "acct",
        "outputFileSystem": "shape",
        "outputFolder": "gates/orders",
        "sourceUrl": "s",
    }
    ctx = {"parameters": params, "activities": {}, "run_id": "r-42"}
    url = evaluate(custom["extendedProperties"]["outputUrl"], ctx)
    assert url == "abfss://shape@acct.dfs.core.windows.net/gates/orders/r-42"
    ds_ctx = {"parameters": params, "activities": {}, "run_id": "r-42"}
    file_system = evaluate(lookup["fileSystem"], ds_ctx)
    folder = evaluate(lookup["folderPath"], ds_ctx)
    location = load("dataset/ShapeGateJson.json")["properties"]["typeProperties"]["location"]
    assert location["fileName"] == "gate.json"
    assert url == f"abfss://{file_system}@acct.dfs.core.windows.net/{folder}"


def test_runbook_has_the_dry_run_checklist_and_flags_what_was_not_verified():
    text = (ADF / "RUNBOOK.md").read_text(encoding="utf-8")
    assert "## 7. Live dry-run checklist" in text and "- [ ]" in text
    assert "[VERIFY]" in text and "not run in a Data Factory" in text
    for needle in ("shape_gate_batch", "run_gate.py", "ShapeGateJson", "exit code", "Completed"):
        assert needle in text
    for name in LINKED_SERVICE_TYPES:
        assert (FACTORY / "linkedService" / f"{name}.json").exists()
    index = (ADF.parent / "README.md").read_text(encoding="utf-8")
    for runbook in ("fabric/RUNBOOK.md", "synapse/RUNBOOK.md", "adf/RUNBOOK.md"):
        assert runbook in index and (ADF.parent / runbook).exists()
