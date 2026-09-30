"""P1-07: the profile engine, its JSON Schema, threads, multi-input, and the P17 regressions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tracemalloc
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.profile.advanced import pearson
from shape.profile.dependence import conditional_numeric_means, normalized_mutual_information
from shape.profile.dependencies import candidate_key, functional_dependency
from shape.profile.engine import (
    EngineOptions,
    profile,
    profile_many,
    profile_table,
    resolve_threads,
)
from shape.schemacheck import validate

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "src" / "shape" / "schemas" / "profile-engine-v1.schema.json").read_text(
        encoding="utf-8"
    )
)


def _table(n: int = 4000) -> pa.Table:
    rng = np.random.default_rng(2)
    f = rng.normal(size=n)
    f[::50] = np.nan
    f[1::97] = np.inf
    f[2::89] = -np.inf
    return pa.table(
        {
            "id": pa.array(np.arange(n)),
            "x": pa.array(f, mask=rng.random(n) < 0.03),
            "flag": pa.array(rng.random(n) < 0.5),
            "word": pa.array(
                np.array(["a@b.co", "héllo", "10.0.0.1", "x"], dtype=object)[rng.integers(0, 4, n)]
            ),
            "day": pa.array(rng.integers(0, 5000, n).astype(np.int32), type=pa.date32()),
            "ts": pa.array(rng.integers(0, 10**15, n), type=pa.timestamp("us")),
            "blob": pa.array([b"x"] * n),
        }
    )


# ---------------------------------------------------------------- schema and modes


@pytest.mark.parametrize("mode", ["exact", "bounded"])
def test_output_validates_against_the_schema(mode):
    doc = profile(_table(), name="t", mode=mode)
    assert validate(doc, SCHEMA) == []
    assert doc["mode"] == mode and doc["tables"]["t"]["rows"] == 4000
    json.dumps(doc, allow_nan=False)  # NaN and inf never reach the JSON: it can be saved


def test_exact_and_bounded_share_one_layout():
    exact = profile(_table(), name="t", mode="exact")["tables"]["t"]["columns"]
    bounded = profile(_table(), name="t", mode="bounded")["tables"]["t"]["columns"]
    for e, b in zip(exact, bounded, strict=True):
        assert set(e) == set(b), e["name"]
        assert set(e["error_models"]) == set(b["error_models"])
        assert e["count"] == b["count"] and e["null_count"] == b["null_count"]
    ex = {c["name"]: c for c in exact}
    bd = {c["name"]: c for c in bounded}
    assert ex["id"]["distinct_exact"] is True and bd["id"]["distinct_exact"] is False
    assert ex["id"]["error_models"]["cardinality"]["exact"] is True
    assert bd["id"]["error_models"]["cardinality"]["algorithm"] == "hyperloglog"
    assert ex["x"]["quantiles"]["0.5"] == pytest.approx(bd["x"]["quantiles"]["0.5"], abs=0.1)
    assert abs(bd["id"]["distinct"] - 4000) / 4000 < 0.02


def test_special_values_are_json_safe_and_typed():
    doc = profile(pa.table({"x": [1.0, float("inf"), float("nan"), None]}), name="t")
    col = doc["tables"]["t"]["columns"][0]
    assert col["nan_count"] == 1 and col["pos_inf_count"] == 1 and col["null_count"] == 1
    assert any(v == "inf" for v, *_ in col["top"])
    assert col["variance_sample"] is None and col["variance_population"] == 0.0
    assert validate(doc, SCHEMA) == []


def test_schema_rejects_malformed_documents():
    doc = profile(_table(50), name="t")
    bad = json.loads(json.dumps(doc))
    bad["tables"]["t"]["columns"][0]["count"] = -1
    bad["mode"] = "sloppy"
    del bad["tables"]["t"]["columns"][1]["kind"]
    errs = validate(bad, SCHEMA)
    assert len(errs) >= 3
    assert validate({}, SCHEMA)


def test_options_are_validated():
    with pytest.raises(ValueError, match="mode"):
        EngineOptions(mode="fast")
    with pytest.raises(ValueError, match="batch_size"):
        EngineOptions(batch_size=0)
    with pytest.raises(ValueError, match="top_n"):
        EngineOptions(top_n=0)


def test_top_n_and_batching_do_not_change_exact_results():
    t = _table(3000)
    one = profile(t, name="t", batch_size=10_000)["tables"]["t"]["columns"]
    many = profile(t, name="t", batch_size=333, top_n=3)["tables"]["t"]["columns"]
    for a, b in zip(one, many, strict=True):
        assert a["count"] == b["count"] and a.get("distinct") == b.get("distinct")
        if "top" in a:
            assert len(b["top"]) <= 3 and [x[:2] for x in a["top"][:3]] == [x[:2] for x in b["top"]]


# --------------------------------------------------------------------- multi-input


def test_profile_many_files_and_names(tmp_path):
    (tmp_path / "a.csv").write_text("k,v\n1,x\n2,y\n", encoding="utf-8")
    pq.write_table(pa.table({"z": [1.5, 2.5, 3.5]}), tmp_path / "b.parquet")
    doc = profile_many([tmp_path / "a.csv", tmp_path / "b.parquet"])
    assert set(doc["tables"]) == {"a", "b"} and doc["tables"]["b"]["rows"] == 3
    named = profile_many({"left": tmp_path / "a.csv", "right": _table(10)})
    assert set(named["tables"]) == {"left", "right"} and validate(named, SCHEMA) == []
    with pytest.raises(ValueError, match="both named"):
        profile_many([tmp_path / "a.csv", tmp_path / "a.csv"])


def test_many_files_are_one_table_and_directories_work(tmp_path):
    for i in range(3):
        (tmp_path / f"p{i}.csv").write_text(
            "k\n" + "".join(f"{j}\n" for j in range(i * 10, i * 10 + 10))
        )
    doc = profile(str(tmp_path / "p*.csv"), name="parts")
    assert doc["tables"]["parts"]["rows"] == 30
    assert profile(tmp_path)["tables"][tmp_path.name]["rows"] == 30


def test_profile_table_accepts_every_reader_source():
    rows = [{"a": 1, "b": "x"}, {"a": 2, "b": None}]
    assert profile_table(rows, "rows")["rows"] == 2
    assert profile_table({"a": np.arange(5)}, "d")["columns"][0]["kind"] == "int"


# -------------------------------------------------------------------------- threads


def test_shape_threads_env_is_read_and_validated(monkeypatch):
    monkeypatch.delenv("SHAPE_THREADS", raising=False)
    assert resolve_threads() == 0 and resolve_threads(3) == 3
    monkeypatch.setenv("SHAPE_THREADS", "2")
    assert resolve_threads() == 2
    for bad in ("two", "-1"):
        monkeypatch.setenv("SHAPE_THREADS", bad)
        with pytest.raises(ValueError, match="SHAPE_THREADS"):
            resolve_threads()


def test_shape_threads_sizes_the_kernel_pool_in_a_fresh_process():
    code = (
        "from shape.profile.engine import configure_threads;"
        "from shape.kernel import kernel_name;"
        "print(kernel_name(), configure_threads())"
    )
    out = {}
    for n in ("1", "3"):
        env = dict(os.environ, SHAPE_THREADS=n, SHAPE_KERNEL="rust")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        out[n] = r.stdout.split()
    assert out["1"] == ["rust", "1"] and out["3"] == ["rust", "3"]


def test_python_kernel_gives_the_same_document(monkeypatch):
    from shape.kernel import dispatch

    t = _table(1500)
    native = profile(t, name="t")
    monkeypatch.setenv("SHAPE_KERNEL", "python")
    dispatch.reset()
    try:
        ref = profile(t, name="t")
    finally:
        dispatch.reset()
    assert [c["count"] for c in native["tables"]["t"]["columns"]] == [
        c["count"] for c in ref["tables"]["t"]["columns"]
    ]
    assert validate(ref, SCHEMA) == []


@pytest.mark.heavy
def test_bounded_mode_memory_does_not_grow_with_rows(tmp_path):
    """The 5M vs 50M check is benchmarks/vs_spindle/profile_1to1/rss_check.py; this is its
    small version: peak RSS of a CSV seven times larger is within 10%."""
    import pyarrow.csv as pacsv

    code = (
        "import resource, sys;"
        "from shape.profile.engine import profile;"
        "profile(sys.argv[1], mode='bounded');"
        "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)"
    )
    peaks = {}
    for rows in (3_000_000, 21_000_000):
        rng = np.random.default_rng(1)
        p = tmp_path / f"r{rows}.csv"
        writer = None
        for start in range(0, rows, 1_000_000):
            m = min(1_000_000, rows - start)
            chunk = pa.table(
                {
                    "id": pa.array(np.arange(start, start + m)),
                    "x": pa.array(rng.normal(size=m)),
                    "name": pa.array([f"n{k}" for k in rng.integers(0, 5000, m)]),
                }
            )
            if writer is None:
                writer = pacsv.CSVWriter(p, chunk.schema)
            writer.write_table(chunk)
        assert writer is not None
        writer.close()
        r = subprocess.run(
            [sys.executable, "-c", code, str(p)], capture_output=True, text=True, check=True
        )
        peaks[rows] = int(r.stdout.split()[-1])
        p.unlink()
    assert abs(peaks[21_000_000] - peaks[3_000_000]) / peaks[3_000_000] < 0.10, peaks


# ------------------------------------------------------------------ P17: bounded memory


def _peak_kib(fn) -> float:
    tracemalloc.start()
    fn()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak / 1024


def _rows(n):
    for i in range(n):
        yield {"a": i * 0.5, "b": i * 1.5 + (i % 7), "k": f"key{i}", "c": i % 13}


def test_p17_pearson_streams_rows():
    rows = list(_rows(1000))
    xs = np.array([r["a"] for r in rows])
    ys = np.array([r["b"] for r in rows])
    assert pearson(iter(rows), "a", "b") == pytest.approx(np.corrcoef(xs, ys)[0, 1], rel=1e-9)
    assert pearson([{"a": 1, "b": 2}], "a", "b") == 0.0
    peak = _peak_kib(lambda: pearson(_rows(200_000), "a", "b"))
    assert peak < 2_000, peak  # the old version held every pair: tens of MiB


def test_p17_mutual_information_keeps_a_bounded_sample():
    small = list(_rows(500))
    assert normalized_mutual_information(small, "a", "b") == pytest.approx(1.0, abs=0.35)
    same = normalized_mutual_information(small, "c", "c")
    assert same == pytest.approx(1.0)
    peak = _peak_kib(lambda: normalized_mutual_information(_rows(200_000), "a", "b", max_rows=2000))
    assert peak < 3_000, peak
    est = normalized_mutual_information(_rows(30_000), "a", "b", max_rows=3000)
    assert 0.5 < est <= 1.0  # a sample still sees the strong dependence


def test_p17_group_and_key_evidence_is_bounded():
    fd = functional_dependency(_rows(20_000), ("k",), "c", max_groups=500)
    assert fd.determinant_groups == 500 and fd.truncated and fd.rows_untracked == 19_500
    assert fd.rows == 20_000 and fd.confidence == 1.0
    full = functional_dependency(_rows(300), ("c",), "a")
    assert not full.truncated and full.determinant_groups == 13 and full.rows_untracked == 0
    ck = candidate_key(_rows(5000), ("k",), max_keys=1000)
    assert ck.exact is False and ck.unique is False and abs(ck.distinct - 5000) / 5000 < 0.05
    exact = candidate_key(_rows(500), ("k",))
    assert exact.exact and exact.unique and exact.distinct == 500
    means = conditional_numeric_means(_rows(5000), "k", "a", min_count=1, max_groups=100)
    assert len(means) == 100
    with pytest.raises(ValueError):
        functional_dependency([], ("k",), "c", max_groups=0)
    with pytest.raises(ValueError):
        candidate_key([], ("k",), max_keys=0)
