"""PF-06: the Azure Synapse generate notebooks and the generate-then-check pipeline.

The notebooks' code cells run against a local Spark session whose ``abfss://`` paths resolve into a
temporary directory, with ``mssparkutils`` replaced by a stub over the same directory. The
pipeline definition is checked at schema level and its expressions are evaluated on the notebooks'
real exit values. Not covered, because it needs a Synapse workspace: the notebook-activity
exit-value path, its parameter encoding, ``mssparkutils`` behaviour, linked-service access and the
Spark pool's Python version. Those are the live dry-run checklist in
integrations/synapse/RUNBOOK.md.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import jsonschema
import nbformat
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from adf_expr import evaluate
from deltalake import DeltaTable
from fabric_helpers import REPO, NotebookExit, run_notebook

import shape
from shape.integrations.fabric import generation

SYNAPSE = REPO / "integrations" / "synapse"
GEN_NB = SYNAPSE / "notebooks" / "shape_generate_synapse.ipynb"
PROFILE_NB = SYNAPSE / "notebooks" / "shape_profile_domain_synapse.ipynb"
PIPELINE = SYNAPSE / "pipelines" / "shape_generate_gate_synapse.json"
ACCOUNT = "abfss://shape@acct.dfs.core.windows.net"
OUT = f"{ACCOUNT}/generated"

spec = importlib.util.spec_from_file_location("pf06_build_synapse", SYNAPSE / "build_synapse.py")
builder = importlib.util.module_from_spec(spec)
sys.modules["pf06_build_synapse"] = builder
spec.loader.exec_module(builder)

GENERATE_KEYS = {
    "domain",
    "scale",
    "seed",
    "mode",
    "tableFormat",
    "tablesPath",
    "tablePrefix",
    "tables",
    "totalRows",
    "contractPath",
    "kernel",
}
PROFILE_KEYS = {
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


# --------------------------------------------------------------------------------- stubs


def _local(root: Path, url: str) -> str:
    m = re.fullmatch(r"abfss://([^@]+)@[^/]+/(.*)", url)
    assert m, f"not an abfss URL: {url}"
    return str(root / m.group(1) / m.group(2))


class FakeFs:
    """``mssparkutils.fs`` over a directory (``file:/p`` is the local path ``/p``)."""

    def __init__(self, root: Path):
        self.root = root

    def _path(self, url: str) -> Path:
        return (
            Path(url[len("file:") :]) if url.startswith("file:") else Path(_local(self.root, url))
        )

    def exists(self, url):
        return self._path(url).exists()

    def head(self, url, max_bytes=65536):
        return self._path(url).read_text(encoding="utf-8")[:max_bytes]

    def cp(self, src, dest, recurse=False):
        target = self._path(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self._path(src).read_bytes())
        return True

    def put(self, url, content, overwrite=False):
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


class _Writer:
    def __init__(self, writer, root):
        self._w, self._root = writer, root

    def format(self, fmt):
        self._w = self._w.format(fmt)
        return self

    def mode(self, mode):
        self._w = self._w.mode(mode)
        return self

    def save(self, path):
        return self._w.save(_local(self._root, path))


class _Frame:
    def __init__(self, df, root):
        self._df, self._root = df, root

    @property
    def write(self):
        return _Writer(self._df.write, self._root)


class _Reader:
    def __init__(self, reader, root):
        self._r, self._root = reader, root

    def format(self, fmt):
        self._r = self._r.format(fmt)
        return self

    def load(self, path):
        return self._r.load(_local(self._root, path))


class MappedSpark:
    """A Spark session whose ``abfss://`` read and write paths resolve into a directory."""

    def __init__(self, session, root: Path):
        self._s, self._root = session, root

    def __getattr__(self, name):
        return getattr(self._s, name)

    def createDataFrame(self, data, *args, **kwargs):  # noqa: N802 (Spark's name)
        return _Frame(self._s.createDataFrame(data, *args, **kwargs), self._root)

    @property
    def read(self):
        return _Reader(self._s.read, self._root)


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    delta = pytest.importorskip("delta", reason="delta-spark is a test requirement")
    from pyspark.sql import SparkSession

    os.environ["PYSPARK_PYTHON"] = sys.executable
    builder_ = (
        SparkSession.builder.master("local[2]")
        .appName("shape-synapse-generate-tests")
        .config("spark.sql.warehouse.dir", tmp_path_factory.mktemp("warehouse").as_uri())
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog"
        )
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
    )
    spark_session = delta.configure_spark_with_delta_pip(builder_).getOrCreate()
    yield spark_session
    spark_session.stop()


@pytest.fixture()
def adls(tmp_path) -> Path:
    root = tmp_path / "adls"
    root.mkdir()
    return root


def _gen_params(**kw) -> dict:
    p = {
        "domain": "retail",
        "scale": "small",
        "seed": 42,
        "mode": "",
        "outputPath": OUT,
        "tablePrefix": "",
        "tableFormat": "delta",
        "writeMode": "overwrite",
        "maxRows": 20_000_000,
        "linkedServiceName": "",
    }
    p.update(kw)
    return p


def _profile_params(**kw) -> dict:
    p = {
        "domain": "retail",
        "tablesPath": f"{OUT}/tables",
        "tablePrefix": "",
        "tableFormat": "delta",
        "contractPath": f"{OUT}/retail/contract.json",
        "baselinePath": "",
        "outputPath": f"{ACCOUNT}/shape",
        "failOnDrift": False,
        "linkedServiceName": "",
    }
    p.update(kw)
    return p


def _run(path: Path, session, adls: Path, params: dict) -> dict:
    raw, _ = run_notebook(
        path,
        Path("/unused"),
        params,
        {
            "spark": MappedSpark(session, adls),
            "mssparkutils": FakeMssparkutils(adls),
            "displayHTML": lambda html: None,
        },
    )
    assert raw is not None, "the notebook never called mssparkutils.notebook.exit"
    assert len(raw.encode()) < 1_000_000
    return json.loads(raw)


def _generate(session, adls, **kw) -> dict:
    out = _run(GEN_NB, session, adls, _gen_params(**kw))
    assert GENERATE_KEYS <= set(out)
    return out


def _check(session, adls, **kw) -> dict:
    out = _run(PROFILE_NB, session, adls, _profile_params(**kw))
    assert PROFILE_KEYS <= set(out)
    return out


def _table(adls: Path, name: str, prefix: str = "") -> pa.Table:
    return DeltaTable(
        str(adls / "shape" / "generated" / "tables" / f"{prefix}{name}")
    ).to_pyarrow_table()


# ------------------------------------------------------------------------- structure


def test_generated_files_are_current():
    def normal(nb: dict) -> list:
        return [
            ("markdown", "".join(c["source"]).strip())
            if c["cell_type"] == "markdown"
            else (
                "code",
                ast.dump(ast.parse("".join(c["source"]))),
                tuple(c["metadata"].get("tags", [])),
            )
            for c in nb["cells"]
        ]

    for rel, doc in builder.build().items():
        committed = json.loads((SYNAPSE / rel).read_text(encoding="utf-8"))
        if rel.endswith(".ipynb"):
            assert normal(committed) == normal(doc), f"{rel} is stale: rerun build_synapse.py"
            assert committed["metadata"] == doc["metadata"]
        else:
            assert committed == doc, f"{rel} is stale: rerun build_synapse.py"
    assert {
        "notebooks/shape_generate_synapse.ipynb",
        "notebooks/shape_profile_domain_synapse.ipynb",
        "pipelines/shape_generate_gate_synapse.json",
    } <= set(builder.build())


@pytest.mark.parametrize("path", [GEN_NB, PROFILE_NB])
def test_notebooks_are_valid_with_one_parameters_cell_and_a_final_exit(path):
    nb = nbformat.read(path, as_version=4)
    nbformat.validate(nb)
    assert nb.metadata["kernelspec"]["name"] == "synapse_pyspark"
    code = [c for c in nb.cells if c.cell_type == "code"]
    assert len([c for c in code if "parameters" in c.metadata.get("tags", [])]) == 1
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


# ----------------------------------------------------------------- generate notebook


def test_the_notebook_writes_every_table_as_delta_and_the_contract(session, adls):
    out = _generate(session, adls)
    assert (
        out["tablesPath"] == f"{OUT}/tables"
        and out["contractPath"] == f"{OUT}/retail/contract.json"
    )
    assert out["totalRows"] == 21750 and len(out["tables"]) == 9
    expected = shape.generate("retail", scale="small", seed=42)
    for entry in out["tables"]:
        written = _table(adls, entry["table"])
        assert entry["rows"] == written.num_rows == expected[entry["table"]].num_rows
        assert entry["path"] == f"{OUT}/tables/{entry['table']}"
    # the same values as the engine's (a Spark round trip may change the integer or string width)
    customer = _table(adls, "customer").sort_by("customer_id").to_pandas()
    engine = expected["customer"].to_pandas()
    assert customer["customer_id"].tolist() == engine["customer_id"].tolist()
    assert customer["first_name"].tolist() == engine["first_name"].tolist()
    contract = json.loads(
        (adls / "shape" / "generated" / "retail" / "contract.json").read_text(encoding="utf-8")
    )
    assert set(contract["tables"]) == set(expected.tables)
    assert contract["tables"]["customer"]["row_count"] == {"min": 1000, "max": 1000}


def test_parquet_format_prefix_and_star_mode(session, adls):
    out = _generate(
        session, adls, tableFormat="parquet", tablePrefix="r_", mode="star", scale="fabric_demo"
    )
    assert out["tableFormat"] == "parquet" and out["mode"] == "star"
    folder = adls / "shape" / "generated" / "tables" / "r_customer"
    assert not (folder / "_delta_log").exists()
    assert (
        pq.read_table(str(folder)).num_rows
        == shape.generate("retail", scale="fabric_demo", mode="star")["customer"].num_rows
    )


def test_max_rows_refuses_a_scale_too_large_for_the_driver(session, adls):
    with pytest.raises(ValueError, match="above maxRows"):
        _run(GEN_NB, session, adls, _gen_params(maxRows=1000))
    assert not (adls / "shape").exists()


@pytest.mark.parametrize(
    "bad",
    [
        {"domain": "../etc"},
        {"domain": "nope"},
        {"scale": "../x"},
        {"scale": "huge"},
        {"mode": "snowflake"},
        {"tablePrefix": "../x"},
        {"tableFormat": "csv"},
        {"writeMode": "merge"},
        {"outputPath": "/etc/shape"},
    ],
)
def test_bad_parameters_fail_before_anything_is_written(session, adls, bad):
    with pytest.raises((ValueError, generation.GenerationRequestError)):
        _run(GEN_NB, session, adls, _gen_params(**bad))
    assert list(adls.iterdir()) == []


def test_linked_service_name_configures_the_spark_token_provider(session, adls):
    _generate(session, adls, scale="fabric_demo", linkedServiceName="shape_adls_ls")
    assert session.conf.get("spark.storage.synapse.linkedServiceName") == "shape_adls_ls"
    assert session.conf.get("fs.azure.account.oauth.provider.type").endswith(
        "LinkedServiceBasedTokenProvider"
    )


# ------------------------------------------------------------------ profile notebook


def test_generated_data_passes_the_domains_contract(session, adls):
    _generate(session, adls)
    out = _check(session, adls)
    assert out["passed"] is True and out["violations"] == [] and out["drifted"] is False
    assert out["rows"] == 21750 and len(out["tables"]) == 9 and out["tables"]["order"] == 5000
    artifact = Path(_local(adls, out["artifactPath"]))
    assert artifact.exists()
    assert set(shape.load(str(artifact)).tables) == set(out["tables"])


def test_the_parquet_tables_pass_too(session, adls):
    _generate(session, adls, tableFormat="parquet")
    assert _check(session, adls, tableFormat="parquet")["passed"] is True


def test_tables_that_break_the_contract_fail_the_gate(session, adls):
    _generate(session, adls)
    # damage `customer` in place: keep 900 rows, as a Delta overwrite
    customer = _table(adls, "customer").slice(0, 900)
    from deltalake import write_deltalake

    write_deltalake(
        str(adls / "shape" / "generated" / "tables" / "customer"), customer, mode="overwrite"
    )
    out = _check(session, adls)
    assert out["passed"] is False
    assert "customer:row_count.min" in {v["rule"] for v in out["violations"]}


def test_a_missing_table_fails_instead_of_passing_vacuously(session, adls):
    import shutil

    _generate(session, adls)
    shutil.rmtree(adls / "shape" / "generated" / "tables" / "return")
    with pytest.raises(Exception, match="return|PATH_NOT_FOUND|does not exist|DELTA"):
        _check(session, adls)


def test_baseline_drift_is_reported_and_fails_only_with_fail_on_drift(session, adls):
    _generate(session, adls)
    base = _check(session, adls)
    baseline = f"{ACCOUNT}/baselines/retail_base.shape"
    local_base = adls / "shape" / "baselines" / "retail_base.shape"
    local_base.parent.mkdir(parents=True)
    local_base.write_bytes(Path(_local(adls, base["artifactPath"])).read_bytes())
    same = _check(session, adls, baselinePath=baseline, failOnDrift="true")
    assert same["passed"] is True and same["drifted"] is False
    _generate(session, adls, seed=99)
    seen = _check(session, adls, baselinePath=baseline)
    assert seen["drifted"] is True and seen["changes"] and seen["passed"] is True
    gated = _check(session, adls, baselinePath=baseline, failOnDrift="True")
    assert gated["passed"] is False and gated["violations"][-1]["rule"] == "drift"


# ---------------------------------------------------------------------- the pipeline

REFERENCE = {
    "type": "object",
    "required": ["referenceName", "type"],
    "properties": {"referenceName": {"type": "string"}, "type": {"type": "string"}},
}
NOTEBOOK_ACTIVITY = {
    "type": "object",
    "required": ["name", "type", "dependsOn", "typeProperties"],
    "properties": {
        "type": {"const": "SynapseNotebook"},
        "typeProperties": {
            "type": "object",
            "required": ["notebook", "parameters", "sparkPool"],
            "properties": {
                "notebook": {
                    "allOf": [REFERENCE],
                    "properties": {"type": {"const": "NotebookReference"}},
                },
                "sparkPool": {
                    "allOf": [REFERENCE],
                    "properties": {"type": {"const": "BigDataPoolReference"}},
                },
            },
        },
    },
}
IF_ACTIVITY = {
    "type": "object",
    "required": ["name", "type", "dependsOn", "typeProperties"],
    "properties": {
        "type": {"const": "IfCondition"},
        "typeProperties": {
            "type": "object",
            "required": ["expression", "ifTrueActivities", "ifFalseActivities"],
        },
    },
}
PIPELINE_SCHEMA = {
    "type": "object",
    "required": ["name", "properties"],
    "properties": {
        "name": {"const": "shape_generate_gate_synapse"},
        "properties": {
            "type": "object",
            "required": ["activities", "parameters"],
            "properties": {
                "activities": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "prefixItems": [NOTEBOOK_ACTIVITY, NOTEBOOK_ACTIVITY, IF_ACTIVITY],
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
PARAM_TYPES = {"string": str, "bool": bool, "int": int}


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
    assert (generate["name"], profile["name"], gate["name"]) == (
        "GenerateDomain",
        "ProfileAndCheck",
        "CheckGate",
    )
    assert generate["dependsOn"] == []
    assert generate["typeProperties"]["notebook"]["referenceName"] == "shape_generate_synapse"
    assert profile["typeProperties"]["notebook"]["referenceName"] == "shape_profile_domain_synapse"
    assert profile["dependsOn"] == [
        {"activity": "GenerateDomain", "dependencyConditions": ["Succeeded"]}
    ]
    assert gate["dependsOn"] == [
        {"activity": "ProfileAndCheck", "dependencyConditions": ["Succeeded"]}
    ]
    assert gate["typeProperties"]["ifTrueActivities"] == []
    (fail,) = gate["typeProperties"]["ifFalseActivities"]
    assert fail["type"] == "Fail" and fail["typeProperties"]["errorCode"] == "ShapeContractFailed"
    assert "<<SPARK_POOL>>" in json.dumps(p)  # a placeholder to fill in, never a real pool name


@pytest.mark.parametrize(
    "index, notebook, from_exit_value",
    [(0, GEN_NB, set()), (1, PROFILE_NB, {"tablesPath", "contractPath"})],
)
def test_notebook_parameters_match_the_notebook_parameters_cell(index, notebook, from_exit_value):
    p = _pipeline()
    passed = p["properties"]["activities"][index]["typeProperties"]["parameters"]
    defaults = _cell_defaults(notebook)
    # maxRows is deliberately not a pipeline parameter: the notebook's own cap applies
    expected = set(defaults) - ({"maxRows"} if index == 0 else set())
    assert set(passed) == expected
    for name, spec in passed.items():
        assert isinstance(defaults[name], PARAM_TYPES[spec["type"]]), name
        if name in from_exit_value:
            assert "exitValue" in spec["value"]["value"]
            continue
        source = "reportPath" if (index == 1 and name == "outputPath") else name
        assert spec["value"]["value"] == f"@pipeline().parameters.{source}"
        assert p["properties"]["parameters"][source]["type"] == spec["type"]
    used = set(re.findall(r"pipeline\(\)\.parameters\.(\w+)", json.dumps(p)))
    assert used == set(p["properties"]["parameters"])


def test_expressions_reference_real_parameters_and_activities():
    p = _pipeline()
    text = json.dumps(p)
    for ref in re.findall(r"pipeline\(\)\.parameters\.(\w+)", text):
        assert ref in p["properties"]["parameters"], ref
    assert set(re.findall(r"activity\('(\w+)'\)", text)) == {"GenerateDomain", "ProfileAndCheck"}


def test_exit_value_fields_the_pipeline_reads_exist_in_the_notebook_results():
    def keys(path: Path) -> set[str]:
        found: set[str] = set()
        for c in nbformat.read(path, as_version=4).cells:
            if c.cell_type == "code":
                for node in ast.walk(ast.parse(c.source)):
                    if (
                        isinstance(node, ast.Assign)
                        and isinstance(node.value, ast.Dict)
                        and getattr(node.targets[0], "id", "") in ("result", "result_value")
                    ):
                        found |= {k.value for k in node.value.keys}
        return found

    text = json.dumps(_pipeline())
    read_gen = set(re.findall(r"activity\('GenerateDomain'\)[\w.]*exitValue\)\.(\w+)", text))
    read_prof = set(re.findall(r"activity\('ProfileAndCheck'\)[\w.]*exitValue\)\.(\w+)", text))
    assert read_gen == {"tablesPath", "contractPath"}
    assert {"passed", "violations"} <= read_prof
    assert read_gen <= keys(GEN_NB) == GENERATE_KEYS
    assert read_prof <= keys(PROFILE_NB) == PROFILE_KEYS


def test_the_pipeline_expressions_on_real_exit_values(session, adls):
    p = _pipeline()
    acts = {a["name"]: a for a in p["properties"]["activities"]}
    gate = acts["CheckGate"]["typeProperties"]
    fail = gate["ifFalseActivities"][0]["typeProperties"]
    params = {k: v["defaultValue"] for k, v in p["properties"]["parameters"].items()}
    raw_generate, _ = run_notebook(
        GEN_NB,
        Path("/unused"),
        _gen_params(),
        {
            "spark": MappedSpark(session, adls),
            "mssparkutils": FakeMssparkutils(adls),
            "displayHTML": lambda html: None,
        },
    )
    ctx = {
        "parameters": params,
        "activities": {
            "GenerateDomain": {"status": {"Output": {"result": {"exitValue": raw_generate}}}}
        },
    }
    passed_args = acts["ProfileAndCheck"]["typeProperties"]["parameters"]
    tables_path = evaluate(passed_args["tablesPath"]["value"], ctx)
    contract_path = evaluate(passed_args["contractPath"]["value"], ctx)
    assert tables_path == f"{OUT}/tables" and contract_path == f"{OUT}/retail/contract.json"

    kw = {"tablesPath": tables_path, "contractPath": contract_path}
    raw_ok, _ = run_notebook(
        PROFILE_NB,
        Path("/unused"),
        _profile_params(**kw),
        {
            "spark": MappedSpark(session, adls),
            "mssparkutils": FakeMssparkutils(adls),
            "displayHTML": lambda html: None,
        },
    )
    ctx["activities"]["ProfileAndCheck"] = {"status": {"Output": {"result": {"exitValue": raw_ok}}}}
    assert evaluate(gate["expression"], ctx) is True

    from deltalake import write_deltalake

    customer = _table(adls, "customer").slice(0, 5)
    write_deltalake(
        str(adls / "shape" / "generated" / "tables" / "customer"), customer, mode="overwrite"
    )
    raw_bad, _ = run_notebook(
        PROFILE_NB,
        Path("/unused"),
        _profile_params(**kw),
        {
            "spark": MappedSpark(session, adls),
            "mssparkutils": FakeMssparkutils(adls),
            "displayHTML": lambda html: None,
        },
    )
    ctx["activities"]["ProfileAndCheck"] = {
        "status": {"Output": {"result": {"exitValue": raw_bad}}}
    }
    assert evaluate(gate["expression"], ctx) is False
    message = evaluate(fail["message"], ctx)
    assert message.startswith("Shape generated data broke the contract of retail: [")
    assert "customer:row_count.min" in message


def test_runbook_documents_the_generation_pipeline_and_flags_what_was_not_verified():
    text = (SYNAPSE / "RUNBOOK.md").read_text(encoding="utf-8")
    for needle in (
        "shape_generate_synapse",
        "shape_profile_domain_synapse",
        "shape_generate_gate_synapse",
        "sqllocks-shape-domains",
        "maxRows",
    ):
        assert needle in text, needle
    expr = "@json(activity('GenerateDomain').output.status.Output.result.exitValue).contractPath"
    assert expr in text
    assert "[VERIFY]" in text and "- [ ]" in text
