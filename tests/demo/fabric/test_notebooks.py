"""DM-05 / DM-05b: run the Fabric notebooks' code cells locally."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import nbformat
import pandas as pd
import pytest
from fabric_helpers import (  # noqa: F401
    CONTRACT,
    NOTEBOOKS,
    ONELAKE_TABLES,
    installs_by_path,
    make_orders,
    run_notebook,
)

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
}
NOTEBOOK_FILES = [
    "shape_setup.ipynb",
    "shape_profile.ipynb",
    "shape_profile_spark.ipynb",
    "shape_profile_distributed.ipynb",
]
PY_NB = NOTEBOOKS / "shape_profile.ipynb"
SPARK_NB = NOTEBOOKS / "shape_profile_spark.ipynb"
DIST_NB = NOTEBOOKS / "shape_profile_distributed.ipynb"


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


def _normal(nb: dict) -> list:
    """Cell content independent of formatting (ruff reformats .ipynb files)."""
    out = []
    for c in nb["cells"]:
        src = "".join(c["source"])
        if c["cell_type"] == "markdown":
            out.append(("markdown", src.strip()))
            continue
        magic = any(ln.lstrip().startswith("%") for ln in src.splitlines())
        body = src.strip() if magic else ast.dump(ast.parse(src))
        out.append(("code", body, tuple(c["metadata"].get("tags", []))))
    return out


def test_generated_notebooks_are_current():
    spec = importlib.util.spec_from_file_location(
        "build_notebooks", NOTEBOOKS / "build_notebooks.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name, nb in mod.build().items():
        committed = json.loads((NOTEBOOKS / name).read_text(encoding="utf-8"))
        assert _normal(committed) == _normal(nb), f"{name} is stale: rerun build_notebooks.py"
        assert committed["metadata"] == nb["metadata"]


@pytest.mark.parametrize("name", NOTEBOOK_FILES)
def test_valid_nbformat_with_fabric_metadata(name):
    nb = nbformat.read(NOTEBOOKS / name, as_version=4)
    nbformat.validate(nb)
    assert nb.metadata["microsoft"]["language"] == "python"
    assert "kernel_info" in nb.metadata and "dependencies" in nb.metadata


@pytest.mark.parametrize(
    "name", ["shape_profile.ipynb", "shape_profile_spark.ipynb", "shape_profile_distributed.ipynb"]
)
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


def test_python_notebook_configure_and_install_cells():
    src = [c.source for c in nbformat.read(PY_NB, as_version=4).cells if c.cell_type == "code"]
    assert src[0].startswith("%%configure") and '"vCores": 8' in src[0]
    # F-4: the uploaded wheel is installed by file path, so PyPI's older 0.9.0 can never win;
    # the cell names the platform wheel to put in its place for the Rust kernel (PF-02)
    install = next(s for s in src if "%pip install" in s)
    assert installs_by_path(install)
    assert "cp311-abi3-manylinux_2_28_x86_64.whl" in install
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


def test_setup_notebook_writes_to_onelake_by_abfss_in_fabric(tmp_path, fabric_onelake):
    """F-1: in Fabric a Delta write through /lakehouse/default/Tables fails ("Unable to rename
    file"), so the tables go to the default lakehouse's OneLake URI with the storage token."""
    import pyarrow as pa

    root = tmp_path / "lh"
    (root / "Files" / "demo" / "day1").mkdir(parents=True)
    frame = make_orders(1, 300)
    frame["placed_at"] = pd.Timestamp("2026-01-01") + pd.to_timedelta(range(300), unit="min")
    frame.to_parquet(root / "Files" / "demo" / "day1" / "orders.parquet")
    _, ns = run_notebook(NOTEBOOKS / "shape_setup.ipynb", root)
    assert [c["location"] for c in fabric_onelake] == [f"{ONELAKE_TABLES}/orders_day1"]
    call = fabric_onelake[0]
    assert call["storage_options"] == {
        "bearer_token": "token-for-storage",
        "use_fabric_endpoint": "true",
    }
    assert call["mode"] == "overwrite" and call["schema_mode"] == "overwrite"
    # the types every Delta reader takes: microsecond UTC timestamps, as Shape's sink writes
    assert call["table"].schema.field("placed_at").type == pa.timestamp("us", tz="UTC")
    assert call["table"].num_rows == 300
    assert ns["created"] == [{"table": "orders_day1", "rows": 300, "source": "day1/orders.parquet"}]


def test_setup_notebook_takes_an_explicit_onelake_uri(tmp_path, fabric_onelake):
    root = tmp_path / "lh"
    (root / "Files" / "demo").mkdir(parents=True)
    make_orders(1, 50).to_parquet(root / "Files" / "demo" / "orders.parquet")
    uri = "abfss://Shape@onelake.dfs.fabric.microsoft.com/shape_demo.Lakehouse/Tables"
    run_notebook(
        NOTEBOOKS / "shape_setup.ipynb",
        root,
        replacements={'TABLES_URI = ""': f'TABLES_URI = "{uri}/"'},
    )
    assert [c["location"] for c in fabric_onelake] == [f"{uri}/orders"]


def test_setup_notebook_in_fabric_without_a_lakehouse_says_so(
    tmp_path, fabric_onelake, monkeypatch
):
    import sys
    import types

    monkeypatch.setattr(sys.modules["notebookutils"], "runtime", types.SimpleNamespace(context={}))
    root = tmp_path / "lh"
    (root / "Files" / "demo").mkdir(parents=True)
    make_orders(1, 50).to_parquet(root / "Files" / "demo" / "orders.parquet")
    with pytest.raises(RuntimeError, match="No default lakehouse"):
        run_notebook(NOTEBOOKS / "shape_setup.ipynb", root)
    assert fabric_onelake == []


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
    on, _ = run_notebook(
        PY_NB, lakehouse, _params("orders_day2", contractPath="", failOnDrift="True")
    )
    off, _ = run_notebook(
        PY_NB, lakehouse, _params("orders_day2", contractPath="", failOnDrift="false")
    )
    on_out, off_out = _check_exit(on, lakehouse), _check_exit(off, lakehouse)
    assert on_out["passed"] is False and on_out["violations"][0]["rule"] == "drift"
    assert off_out["passed"] is True and off_out["drifted"] is True


def test_no_contract_no_baseline(lakehouse):
    raw, _ = run_notebook(
        PY_NB, lakehouse, _params("orders_day2", contractPath="", baselinePath="")
    )
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


def test_two_runs_in_the_same_instant_keep_both_artifacts(lakehouse, monkeypatch):
    """PF-06b: the folder was named by the second, so a second run overwrote the first one's
    baseline. Here every run sees the same clock reading."""
    from datetime import UTC, datetime

    from shape.integrations.fabric import run_folder

    frozen = run_folder.run_stamp(datetime(2026, 9, 30, 12, 0, 0, 5, tzinfo=UTC))
    monkeypatch.setattr(run_folder, "run_stamp", lambda now=None: frozen)
    params = _params("orders_day1", baselinePath="")
    first = _check_exit(run_notebook(PY_NB, lakehouse, params)[0], lakehouse)["artifactPath"]
    before = (lakehouse / "Files" / first).read_bytes()
    second = _check_exit(run_notebook(PY_NB, lakehouse, params)[0], lakehouse)["artifactPath"]
    assert first != second and first.split("/")[:2] == second.split("/")[:2]
    assert first.split("/")[2] == frozen and second.split("/")[2] == f"{frozen}_2"
    assert (lakehouse / "Files" / first).read_bytes() == before


# --------------------------------------------------------------- DM-05b: spark nb


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    delta = pytest.importorskip("delta", reason="delta-spark is a [dev] dependency")
    import sys

    from pyspark.sql import SparkSession

    env = pytest.MonkeyPatch()  # restored at teardown
    env.setenv("PYSPARK_PYTHON", sys.executable)  # mapInArrow workers import the same shape
    wh = tmp_path_factory.mktemp("warehouse")
    builder = (
        SparkSession.builder.master("local[1]")
        .appName("shape-l2-tests")
        .config("spark.sql.warehouse.dir", wh.as_uri())
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog"
        )
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
    )
    session = delta.configure_spark_with_delta_pip(builder).getOrCreate()
    yield session
    session.stop()
    env.undo()


@pytest.fixture()
def spark_tables(spark, lakehouse):
    for day in (1, 2):
        spark.sql(f"DROP TABLE IF EXISTS orders_day{day}")
        spark.sql(
            f"CREATE TABLE orders_day{day} USING DELTA "
            f"LOCATION '{(lakehouse / 'Tables' / f'orders_day{day}').as_uri()}'"
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
        SPARK_NB,
        lh,
        _params("orders_day1", contractPath="", baselinePath=""),
        {"spark": spark},
        {"DRIVER_ROW_LIMIT = 5_000_000": "DRIVER_ROW_LIMIT = 500"},
    )
    out = _check_exit(raw, lh)
    assert out["sampled"] is True and out["rows"] == 2000  # rows = true row count
    assert 0 < ns["table"].num_rows < 2000


# ----------------------------------------------- PF-02: distributed PySpark notebook


def _dist_params(table: str, **kw):
    return _params(table, contractPath="", baselinePath="", mode="distributed", partitions=0, **kw)


def _strict_json(path: Path):
    def _no_constant(name: str):
        raise AssertionError(f"{path.name} contains the non-JSON constant {name}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=_no_constant)


def test_distributed_notebook_profiles_on_executors_and_says_it_checked_nothing(
    spark, spark_tables
):
    lh = spark_tables
    raw, _ = run_notebook(DIST_NB, lh, _dist_params("orders_day1"), {"spark": spark})
    assert raw is not None and len(raw.encode()) < 1_000_000
    out = json.loads(raw)
    assert EXIT_KEYS <= set(out)
    assert out["mode"] == "bounded" and out["checked"] is False
    assert out["rows"] == 2000 and out["sampled"] is False
    assert out["passed"] is True and out["violations"] == [] and out["drifted"] is False
    art = lh / "Files" / out["artifactPath"]
    assert art.name == "orders_day1.profile.json"
    doc = _strict_json(art)
    assert doc["mode"] == "bounded" and doc["tables"]["orders_day1"]["rows"] == 2000
    names = [c["name"] for c in doc["tables"]["orders_day1"]["columns"]]
    assert names == ["customer_id", "email", "status", "amount"]
    summary = _strict_json(art.with_name("orders_day1.summary.json"))
    assert summary["rows"] == 2000 and set(summary["columns"]) == set(names)
    assert summary["columns"]["customer_id"]["min"] == 1
    assert summary["columns"]["customer_id"]["max"] == 2000
    assert summary["columns"]["email"]["null_count"] > 0


def test_distributed_notebook_statistics_agree_with_the_exact_notebook(spark, spark_tables):
    lh = spark_tables
    dist_raw, _ = run_notebook(DIST_NB, lh, _dist_params("orders_day2"), {"spark": spark})
    exact_raw, _ = run_notebook(
        SPARK_NB, lh, _params("orders_day2", contractPath="", baselinePath=""), {"spark": spark}
    )
    dist, exact = json.loads(dist_raw), json.loads(exact_raw)
    assert dist["rows"] == exact["rows"]
    bounded = {
        c["name"]: c
        for c in _strict_json(lh / "Files" / dist["artifactPath"])["tables"]["orders_day2"][
            "columns"
        ]
    }
    summary = json.loads(
        (lh / "Files" / exact["artifactPath"]).with_suffix(".summary.json").read_text()
    )
    for name, col in summary["columns"].items():
        assert bounded[name]["null_count"] == pytest.approx(col["null_rate"] * 2000, abs=0.5)
        if col["cardinality"] is not None:
            assert bounded[name]["distinct"] == pytest.approx(col["cardinality"], rel=0.03, abs=1)


def test_distributed_notebook_exact_mode_equals_the_spark_notebook(spark, spark_tables):
    lh = spark_tables
    for table in ("orders_day1", "orders_day2"):
        ex_raw, _ = run_notebook(
            DIST_NB, lh, _params(table, mode="exact", partitions=0), {"spark": spark}
        )
        sp_raw, _ = run_notebook(SPARK_NB, lh, _params(table), {"spark": spark})
        ex, sp = _check_exit(ex_raw, lh), _check_exit(sp_raw, lh)
        assert ex.pop("mode") == "exact" and ex.pop("checked") is True
        for out in (ex, sp):
            out.pop("artifactPath")
        assert ex == sp


def test_distributed_notebook_refuses_a_contract_it_cannot_check(spark, spark_tables):
    with pytest.raises(ValueError, match="mode = 'exact'"):
        run_notebook(
            DIST_NB,
            spark_tables,
            _params("orders_day1", mode="distributed", partitions=0),
            {"spark": spark},
        )


def test_distributed_notebook_rejects_an_unknown_mode(spark, spark_tables):
    with pytest.raises(ValueError, match="mode must be"):
        run_notebook(
            DIST_NB,
            spark_tables,
            _dist_params("orders_day1") | {"mode": "approximate"},
            {"spark": spark},
        )


def test_distributed_notebook_repartitions(spark, spark_tables):
    raw, _ = run_notebook(
        DIST_NB, spark_tables, _dist_params("orders_day1") | {"partitions": 3}, {"spark": spark}
    )
    assert json.loads(raw)["rows"] == 2000


# ----------------------------------------- DEMO-LIVE Obs-2: the baseline's not-signed warning


BASELINE_NOTEBOOKS = [
    "shape_profile.ipynb",
    "shape_profile_spark.ipynb",
    "shape_profile_distributed.ipynb",
    "shape_profile_domain.ipynb",
    "shape_profile_dbt.ipynb",
]


def test_the_baseline_load_silences_only_the_not_signed_warning(lakehouse):
    import warnings

    import shape
    from shape.artifact.io import ArtifactNotVerifiedWarning

    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        raw, ns = run_notebook(PY_NB, lakehouse, _params("orders_day2"))
    assert json.loads(raw)["drifted"] is True  # the baseline was loaded and diffed
    assert not [w for w in seen if issubclass(w.category, ArtifactNotVerifiedWarning)]
    # the same artifact still warns outside the notebook's baseline load: nothing global changed
    with pytest.warns(ArtifactNotVerifiedWarning):
        shape.load(str(lakehouse / "Files" / "baselines" / "orders_day1.shape"))
    # and inside it, any other warning still shows
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        original = shape.load

        def noisy(path):
            warnings.warn("something else", UserWarning, stacklevel=1)
            return original(path)

        ns["shape"].load = noisy
        try:
            ns["load_baseline"]("baselines/orders_day1.shape")
        finally:
            ns["shape"].load = original
    assert [str(w.message) for w in seen] == ["something else"]


@pytest.mark.parametrize("name", BASELINE_NOTEBOOKS)
def test_every_baseline_goes_through_the_scoped_loader(name):
    nb = nbformat.read(NOTEBOOKS / name, as_version=4)
    src = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
    assert "load_baseline(baselinePath)" in src
    assert "shape.load(_resolve(baselinePath))" not in src
    ignores = [
        ast.unparse(n)
        for n in ast.walk(ast.parse("\n".join(ln for ln in src.splitlines() if ln[:1] != "%")))
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "warnings.simplefilter"
    ]
    assert ignores == ["warnings.simplefilter('ignore', ArtifactNotVerifiedWarning)"]
