"""DM-05 / DM-05b: run the Fabric notebooks' code cells locally."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import nbformat
import pandas as pd
import pytest
from conftest import CONTRACT, NOTEBOOKS, make_orders, run_notebook  # noqa: F401

EXIT_KEYS = {
    "table", "rows", "passed", "violations", "drifted", "changes", "artifactPath",
    "sampled", "truncated",
}
NOTEBOOK_FILES = ["shape_setup.ipynb", "shape_profile.ipynb", "shape_profile_spark.ipynb"]
PY_NB = NOTEBOOKS / "shape_profile.ipynb"
SPARK_NB = NOTEBOOKS / "shape_profile_spark.ipynb"


def _params(table: str, **kw):
    p = {
        "tableName": table,
        "contractPath": "contracts/orders.json",
        "baselinePath": "baselines/orders_day1.shape",
        "outputDir": "shape",
        "failOnDrift": False,
    }
    p.update(kw)
    return p


def _check_exit(raw: str | None, lakehouse: Path) -> dict:
    assert raw is not None, "notebook never called notebookutils.notebook.exit"
    assert len(raw.encode()) < 1_000_000
    out = json.loads(raw)
    assert EXIT_KEYS <= set(out)
    assert isinstance(out["passed"], bool) and isinstance(out["drifted"], bool)
    assert isinstance(out["violations"], list) and isinstance(out["changes"], list)
    assert isinstance(out["rows"], int)
    art = lakehouse / "Files" / out["artifactPath"]
    assert art.exists() and art.suffix == ".shape"
    assert art.with_suffix(".html").exists() and art.with_suffix(".summary.json").exists()
    return out


# ------------------------------------------------------------ structure / generation


def test_generated_notebooks_are_current():
    spec = importlib.util.spec_from_file_location("build_notebooks", NOTEBOOKS / "build_notebooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name, nb in mod.build().items():
        committed = json.loads((NOTEBOOKS / name).read_text(encoding="utf-8"))
        assert committed == nb, f"{name} is stale: rerun build_notebooks.py"


@pytest.mark.parametrize("name", NOTEBOOK_FILES)
def test_valid_nbformat_with_fabric_metadata(name):
    nb = nbformat.read(NOTEBOOKS / name, as_version=4)
    nbformat.validate(nb)
    assert nb.metadata["microsoft"]["language"] == "python"
    assert "kernel_info" in nb.metadata and "dependencies" in nb.metadata


@pytest.mark.parametrize("name", ["shape_profile.ipynb", "shape_profile_spark.ipynb"])
def test_parameters_cell_and_exit_placement(name):
    nb = nbformat.read(NOTEBOOKS / name, as_version=4)
    code = [c for c in nb.cells if c.cell_type == "code"]
    tagged = [c for c in code if "parameters" in c.metadata.get("tags", [])]
    assert len(tagged) == 1
    names = {t.id for t in ast.walk(ast.parse(tagged[0].source)) if isinstance(t, ast.Name)}
    assert {"tableName", "contractPath", "baselinePath", "outputDir", "failOnDrift"} <= names

    # exit: exactly once, in the last cell, at top level, never inside try/except
    calls = 0
    for c in code:
        tree = ast.parse("\n".join(ln for ln in c.source.splitlines() if not ln.lstrip().startswith("%")))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "notebookutils.notebook.exit":
                calls += 1
                assert c is code[-1]
                assert node in [getattr(s, "value", None) for s in tree.body]
    assert calls == 1
    assert not any(isinstance(n, ast.Try) for n in ast.walk(ast.parse(code[-1].source)))


def test_python_notebook_configure_and_install_cells():
    src = [c.source for c in nbformat.read(PY_NB, as_version=4).cells if c.cell_type == "code"]
    assert src[0].startswith("%%configure") and '"vCores": 8' in src[0]
    assert any("%pip install builtin/sqllocks_shape-" in s and s.endswith(".whl\n") for s in src)
    nb = nbformat.read(PY_NB, as_version=4)
    assert nb.metadata["kernel_info"]["jupyter_kernel_name"] in ("python3.11", "python3.12")


# ------------------------------------------------------------------- DM-05: setup


def test_setup_notebook_creates_delta_tables(tmp_path):
    from deltalake import DeltaTable

    root = tmp_path / "lh"
    (root / "Files" / "demo" / "day1").mkdir(parents=True)
    (root / "Tables").mkdir()
    make_orders(1, 300).to_parquet(root / "Files" / "demo" / "day1" / "orders.parquet")
    make_orders(2, 300).to_parquet(root / "Files" / "demo" / "orders.parquet")
    run_notebook(NOTEBOOKS / "shape_setup.ipynb", root)
    assert DeltaTable(str(root / "Tables" / "orders_day1")).to_pyarrow_table().num_rows == 300
    assert DeltaTable(str(root / "Tables" / "orders")).to_pyarrow_table().num_rows == 300


def test_setup_notebook_fails_clearly_without_data(tmp_path):
    (tmp_path / "Files" / "demo").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="Files/demo"):
        run_notebook(NOTEBOOKS / "shape_setup.ipynb", tmp_path)


# ---------------------------------------------------------------- DM-05: python nb


def test_python_notebook_day1_passes(lakehouse):
    raw, ns = run_notebook(PY_NB, lakehouse, _params("orders_day1"))
    out = _check_exit(raw, lakehouse)
    assert out["passed"] is True and out["violations"] == []
    assert out["drifted"] is False and out["rows"] == 2000
    assert out["table"] == "orders_day1" and out["sampled"] is False


def test_python_notebook_day2_fails_with_violations_and_drift(lakehouse):
    raw, _ = run_notebook(PY_NB, lakehouse, _params("orders_day2"))
    out = _check_exit(raw, lakehouse)
    assert out["passed"] is False
    rules = {(v["column"], v["rule"]) for v in out["violations"]}
    assert ("email", "max_null_rate") in rules and ("status", "allowed_values") in rules
    assert out["drifted"] is True
    assert {c["column"] for c in out["changes"]} >= {"email", "status", "amount"}


def test_fail_on_drift_string_from_pipeline(lakehouse):
    # day 1 against a baseline of itself: no drift, still passes with failOnDrift="true"
    raw, _ = run_notebook(PY_NB, lakehouse, _params("orders_day1", failOnDrift="true"))
    assert _check_exit(raw, lakehouse)["passed"] is True
    # no contract, only drift: fails only because failOnDrift is on
    on, _ = run_notebook(PY_NB, lakehouse, _params("orders_day2", contractPath="", failOnDrift="True"))
    off, _ = run_notebook(PY_NB, lakehouse, _params("orders_day2", contractPath="", failOnDrift="false"))
    on_out, off_out = _check_exit(on, lakehouse), _check_exit(off, lakehouse)
    assert on_out["passed"] is False and on_out["violations"][0]["rule"] == "drift"
    assert off_out["passed"] is True and off_out["drifted"] is True


def test_no_contract_no_baseline(lakehouse):
    raw, _ = run_notebook(PY_NB, lakehouse, _params("orders_day2", contractPath="", baselinePath=""))
    out = _check_exit(raw, lakehouse)
    assert out["passed"] is True and out["drifted"] is False and out["changes"] == []


def test_exit_value_is_compact_when_many_violations(lakehouse, tmp_path):
    wide = pd.DataFrame({f"c{i}": range(1500) for i in range(400)})
    from deltalake import write_deltalake

    write_deltalake(str(lakehouse / "Tables" / "wide"), wide)
    contract = {"columns": {f"c{i}": {"max": -1} for i in range(400)}}
    (lakehouse / "Files" / "contracts" / "wide.json").write_text(json.dumps(contract))
    raw, _ = run_notebook(
        PY_NB, lakehouse, _params("wide", contractPath="contracts/wide.json", baselinePath="")
    )
    out = _check_exit(raw, lakehouse)
    assert out["passed"] is False and len(out["violations"]) == 100 and out["truncated"] is True


def test_artifacts_can_be_the_next_baseline(lakehouse):
    raw, _ = run_notebook(PY_NB, lakehouse, _params("orders_day1", baselinePath=""))
    art = _check_exit(raw, lakehouse)["artifactPath"]
    raw2, _ = run_notebook(PY_NB, lakehouse, _params("orders_day2", baselinePath=art))
    assert _check_exit(raw2, lakehouse)["drifted"] is True


# --------------------------------------------------------------- DM-05b: spark nb


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    delta = pytest.importorskip("delta", reason="delta-spark is a [dev] dependency")
    from pyspark.sql import SparkSession

    wh = tmp_path_factory.mktemp("warehouse")
    builder = (
        SparkSession.builder.master("local[1]")
        .appName("shape-l2-tests")
        .config("spark.sql.warehouse.dir", str(wh))
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
    )
    session = delta.configure_spark_with_delta_pip(builder).getOrCreate()
    yield session
    session.stop()


@pytest.fixture()
def spark_tables(spark, lakehouse):
    for day in (1, 2):
        spark.sql(f"DROP TABLE IF EXISTS orders_day{day}")
        spark.sql(
            f"CREATE TABLE orders_day{day} USING DELTA "
            f"LOCATION '{lakehouse / 'Tables' / f'orders_day{day}'}'"
        )
    return lakehouse


@pytest.mark.parametrize("table", ["orders_day1", "orders_day2"])
def test_spark_notebook_matches_python_notebook(spark, spark_tables, table):
    lh = spark_tables
    py_raw, _ = run_notebook(PY_NB, lh, _params(table))
    sp_raw, ns = run_notebook(SPARK_NB, lh, _params(table), {"spark": spark})
    py, sp = _check_exit(py_raw, lh), _check_exit(sp_raw, lh)
    for out in (py, sp):
        out.pop("artifactPath")
    assert sp == py
    assert int(spark.version.split(".")[0]) >= 4  # this run exercised toArrow()


def test_spark_notebook_samples_above_row_limit(spark, spark_tables):
    lh = spark_tables
    raw, ns = run_notebook(
        SPARK_NB, lh, _params("orders_day1", contractPath="", baselinePath=""),
        {"spark": spark}, {"DRIVER_ROW_LIMIT = 5_000_000": "DRIVER_ROW_LIMIT = 500"},
    )
    out = _check_exit(raw, lh)
    assert out["sampled"] is True and out["rows"] == 2000  # rows = true row count
    assert 0 < ns["table"].num_rows < 2000
