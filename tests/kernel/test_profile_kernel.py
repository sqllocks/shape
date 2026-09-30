"""P1-06: the fused profile kernel, Rust vs the independent Python reference, in both modes."""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

import numpy as np
import pyarrow as pa
import pytest

from shape.kernel import dispatch, reference


@pytest.fixture(scope="module")
def native():
    return dispatch._import_native()


def _table(n: int = 6000, seed: int = 11) -> pa.Table:
    rng = np.random.default_rng(seed)
    ints = rng.integers(-500, 500, n).astype(object)
    ints[rng.random(n) < 0.05] = None
    floats = rng.normal(size=n) * 100
    floats[::37] = np.nan
    floats[1::101] = np.inf
    floats[2::103] = -np.inf
    floats[3::107] = -0.0
    floats[4::109] = np.round(floats[4::109])
    fl = pa.array(floats, mask=rng.random(n) < 0.04)
    names = [
        "alice@example.com",
        "bob@example.org",
        "not an email",
        "550e8400-e29b-41d4-a716-446655440000",
        "123-45-6789",
        "192.168.0.1",
        "10.0.0.256",
        "00:1a:2b:3c:4d:5e",
        "GB29NWBK60161331926819",
        "94105",
        "94105-1234",
        "2020-01-05",
        "2020/1/5",
        "(415) 555-2671",
        "USD",
        "en-US",
        "héllo wörld ✓",
        "",
        " ",
        "x" * 300,
        "::1",
        "fe80::1",
    ]
    text = np.array(names, dtype=object)[rng.integers(0, len(names), n)]
    text = pa.array(text, mask=rng.random(n) < 0.06)
    base = int(dt.datetime(2019, 3, 1).timestamp())
    secs = base + rng.integers(0, 3 * 365 * 86400, n)
    cols = {
        "i64": pa.array(ints, type=pa.int64()),
        "i8": pa.array(rng.integers(-128, 128, n).astype(np.int8)),
        "u64": pa.array(
            np.concatenate([[2**64 - 1, 2**63], rng.integers(0, 2**64, n - 2, dtype=np.uint64)])
        ),
        "f64": fl,
        "f32": pa.array(rng.random(n).astype(np.float32) * 10),
        "dec": pa.array(
            [Decimal(int(v)).scaleb(-2) for v in rng.integers(-(10**6), 10**6, n)],
            type=pa.decimal128(12, 2),
        ),
        "flag": pa.array(rng.random(n) < 0.3, mask=rng.random(n) < 0.05),
        "text": text,
        "big_text": pa.array(
            np.array(names, dtype=object)[rng.integers(0, len(names), n)], type=pa.large_string()
        ),
        "d32": pa.array(rng.integers(-2000, 30000, n).astype(np.int32), type=pa.date32()),
        "d64": pa.array(rng.integers(0, 20000, n) * 86_400_000, type=pa.date64()),
        "ts_s": pa.array(secs, type=pa.timestamp("s"), mask=rng.random(n) < 0.03),
        "ts_ms": pa.array(secs * 1000 + rng.integers(0, 1000, n), type=pa.timestamp("ms")),
        "ts_us": pa.array(secs * 10**6 + rng.integers(0, 10**6, n), type=pa.timestamp("us", "UTC")),
        "ts_ns": pa.array(secs * 10**9 + rng.integers(0, 10**9, n), type=pa.timestamp("ns")),
        "bin": pa.array([b"ab"] * n),
        "lst": pa.array([[1]] * n),
        "nulls": pa.nulls(n),
    }
    return pa.table(cols)


def _same(a, b, path="$", rel=1e-9):
    if isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None, path
        if math.isnan(a) or math.isnan(b):
            assert math.isnan(a) and math.isnan(b), path
        else:
            assert a == pytest.approx(b, rel=rel, abs=1e-9), (path, a, b)
    elif isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), (
            path,
            sorted(a, key=str),
            sorted(b, key=str),
        )
        for k in a:
            _same(a[k], b[k], f"{path}.{k}", rel)
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), (path, len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _same(x, y, f"{path}[{i}]", rel)
    else:
        assert a == b, (path, a, b)


def _run(impl, table: pa.Table, mode: str, batch_rows: int):
    st = impl.ProfileState(table.schema, mode)
    for b in table.to_batches(max_chunksize=batch_rows):
        st.update(b)
    return st


@pytest.mark.parametrize("mode", ["exact", "bounded"])
@pytest.mark.parametrize("batch_rows", [6000, 1000, 777])
def test_rust_equals_reference(native, mode, batch_rows):
    table = _table()
    got = _run(native, table, mode, batch_rows).finalize()
    want = _run(reference, table, mode, batch_rows).finalize()
    assert got["rows"] == want["rows"] == table.num_rows and got["mode"] == mode
    _same(got, want)


@pytest.mark.parametrize("mode", ["exact", "bounded"])
def test_merge_equals_one_pass(native, mode):
    table = _table(4000, seed=3)
    half = table.num_rows // 2
    for impl in (native, reference):
        whole = _run(impl, table, mode, 4000)
        a = _run(impl, table.slice(0, half), mode, 500)
        b = _run(impl, table.slice(half), mode, 500)
        a.merge(b)
        assert a.rows == whole.rows
        merged, one = a.finalize(), whole.finalize()
        for m, o in zip(merged["columns"], one["columns"], strict=True):
            # counts, min/max and histograms are exact; float moments agree to rounding
            for key in (
                "count",
                "null_count",
                "true_count",
                "nan_count",
                "finite_count",
                "min",
                "max",
                "hour_hist",
                "dow_hist",
                "month_hist",
                "year_hist",
                "patterns",
            ):
                if key in o:
                    _same(m[key], o[key], key)
            if "mean" in o and o["mean"] is not None:
                assert m["mean"] == pytest.approx(o["mean"], rel=1e-9, abs=1e-9)
                assert m["m2"] == pytest.approx(o["m2"], rel=1e-8, abs=1e-8)
            if mode == "exact" and "top" in o:
                assert m["distinct"] == o["distinct"]
                assert [t[:2] for t in m["top"]] == [t[:2] for t in o["top"]]
                assert [t[3] for t in m["top"]] == [t[3] for t in o["top"]]  # first-seen rows


def test_exact_mode_against_numpy():
    table = _table(5000, seed=5)
    cols = {
        c["name"]: c
        for c in _run(dispatch._import_native(), table, "exact", 999).finalize()["columns"]
    }
    f = table["f64"].to_pandas()
    fin = f[np.isfinite(f)].dropna()
    c = cols["f64"]
    assert c["count"] == 5000 and c["null_count"] == int(
        f.isna().sum() - np.isnan(f.to_numpy()[~f.isna()]).sum()
        if False
        else table["f64"].null_count
    )
    assert c["finite_count"] == len(fin)
    assert c["mean"] == pytest.approx(fin.mean(), rel=1e-12)
    assert c["m2"] / c["finite_count"] == pytest.approx(fin.var(ddof=0), rel=1e-9)
    assert c["quantiles"][0.5] == pytest.approx(np.quantile(fin, 0.5), rel=1e-12)
    assert c["quantiles"][0.99] == pytest.approx(np.quantile(fin, 0.99), rel=1e-12)
    ints = table["i64"].drop_null().to_numpy()
    ci = cols["i64"]
    assert ci["min"] == ints.min() and ci["max"] == ints.max() and ci["distinct"] == len(set(ints))
    top = ci["top"][0]
    vals, counts = np.unique(ints, return_counts=True)
    assert top[1] == counts.max()
    assert cols["u64"]["max"] == 2**64 - 1 and cols["u64"]["kind"] == "int"


def test_special_values_are_counted_as_specified(native):
    t = pa.table({"x": pa.array([1.0, float("nan"), float("inf"), float("-inf"), None, -0.0, 0.0])})
    c = _run(native, t, "exact", 3).finalize()["columns"][0]
    assert (c["count"], c["null_count"], c["nan_count"]) == (7, 1, 1)
    assert (c["pos_inf_count"], c["neg_inf_count"], c["finite_count"]) == (1, 1, 3)
    assert c["distinct"] == 4  # 1.0, inf, -inf and zero (-0.0 == 0.0); NaN and null excluded
    assert c["min"] == -0.0 and c["max"] == 1.0


def test_bool_text_and_temporal_details(native):
    t = pa.table(
        {
            "b": pa.array([True, False, None, True]),
            "s": pa.array(["a@b.co", "héllo", None, ""]),
            "ts": pa.array([0, 3_600_000_000, 86_400_000_000 * 3, None], type=pa.timestamp("us")),
        }
    )
    b, s, ts = _run(native, t, "exact", 2).finalize()["columns"]
    assert (b["true_count"], b["false_count"], b["null_count"]) == (2, 1, 1)
    assert (
        s["length"]["min"] == 0
        and s["length"]["max"] == 6
        and s["length"]["hist"] == {0: 1, 5: 1, 6: 1}
    )
    assert s["patterns"]["email"] == 1 and s["min"] == "" and s["max"] == "héllo"
    assert ts["hour_hist"][0] == 2 and ts["hour_hist"][1] == 1
    assert ts["dow_hist"] == [0, 0, 0, 2, 0, 0, 1]  # 1970-01-01 was a Thursday
    assert ts["month_hist"][0] == 3 and ts["year_hist"] == {1970: 3}


def test_bounded_mode_uses_sketches_and_stays_small(native):
    n = 200_000
    rng = np.random.default_rng(1)
    t = pa.table(
        {
            "k": pa.array(rng.integers(0, 10**9, n)),
            "s": pa.array([f"v{i % 40_000}" for i in range(n)]),
        }
    )
    st = _run(native, t, "bounded", 20_000)
    k, s = st.finalize()["columns"]
    assert k["distinct_exact"] is False and abs(k["distinct"] - n) / n < 0.05
    assert len(k["top"]) <= 64 and len(s["top"]) <= 64
    assert abs(s["distinct"] - 40_000) / 40_000 < 0.05
    ex = _run(native, t, "exact", 20_000).finalize()["columns"][1]
    assert ex["distinct"] == 40_000 and ex["distinct_exact"] is True


def test_errors(native):
    schema = pa.schema([("a", pa.int64())])
    with pytest.raises(ValueError, match="mode"):
        native.ProfileState(schema, "sloppy")
    st = native.ProfileState(schema, "exact")
    with pytest.raises(ValueError, match="schema"):
        st.update(pa.record_batch({"a": ["x"]}))
    with pytest.raises(ValueError, match="cannot merge"):
        st.merge(native.ProfileState(schema, "bounded"))
    assert st.rows == 0 and st.mode == "exact"
    assert st.finalize()["columns"][0]["count"] == 0
