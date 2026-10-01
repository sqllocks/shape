"""PF-02: the distributed bounded profile (``shape.integrations.fabric.spark``) on a local Spark.

Acceptance: the distributed bounded profile of D3 equals the single-process bounded profile
within the T-14 bounds. "Equals" is checked per statistic:

* counts, null counts, min, max, boolean counts, string lengths, histograms: identical;
* mean and variance: equal up to floating-point summation order (relative 1e-9);
* distinct counts (HLL p=14): both within the sketch's error bound of the true count;
* quantiles (KLL k=200): the rank of each reported value is within the sketch's rank error of
  the requested probability;
* top values (SpaceSaving, capacity 64): ``count - error <= true count <= count``, and every
  value more frequent than ``n / capacity`` is present.

Needs ``pyspark`` (in ``[dev]``) and a Java runtime; both are installed by CI for this file.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyspark  # noqa: F401  (a [dev] dependency and Java are required: a missing one fails)
import pytest

import shape
from shape.integrations.fabric.spark import profile_distributed
from shape.profile.engine import profile as engine_profile

ROOT = Path(__file__).resolve().parents[2]
D3_ROWS = 120_000
APPROXIMATE = {"distinct", "distinct_exact", "top", "quantiles", "error_models"}


def _d3(n: int) -> pa.Table:
    path = ROOT / "benchmarks" / "vs_spindle" / "profile_1to1" / "datasets.py"
    spec = importlib.util.spec_from_file_location("vs_spindle_datasets_pf02", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vs_spindle_datasets_pf02"] = mod
    spec.loader.exec_module(mod)
    table: pa.Table = mod._d3_chunk(0, 0, n)
    return table


@pytest.fixture(scope="module")
def spark(tmp_path_factory: pytest.TempPathFactory) -> Any:
    from pyspark.sql import SparkSession

    os.environ["PYSPARK_PYTHON"] = sys.executable  # executors import the same shape
    session = (
        SparkSession.builder.master("local[2]")
        .appName("shape-pf02")
        .config("spark.sql.warehouse.dir", str(tmp_path_factory.mktemp("warehouse")))
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )
    yield session
    session.stop()


@pytest.fixture(scope="module")
def d3_frame(spark: Any, tmp_path_factory: pytest.TempPathFactory) -> Any:
    import pyarrow.parquet as pq

    path = tmp_path_factory.mktemp("d3") / "d3.parquet"
    pq.write_table(_d3(D3_ROWS), path, row_group_size=20_000)
    return spark.read.parquet(str(path)).repartition(5)


@pytest.fixture(scope="module")
def d3_arrow(d3_frame: Any) -> pa.Table:
    """The table as Spark hands it to Arrow: what both profiles must describe."""
    table: pa.Table = d3_frame.toArrow()
    return table


@pytest.fixture(scope="module")
def distributed(d3_frame: Any) -> dict[str, Any]:
    return profile_distributed(d3_frame, name="d3")


@pytest.fixture(scope="module")
def single(d3_arrow: pa.Table) -> dict[str, Any]:
    return engine_profile(d3_arrow, name="d3", mode="bounded")


def _cols(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in doc["tables"]["d3"]["columns"]}


def _same(a: Any, b: Any, path: str) -> None:
    if isinstance(a, dict):
        assert isinstance(b, dict) and a.keys() == b.keys(), path
        for k in a:
            _same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _same(x, y, f"{path}[{i}]")
    elif isinstance(a, float) and isinstance(b, float):
        assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12), f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


# ------------------------------------------------------------------- the acceptance


def test_document_layout_matches_the_single_process_engine(
    distributed: dict[str, Any], single: dict[str, Any]
) -> None:
    for key in ("schema_version", "engine", "shape_version", "mode"):
        assert distributed[key] == single[key]
    assert distributed["mode"] == "bounded"
    assert distributed["tables"]["d3"]["rows"] == single["tables"]["d3"]["rows"] == D3_ROWS
    d, s = _cols(distributed), _cols(single)
    assert list(d) == list(s) == list(_d3(10).column_names)
    for name in d:
        assert d[name].keys() == s[name].keys(), name
        assert d[name]["error_models"] == s[name]["error_models"], name
        assert d[name]["error_models"]["counts"]["exact"] is True


def test_exact_statistics_are_identical(
    distributed: dict[str, Any], single: dict[str, Any]
) -> None:
    d, s = _cols(distributed), _cols(single)
    for name in d:
        for key in d[name].keys() - APPROXIMATE:
            _same(d[name][key], s[name][key], f"{name}.{key}")


def test_distinct_counts_are_within_the_hll_bound(
    distributed: dict[str, Any], single: dict[str, Any], d3_arrow: pa.Table
) -> None:
    d, s = _cols(distributed), _cols(single)
    checked = 0
    for name, col in d.items():
        if "distinct" not in col:
            continue
        truth = pc.count_distinct(d3_arrow.column(name), mode="only_valid").as_py()
        bound = col["error_models"]["cardinality"]["relative_error"]
        assert bound == pytest.approx(1.04 / math.sqrt(1 << 14))
        for doc in (col, s[name]):
            assert abs(doc["distinct"] - truth) <= max(3 * bound * truth, 2), (name, truth)
        # the two profiles estimate the same set, so they agree to within the bound too
        assert abs(col["distinct"] - s[name]["distinct"]) <= max(6 * bound * truth, 2), name
        checked += 1
    assert checked >= 6


def test_quantiles_are_within_the_kll_rank_error(
    distributed: dict[str, Any], single: dict[str, Any], d3_arrow: pa.Table
) -> None:
    checked = 0
    for name, col in _cols(distributed).items():
        if "quantiles" not in col:
            continue
        eps = col["error_models"]["quantiles"]["relative_error"]
        values = np.sort(
            np.asarray(
                d3_arrow.column(name).drop_null().cast(pa.float64()).to_numpy(zero_copy_only=False)
            )
        )
        for doc in (col, _cols(single)[name]):
            for q, v in doc["quantiles"].items():
                lo = np.searchsorted(values, v, side="left") / len(values)
                hi = np.searchsorted(values, v, side="right") / len(values)
                p = float(q)
                assert lo - 2 * eps <= p <= hi + 2 * eps, (name, q, v, lo, hi)
        checked += 1
    assert checked >= 4


def test_top_values_obey_the_space_saving_bounds(
    distributed: dict[str, Any], d3_arrow: pa.Table
) -> None:
    capacity = 64
    d = _cols(distributed)
    for name in ("event_type", "user_ref", "email"):
        col = d[name]
        counts = {
            row["values"].as_py(): row["counts"].as_py()
            for row in pc.value_counts(d3_arrow.column(name).drop_null())
        }
        assert col["top"], name
        for value, count, error, _first in col["top"]:
            true = counts.get(value, 0)
            assert count - error <= true <= count, (name, value, count, error, true)
        listed = {row[0] for row in col["top"]}
        n = D3_ROWS - col["null_count"]
        for value, true in counts.items():
            if true > n / capacity:
                assert value in listed, (name, value, true)
    assert {row[0] for row in d["event_type"]["top"]} == {
        "view",
        "click",
        "add_to_cart",
        "purchase",
        "logout",
        "login",
    }


# ------------------------------------------------------------------ behaviour


def test_result_is_deterministic_and_ordered_by_partition(
    d3_frame: Any, distributed: dict[str, Any]
) -> None:
    again = profile_distributed(d3_frame, name="d3")
    assert again["tables"] == distributed["tables"]


def test_partitions_option_repartitions(d3_frame: Any, d3_arrow: pa.Table) -> None:
    doc = profile_distributed(d3_frame, name="d3", partitions=3)
    assert doc["tables"]["d3"]["rows"] == D3_ROWS
    one = next(c for c in doc["tables"]["d3"]["columns"] if c["name"] == "event_id")
    assert one["min"] == 0 and one["max"] == D3_ROWS - 1 and one["count"] == D3_ROWS


def test_top_n_limits_the_listed_values(d3_frame: Any) -> None:
    doc = profile_distributed(d3_frame, name="d3", top_n=3)
    assert all(len(c["top"]) <= 3 for c in doc["tables"]["d3"]["columns"] if "top" in c)


def test_nulls_and_an_all_null_partition(spark: Any) -> None:
    from pyspark.sql import types as T

    schema = T.StructType([T.StructField("a", T.LongType()), T.StructField("b", T.StringType())])
    rows = [(None, None)] * 30 + [(i, f"v{i % 3}") for i in range(30)]
    df = spark.createDataFrame(rows, schema).repartition(4)
    doc = profile_distributed(df, name="t")
    a, b = doc["tables"]["t"]["columns"]
    assert doc["tables"]["t"]["rows"] == 60
    assert a["null_count"] == 30 and a["count"] == 60 and a["min"] == 0 and a["max"] == 29
    assert b["null_count"] == 30 and round(b["distinct"]) == 3  # HLL estimate: 3.0003


def test_empty_table_profiles_to_zero_rows(spark: Any) -> None:
    from pyspark.sql import types as T

    schema = T.StructType([T.StructField("a", T.LongType()), T.StructField("b", T.StringType())])
    doc = profile_distributed(spark.createDataFrame([], schema), name="empty")
    entry = doc["tables"]["empty"]
    assert entry["rows"] == 0 and [c["name"] for c in entry["columns"]] == ["a", "b"]


def test_executors_on_the_python_kernel_give_the_same_profile(
    tmp_path: Path, d3_arrow: pa.Table, distributed: dict[str, Any]
) -> None:
    """Snapshots are one format for both kernels: executors on the pure-Python twin (what a
    Fabric Environment without the platform wheel runs) merge into the same document."""
    import pyarrow.parquet as pq
    from pyspark.sql import SparkSession

    path = tmp_path / "d3.parquet"
    pq.write_table(d3_arrow, path, row_group_size=20_000)
    session = (
        SparkSession.builder.master("local[2]")
        .appName("shape-pf02-python-kernel")
        .config("spark.sql.warehouse.dir", str(tmp_path / "wh"))
        .config("spark.ui.enabled", "false")
        .config("spark.executorEnv.SHAPE_KERNEL", "python")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )
    try:
        # the same partitioning as the module fixture: repartition(5) of the same file
        doc = profile_distributed(session.read.parquet(str(path)).repartition(5), name="d3")
    finally:
        session.stop()
    d, s = _cols(doc), _cols(distributed)
    assert doc["tables"]["d3"]["rows"] == D3_ROWS
    for name in d:
        for key in d[name].keys() - APPROXIMATE:
            _same(d[name][key], s[name][key], f"{name}.{key}")


def test_rejects_bad_arguments(d3_frame: Any) -> None:
    with pytest.raises(ValueError, match="top_n"):
        profile_distributed(d3_frame, top_n=0)
    with pytest.raises(ValueError, match="partitions"):
        profile_distributed(d3_frame, partitions=0)


def test_module_imports_without_pyspark() -> None:
    import subprocess

    code = (
        "import sys; sys.modules['pyspark'] = None; "
        "import shape.integrations.fabric.spark as m; print(m.profile_distributed.__name__)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "profile_distributed"


def test_shape_still_profiles_the_same_table_exactly(d3_arrow: pa.Table) -> None:
    """Sanity: the exact (driver-only) path of the notebook is the product API, unchanged."""
    p = shape.profile(d3_arrow.slice(0, 2000), name="d3")
    assert p.summary()["row_count"] == 2000
