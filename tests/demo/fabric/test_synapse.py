"""PF-04: the Azure Synapse Spark notebook and gate pipeline.

The notebook's code cells run against a local Spark session, with ``mssparkutils`` replaced by a
stub that maps ``abfss://`` URLs onto a temporary directory. The pipeline definition is checked at
schema level and its expressions are evaluated on the notebook's real exit values. Not covered,
because it needs a Synapse workspace: the notebook-activity exit-value path, its parameter
encoding, ``mssparkutils`` behaviour and linked-service access. Those are the live dry-run
checklist in integrations/synapse/RUNBOOK.md.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

import jsonschema
import nbformat
import pytest
from adf_expr import evaluate
from fabric_helpers import CONTRACT, REPO, NotebookExit, run_notebook

SYNAPSE = REPO / "integrations" / "synapse"
NOTEBOOK = SYNAPSE / "notebooks" / "shape_profile_synapse.ipynb"
PIPELINE = SYNAPSE / "pipelines" / "shape_gate_synapse.json"
ACCOUNT_URL = "abfss://shape@acct.dfs.core.windows.net"

spec = importlib.util.spec_from_file_location("pf04_build_synapse", SYNAPSE / "build_synapse.py")
builder = importlib.util.module_from_spec(spec)
sys.modules["pf04_build_synapse"] = builder
spec.loader.exec_module(builder)

EXIT_KEYS = {
    "table",
    "rows",
    "passed",
    "violations",
    "drifted",
    "changes",
    "artifactPath",
    "sampled",
    "truncated",
    "kernel",
    "mode",
    "checked",
}


# --------------------------------------------------------------------------------- stub


class FakeFs:
    """``mssparkutils.fs`` over a directory: ``abfss://shape@acct.dfs.core.windows.net/x`` is
    ``<root>/shape/x``, and ``file:/p`` is the local path ``/p``."""

    def __init__(self, root: Path):
        self.root = root
        self.calls: list[tuple] = []

    def _path(self, url: str) -> Path:
        if url.startswith("file:"):
            return Path(url[len("file:") :])
        m = re.fullmatch(r"abfss://([^@]+)@[^/]+/(.*)", url)
        assert m, f"not an abfss URL: {url}"
        return self.root / m.group(1) / m.group(2)

    def exists(self, url):
        return self._path(url).exists()

    def head(self, url, max_bytes=65536):
        self.calls.append(("head", url))
        return self._path(url).read_text(encoding="utf-8")[:max_bytes]

    def cp(self, src, dest, recurse=False):
        self.calls.append(("cp", src, dest))
        target = self._path(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self._path(src).read_bytes())
        return True

    def put(self, url, content, overwrite=False):
        self.calls.append(("put", url))
        target = self._path(url)
        if target.exists() and not overwrite:
            raise FileExistsError(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return True


class FakeMssparkutils:
    def __init__(self, root: Path):
        self.fs = FakeFs(root)

        def _exit(value=""):
            raise NotebookExit(value)

        self.notebook = type("N", (), {"exit": staticmethod(_exit)})


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    delta = pytest.importorskip("delta", reason="delta-spark is a test requirement")
    from pyspark.sql import SparkSession

    env = pytest.MonkeyPatch()  # restored at teardown
    env.setenv("PYSPARK_PYTHON", sys.executable)
    builder_ = (
        SparkSession.builder.master("local[2]")
        .appName("shape-synapse-tests")
        .config("spark.sql.warehouse.dir", str(tmp_path_factory.mktemp("warehouse")))
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog"
        )
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
    )
    session = delta.configure_spark_with_delta_pip(builder_).getOrCreate()
    yield session
    session.stop()
    env.undo()


@pytest.fixture()
def adls(lakehouse, tmp_path):
    """The stub 'storage account': a contract and a day-1 baseline, plus the output container."""
    root = tmp_path / "adls"
    (root / "shape" / "contracts").mkdir(parents=True)
    (root / "shape" / "contracts" / "orders.json").write_text(json.dumps(CONTRACT))
    (root / "shape" / "baselines").mkdir()
    (root / "shape" / "baselines" / "orders_day1.shape").write_bytes(
        (lakehouse / "Files" / "baselines" / "orders_day1.shape").read_bytes()
    )
    return root


def _params(lakehouse: Path, day: int, **kw):
    p = {
        "sourcePath": str(lakehouse / "Tables" / f"orders_day{day}"),
        "sourceFormat": "delta",
        "tableName": "",
        "contractPath": f"{ACCOUNT_URL}/contracts/orders.json",
        "baselinePath": "",
        "outputPath": f"{ACCOUNT_URL}/shape",
        "failOnDrift": False,
        "mode": "exact",
        "partitions": 0,
        "linkedServiceName": "",
    }
    p.update(kw)
    return p


def _run(spark, adls, params, **extra):
    utils = FakeMssparkutils(adls)
    raw, ns = run_notebook(
        NOTEBOOK,
        Path("/unused"),
        params,
        {"spark": spark, "mssparkutils": utils, "displayHTML": lambda html: None, **extra},
    )
    return raw, ns, utils


def _artifacts(adls: Path, out: dict) -> Path:
    url = out["artifactPath"]
    return adls / "shape" / url.split("dfs.core.windows.net/", 1)[1]


# ------------------------------------------------------------------------------ notebook


def _normal(nb: dict) -> list:
    out = []
    for c in nb["cells"]:
        src = "".join(c["source"])
        if c["cell_type"] == "markdown":
            out.append(("markdown", src.strip()))
        else:
            out.append(("code", ast.dump(ast.parse(src)), tuple(c["metadata"].get("tags", []))))
    return out


def test_generated_files_are_current():
    for rel, doc in builder.build().items():
        committed = json.loads((SYNAPSE / rel).read_text(encoding="utf-8"))
        if rel.endswith(".ipynb"):
            assert _normal(committed) == _normal(doc), f"{rel} is stale: rerun build_synapse.py"
            assert committed["metadata"] == doc["metadata"]
        else:
            assert committed == doc, f"{rel} is stale: rerun build_synapse.py"


def test_notebook_is_valid_nbformat_with_the_synapse_kernel():
    nb = nbformat.read(NOTEBOOK, as_version=4)
    nbformat.validate(nb)
    assert nb.metadata["kernelspec"]["name"] == "synapse_pyspark"


def test_parameters_cell_and_exit_placement():
    nb = nbformat.read(NOTEBOOK, as_version=4)
    code = [c for c in nb.cells if c.cell_type == "code"]
    (tagged,) = [c for c in code if "parameters" in c.metadata.get("tags", [])]
    names = {t.id for t in ast.walk(ast.parse(tagged.source)) if isinstance(t, ast.Name)}
    assert set(builder.PARAMETER_TYPES) <= names
    calls = 0
    for c in code:
        tree = ast.parse(c.source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and ast.unparse(node.func) == "mssparkutils.notebook.exit"
            ):
                calls += 1
                assert c is code[-1]
                assert node in [getattr(s, "value", None) for s in tree.body]
    assert calls == 1
    assert not any(isinstance(n, ast.Try) for n in ast.walk(ast.parse(code[-1].source)))


def test_day1_passes_through_the_adls_stub(spark, lakehouse, adls):
    raw, _, utils = _run(spark, adls, _params(lakehouse, 1))
    out = json.loads(raw)
    assert EXIT_KEYS <= set(out) and len(raw.encode()) < 1_000_000
    assert out["passed"] is True and out["violations"] == [] and out["rows"] == 2000
    assert out["mode"] == "exact" and out["checked"] is True and out["sampled"] is False
    assert out["table"] == "orders_day1" and out["kernel"] in ("rust", "python")
    shape_file = _artifacts(adls, out)
    assert shape_file.suffix == ".shape" and shape_file.exists()
    assert shape_file.with_suffix(".html").exists()
    assert json.loads(shape_file.with_suffix(".summary.json").read_text())["row_count"] == 2000
    # storage went through mssparkutils.fs only: the contract was read, the artifacts copied
    kinds = {c[0] for c in utils.fs.calls}
    assert {"head", "cp", "put"} <= kinds


def test_two_runs_in_the_same_instant_keep_both_artifacts(spark, lakehouse, adls, monkeypatch):
    """PF-06b: the folder was named by the second, so a second run overwrote the first one's
    baseline. Here every run sees the same clock reading."""
    from datetime import UTC, datetime

    from shape.integrations.fabric import run_folder

    frozen = run_folder.run_stamp(datetime(2026, 9, 30, 12, 0, 0, 5, tzinfo=UTC))
    monkeypatch.setattr(run_folder, "run_stamp", lambda now=None: frozen)
    first = _artifacts(adls, json.loads(_run(spark, adls, _params(lakehouse, 1))[0]))
    before = first.read_bytes()
    second = _artifacts(adls, json.loads(_run(spark, adls, _params(lakehouse, 1))[0]))
    assert first != second and first.parent.parent == second.parent.parent
    assert first.parent.name == frozen and second.parent.name == f"{frozen}_2"
    assert first.read_bytes() == before


def test_day2_fails_with_violations_and_drift_against_the_baseline(spark, lakehouse, adls):
    params = _params(lakehouse, 2, baselinePath=f"{ACCOUNT_URL}/baselines/orders_day1.shape")
    raw, _, _ = _run(spark, adls, params)
    out = json.loads(raw)
    assert out["passed"] is False and out["drifted"] is True
    rules = {(v["column"], v["rule"]) for v in out["violations"]}
    assert ("email", "max_null_rate") in rules and ("status", "allowed_values") in rules
    assert {c["column"] for c in out["changes"]} >= {"email", "status", "amount"}


def test_fail_on_drift_from_a_pipeline_string(spark, lakehouse, adls):
    base = f"{ACCOUNT_URL}/baselines/orders_day1.shape"
    on = json.loads(
        _run(
            spark,
            adls,
            _params(lakehouse, 2, contractPath="", baselinePath=base, failOnDrift="True"),
        )[0]
    )
    off = json.loads(
        _run(
            spark,
            adls,
            _params(lakehouse, 2, contractPath="", baselinePath=base, failOnDrift="false"),
        )[0]
    )
    assert on["passed"] is False and on["violations"][0]["rule"] == "drift"
    assert off["passed"] is True and off["drifted"] is True


def test_same_exit_value_as_the_fabric_spark_notebook(spark, lakehouse, adls):
    from fabric_helpers import NOTEBOOKS

    syn = json.loads(_run(spark, adls, _params(lakehouse, 2, contractPath=""))[0])
    spark.sql("DROP TABLE IF EXISTS orders_day2")
    spark.sql(
        f"CREATE TABLE orders_day2 USING DELTA LOCATION '{lakehouse / 'Tables' / 'orders_day2'}'"
    )
    fab_raw, _ = run_notebook(
        NOTEBOOKS / "shape_profile_spark.ipynb",
        lakehouse,
        {
            "tableName": "orders_day2",
            "contractPath": "",
            "baselinePath": "",
            "outputDir": "shape",
            "failOnDrift": False,
        },
        {"spark": spark},
    )
    fab = json.loads(fab_raw)
    for out in (syn, fab):
        for key in ("artifactPath", "mode", "checked", "table"):
            out.pop(key, None)
    assert syn == fab


def test_distributed_mode_is_bounded_and_unchecked(spark, lakehouse, adls):
    raw, _, _ = _run(
        spark, adls, _params(lakehouse, 1, contractPath="", mode="distributed", partitions=3)
    )
    out = json.loads(raw)
    assert out["mode"] == "bounded" and out["checked"] is False and out["rows"] == 2000
    profile = json.loads(_artifacts(adls, out).read_text())
    assert profile["mode"] == "bounded" and profile["tables"]["orders_day1"]["rows"] == 2000
    summary = json.loads(_artifacts(adls, out).with_name("orders_day1.summary.json").read_text())
    assert summary["columns"]["customer_id"]["max"] == 2000


def test_distributed_mode_refuses_a_contract_and_a_bad_mode(spark, lakehouse, adls):
    with pytest.raises(ValueError, match="mode = 'exact'"):
        _run(spark, adls, _params(lakehouse, 1, mode="distributed"))
    with pytest.raises(ValueError, match="mode must be"):
        _run(spark, adls, _params(lakehouse, 1, mode="fast"))
    with pytest.raises(ValueError, match="sourcePath or tableName"):
        _run(spark, adls, _params(lakehouse, 1, sourcePath="", contractPath=""))


def test_a_catalog_table_can_be_profiled_by_name(spark, lakehouse, adls):
    spark.sql("DROP TABLE IF EXISTS orders_day1")
    spark.sql(
        f"CREATE TABLE orders_day1 USING DELTA LOCATION '{lakehouse / 'Tables' / 'orders_day1'}'"
    )
    raw, _, _ = _run(spark, adls, _params(lakehouse, 1, sourcePath="", tableName="orders_day1"))
    out = json.loads(raw)
    assert out["table"] == "orders_day1" and out["passed"] is True and out["rows"] == 2000


def test_linked_service_name_configures_the_spark_token_provider(spark, lakehouse, adls):
    _run(spark, adls, _params(lakehouse, 1, contractPath="", linkedServiceName="shape_adls_ls"))
    assert spark.conf.get("spark.storage.synapse.linkedServiceName") == "shape_adls_ls"
    assert spark.conf.get("fs.azure.account.oauth.provider.type").endswith(
        "LinkedServiceBasedTokenProvider"
    )


# ------------------------------------------------------------------------------ pipeline

PIPELINE_SCHEMA = {
    "type": "object",
    "required": ["name", "properties"],
    "properties": {
        "name": {"const": "shape_gate_synapse"},
        "properties": {
            "type": "object",
            "required": ["activities", "parameters"],
            "properties": {
                "activities": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "prefixItems": [
                        {
                            "type": "object",
                            "required": ["name", "type", "dependsOn", "typeProperties"],
                            "properties": {
                                "type": {"const": "SynapseNotebook"},
                                "typeProperties": {
                                    "type": "object",
                                    "required": ["notebook", "parameters", "sparkPool"],
                                    "properties": {
                                        "notebook": {
                                            "type": "object",
                                            "required": ["referenceName", "type"],
                                            "properties": {"type": {"const": "NotebookReference"}},
                                        },
                                        "sparkPool": {
                                            "type": "object",
                                            "required": ["referenceName", "type"],
                                            "properties": {
                                                "type": {"const": "BigDataPoolReference"}
                                            },
                                        },
                                    },
                                },
                            },
                        },
                        {
                            "type": "object",
                            "required": ["name", "type", "dependsOn", "typeProperties"],
                            "properties": {
                                "type": {"const": "IfCondition"},
                                "typeProperties": {
                                    "type": "object",
                                    "required": [
                                        "expression",
                                        "ifTrueActivities",
                                        "ifFalseActivities",
                                    ],
                                },
                            },
                        },
                    ],
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


def _pipeline() -> dict:
    return json.loads(PIPELINE.read_text(encoding="utf-8"))


def test_pipeline_is_schema_valid_with_notebook_if_and_fail():
    p = _pipeline()
    jsonschema.validate(p, PIPELINE_SCHEMA)
    notebook, gate = p["properties"]["activities"]
    assert notebook["name"] == "ProfileTable" and notebook["dependsOn"] == []
    assert gate["dependsOn"] == [
        {"activity": "ProfileTable", "dependencyConditions": ["Succeeded"]}
    ]
    assert gate["typeProperties"]["ifTrueActivities"] == []
    (fail,) = gate["typeProperties"]["ifFalseActivities"]
    assert fail["type"] == "Fail" and fail["typeProperties"]["errorCode"] == "ShapeGateFailed"
    assert notebook["typeProperties"]["notebook"]["referenceName"] == "shape_profile_synapse"


def test_pipeline_parameters_match_the_notebook_parameters_cell():
    nb = nbformat.read(NOTEBOOK, as_version=4)
    cell = next(c for c in nb.cells if "parameters" in c.metadata.get("tags", []))
    defaults = {
        t.targets[0].id: ast.literal_eval(t.value)
        for t in ast.parse(cell.source).body
        if isinstance(t, ast.Assign)
    }
    p = _pipeline()
    passed = p["properties"]["activities"][0]["typeProperties"]["parameters"]
    types = {"string": str, "bool": bool, "int": int}
    # the gate pipeline never passes mode or partitions: the notebook's default (exact) is what
    # lets it check a contract, and the distributed mode must not be reachable as a gate
    assert defaults["mode"] == "exact" and defaults["partitions"] == 0
    gate_params = set(defaults) - {"mode", "partitions"}
    assert set(passed) == gate_params == set(p["properties"]["parameters"])
    for name, spec in passed.items():
        assert spec["value"]["value"] == f"@pipeline().parameters.{name}"
        assert isinstance(defaults[name], types[spec["type"]])
        assert p["properties"]["parameters"][name]["type"] == spec["type"]


def test_expressions_reference_real_parameters_and_activities():
    p = _pipeline()
    text = json.dumps(p)
    for ref in re.findall(r"pipeline\(\)\.parameters\.(\w+)", text):
        assert ref in p["properties"]["parameters"]
    assert set(re.findall(r"activity\('(\w+)'\)", text)) == {"ProfileTable"}
    assert "<<SPARK_POOL>>" in text  # a placeholder to fill in, never a real pool name


def test_exit_value_fields_used_by_the_pipeline_exist_in_the_notebook_result():
    nb = nbformat.read(NOTEBOOK, as_version=4)
    keys = set()
    for c in nb.cells:
        if c.cell_type == "code":
            for node in ast.walk(ast.parse(c.source)):
                if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "result":
                    keys = {k.value for k in node.value.keys}
    assert EXIT_KEYS == keys


def test_gate_expressions_on_real_exit_values(spark, lakehouse, adls):
    p = _pipeline()
    gate = p["properties"]["activities"][1]["typeProperties"]
    fail = gate["ifFalseActivities"][0]["typeProperties"]
    for day, expect in ((1, True), (2, False)):
        params = _params(lakehouse, day)
        raw, _, _ = _run(spark, adls, params)
        ctx = {
            "parameters": {**params},
            "activities": {"ProfileTable": {"status": {"Output": {"result": {"exitValue": raw}}}}},
        }
        assert evaluate(gate["expression"], ctx) is expect
        if not expect:
            message = evaluate(fail["message"], ctx)
            assert message.startswith(f"Shape gate failed for {params['sourcePath']}: [")
            assert "allowed_values" in message and "max_null_rate" in message


def test_runbook_has_the_dry_run_checklist_and_flags_what_was_not_verified():
    text = (SYNAPSE / "RUNBOOK.md").read_text(encoding="utf-8")
    assert "## 7. Live dry-run checklist" in text and "- [ ]" in text
    assert "[VERIFY]" in text and "not run in a Synapse workspace" in text
    expr = "@json(activity('ProfileTable').output.status.Output.result.exitValue).passed"
    assert expr in text
    for needle in ("shape_gate_synapse", "shape_profile_synapse", "checked", "Python 3.11"):
        assert needle in text
