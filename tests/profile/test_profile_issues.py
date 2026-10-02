"""Profiling issues #2, #21, #22, #23, #24 and #37 (lane ISS-profile): each has a test that failed
before its fix, run under both kernels."""

from __future__ import annotations

import decimal
import json
import os
import random
import subprocess
import sys
import textwrap
import warnings

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.kernel import dispatch
from shape.privacy.safe_profile import SafeConfig, SafeProfile, to_safe_profile
from shape.privacy.safe_validator import SafeProfileValidator


@pytest.fixture(params=["python", "rust"])
def kernel(request, monkeypatch):
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    yield request.param
    dispatch.reset()


def _col(values, name="c"):
    arr = values if isinstance(values, (pa.Array, pa.ChunkedArray)) else pa.array(values)
    return shape.profile(pa.table({name: arr})).to_dict()["columns"][name]


# ---- #2: PII rates ------------------------------------------------------------------------


def _ssn(rng):
    return f"{rng.randint(900, 998)}-{rng.randint(1, 98):02d}-{rng.randint(1, 9998):04d}"


def _mixed(share, n=4000, seed=1):
    rng = random.Random(seed)
    return [
        _ssn(rng) if rng.random() < share else rng.choice(["gift", "fragile", "leave at door"])
        for _ in range(n)
    ]


@pytest.mark.parametrize("share", [0.01, 0.10, 0.50])
def test_sparse_ssn_is_reported_as_a_rate(kernel, share):
    values = _mixed(share)
    exact = sum("-" in v for v in values) / len(values)
    col = _col(values)
    assert col["pattern"] is None  # the label keeps its 90% rule
    assert col["pattern_rates"]["ssn"] == pytest.approx(exact, abs=1e-6)
    assert col["pattern_contains_rates"]["ssn"] == pytest.approx(exact, abs=1e-6)
    assert exact == pytest.approx(share, abs=0.02)


def test_embedded_ssn_email_and_card_are_found(kernel):
    rng = random.Random(3)
    values = [f"call me, SSN {_ssn(rng)}" for _ in range(100)]
    col = _col(values)
    assert "ssn" not in (col["pattern_rates"] or {})  # not a bare SSN
    assert col["pattern_contains_rates"]["ssn"] == 1.0
    mixed = _col(["mail a@b.com now", "card 4111 1111 1111 1111", "1234 5678 9012 3456", "none"])
    assert mixed["pattern_contains_rates"] == {"email": 0.25, "credit_card": 0.25}  # Luhn only


def test_clean_text_has_no_pattern_rates(kernel):
    col = _col(["alpha", "beta", "gamma"] * 50)
    assert col["pattern_rates"] is None and col["pattern_contains_rates"] is None


def test_pattern_rates_cover_many_distinct_values(kernel):
    values = [f"note {i}" for i in range(3000)] + ["ssn 123-45-6789"] * 6
    assert _col(values)["pattern_contains_rates"]["ssn"] == pytest.approx(6 / 3006, abs=1e-5)


def test_pattern_rates_above_the_distinct_cap_are_estimated_from_a_sample(kernel, monkeypatch):
    from shape.profile.reference import column

    monkeypatch.setattr(column, "_RATE_MAX_DISTINCT", 200)
    rng = random.Random(5)
    values = [f"note {i}" if i % 10 else f"ssn {_ssn(rng)}" for i in range(2000)]  # 10% SSNs
    assert _col(values)["pattern_contains_rates"]["ssn"] == pytest.approx(0.1, abs=0.03)


def test_safe_profile_keeps_a_sparse_pii_column_pattern_only(kernel, tmp_path):
    values = _mixed(0.01)  # three categories and 1% SSNs
    clean = ["gift", "fragile", "leave at door", "box"] * 1000
    table = pa.table({"note": pa.array(values), "clean": pa.array(clean)})
    safe = to_safe_profile(shape.profile(table, name="t"), SafeConfig())
    cols = safe.tables["t"].columns
    assert cols["note"].categorical_weights is None and cols["note"].length_dist is not None
    assert cols["note"].pattern_contains_rates["ssn"] == pytest.approx(0.01, abs=0.005)
    assert cols["clean"].categorical_weights is not None  # an ordinary category stays
    out = tmp_path / "safe.json"
    safe.save(out)
    assert SafeProfileValidator().validate_file(str(out)).is_clean


# ---- #21: no time-zone database -----------------------------------------------------------


_NO_TZ = textwrap.dedent(
    """
    import sys
    sys.modules["tzdata"] = None  # as on Windows without the package
    import pyarrow as pa, shape
    t = lambda tz: pa.table({"ts": pa.array([1, 2, 3] * 20, pa.timestamp("us", tz=tz))})
    for tz in ("UTC", "+05:30"):
        c = shape.profile(t(tz)).to_dict()["columns"]["ts"]
        assert c["dtype"] == "datetime" and c["min_value"][0] == "timestamp", c
        print(tz, c["min_value"][1])
    try:
        shape.profile(t("America/New_York"))
    except ValueError as exc:
        print("named:", exc)
    """
)


def test_utc_and_offset_zones_need_no_time_zone_database(kernel, tmp_path):
    empty = tmp_path / "tz"
    empty.mkdir()
    env = {**os.environ, "PYTHONTZPATH": str(empty), "SHAPE_KERNEL": kernel}
    r = subprocess.run([sys.executable, "-c", _NO_TZ], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "UTC 1970-01-01 00:00:00.000001+00:00" in r.stdout
    assert "+05:30 1970-01-01 05:30:00.000001+05:30" in r.stdout
    assert "named: cannot read the time zone 'America/New_York'" in r.stdout
    assert "tzdata" in r.stdout  # the error says what to install


def test_zoned_columns_keep_wall_clock_and_offset(kernel):
    us = [1_700_000_000_000_000, 1_700_003_600_000_000]
    for tz, first in (
        ("UTC", "2023-11-14 22:13:20+00:00"),
        ("+05:30", "2023-11-15 03:43:20+05:30"),
    ):
        col = _col(pa.array(us, pa.timestamp("us", tz=tz)))
        assert col["min_value"] == ["timestamp", first]
        assert list(col["value_counts_ext"])[0] == first


# ---- #22: NaN, inf and single values ------------------------------------------------------


def test_nan_and_inf_are_counted_apart_from_nulls(kernel):
    table = pa.table(
        {
            "a": [1.0, float("nan"), float("inf"), 2.0, 3.0] * 40,
            "one": [5.0] + [None] * 199,
        }
    )
    p = shape.profile(table, name="t")
    a = p.to_dict()["columns"]["a"]
    assert (a["null_count"], a["nan_count"], a["inf_count"]) == (0, 40, 40)
    assert a["null_rate"] == 0.0
    assert (a["min_value"][1], a["max_value"][1], a["mean"]) == (1.0, 3.0, 2.0)
    assert a["std"] == pytest.approx(np.std([1.0, 2.0, 3.0] * 40, ddof=1))
    assert a["cardinality"] == 3 and a["is_unique"] is False
    one = p.summary()["columns"]["one"]
    assert one["std"] is None and one["mean"] == 5.0 and one["null_rate"] == 0.995
    json.dumps(p.to_dict(), allow_nan=False)  # no NaN reaches the JSON
    json.dumps(p.summary(), allow_nan=False)


def test_real_nulls_stay_nulls_beside_nan(kernel):
    col = _col(pa.array([1.0, None, float("nan"), -float("inf"), 4.0] * 10, pa.float64()))
    assert (col["null_count"], col["nan_count"], col["inf_count"]) == (10, 10, 10)


def test_a_single_value_has_no_spread(kernel):
    for values in ([7], [7.5], [7, None, None]):
        assert _col(values)["std"] is None


def test_infinity_in_a_csv_file_is_profiled(kernel, tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("id,v\n0,inf\n1,-inf\n2,1.5\n3,2.5\n")
    col = shape.profile(str(f)).to_dict()["columns"]["v"]
    assert col["inf_count"] == 2 and col["mean"] == 2.0


# ---- #23: CSV delimiter -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("delim", "rows"),
    [(";", "1;2\n3;4\n"), ("|", "1|2\n3|4\n"), ("\t", "1\t2\n3\t4\n"), (",", "1,2\n3,4\n")],
)
def test_delimiter_is_sniffed(kernel, tmp_path, delim, rows):
    f = tmp_path / "t.csv"
    f.write_text(f"a{delim}b\n" + rows * 40)
    assert list(shape.profile(str(f)).to_dict()["columns"]) == ["a", "b"]


def test_sniffing_keeps_commas_inside_quoted_fields(kernel, tmp_path):
    f = tmp_path / "t.csv"
    f.write_text('a;b\n"x,y";1\n"z,w";2\n' * 20)
    assert list(shape.profile(str(f)).to_dict()["columns"]) == ["a", "b"]


def test_delimiter_encoding_quotechar_and_header_can_be_set(kernel, tmp_path):
    f = tmp_path / "t.csv"
    f.write_text("n;v\ncafé;1\ncafé;2\n", encoding="latin-1")
    cols = shape.profile(str(f), delimiter=";", encoding="latin-1").to_dict()["columns"]
    assert list(cols) == ["n", "v"] and list(cols["n"]["value_counts_ext"]) == ["café"]
    g = tmp_path / "h.csv"
    g.write_text("1,2\n3,4\n" * 5)
    assert list(shape.profile(str(g), header=False).to_dict()["columns"]) == ["f0", "f1"]
    q = tmp_path / "q.csv"
    q.write_text("a,b\n'x,y',1\n'z,w',2\n")
    assert list(shape.profile(str(q), quotechar="'").to_dict()["columns"]) == ["a", "b"]


def test_one_column_with_a_delimiter_in_its_name_warns(kernel, tmp_path):
    f = tmp_path / "t.csv"
    f.write_text("a;b\n" + "1;2\n3;4\n" * 40)
    with pytest.warns(UserWarning, match="delimiter"):
        cols = shape.profile(str(f), delimiter=",").to_dict()["columns"]
    assert list(cols) == ["a;b"]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        shape.profile(str(f))  # sniffed: no warning


def test_cli_profile_takes_the_csv_options(kernel, tmp_path):
    f = tmp_path / "t.csv"
    f.write_text("a;b\n" + "1;2\n3;4\n" * 40)
    env = {**os.environ, "SHAPE_KERNEL": kernel}
    cli = [sys.executable, "-m", "shape.cli.main", "profile", str(f)]
    for extra in ([], ["--delimiter", ";"]):
        out = tmp_path / "o.shape"
        r = subprocess.run([*cli, "-o", str(out), *extra], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        assert list(shape.load(str(out)).to_dict()["columns"]) == ["a", "b"]
    r = subprocess.run(
        [*cli, "-o", str(tmp_path / "n.shape"), "--no-header"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0 and list(
        shape.load(str(tmp_path / "n.shape")).to_dict()["columns"]
    ) == [
        "f0",
        "f1",
    ]


def test_engine_sniffs_the_delimiter(kernel, tmp_path):
    from shape.profile.engine import profile as engine_profile

    f = tmp_path / "t.csv"
    f.write_text("a;b\n" + "1;2\n3;4\n" * 40)
    doc = engine_profile(str(f))
    assert [c["name"] for c in next(iter(doc["tables"].values()))["columns"]] == ["a", "b"]


# ---- #24: decimals ------------------------------------------------------------------------


def test_decimal_precision_and_scale_are_recorded(kernel):
    values = pa.array([decimal.Decimal("1.10"), decimal.Decimal("2.25")] * 50, pa.decimal128(10, 2))
    p = shape.profile(pa.table({"amount": values, "n": pa.array([1, 2] * 50)}), name="t")
    amount = p.summary()["columns"]["amount"]
    assert (amount["precision"], amount["scale"]) == (10, 2)
    assert p.to_dict()["columns"]["amount"]["scale"] == 2
    n = p.summary()["columns"]["n"]
    assert n["precision"] is None and n["scale"] is None


def test_decimal_scale_change_is_visible(kernel):
    def scale(s):
        v = pa.array([decimal.Decimal(1)] * 5, pa.decimal128(12, s))
        return _col(v)["scale"]

    assert (scale(2), scale(4)) == (2, 4)


# ---- #37: profile size --------------------------------------------------------------------


def test_unique_long_text_is_not_stored(kernel):
    n = 1500
    docs = [f"doc{i:05d} " + "lorem ipsum dolor " * 1100 for i in range(n)]
    col = _col(docs)
    assert col["value_counts_ext"] is None and col["is_enum"] is False
    for key in ("min_value", "max_value"):
        assert len(col[key][1]) <= 257  # cut, with an ellipsis
    assert len(json.dumps(shape.profile(pa.table({"t": pa.array(docs)})).to_dict())) < 20_000


def test_long_values_of_a_repeating_column_are_cut(kernel):
    long_a, long_b = "a" * 5000, "b" * 5000
    col = _col([long_a, long_b] * 100)
    assert all(len(k) <= 257 for k in col["value_counts_ext"])
    assert all(len(k) <= 257 for k in col["enum_values"])
    assert sum(col["value_counts_ext"].values()) == pytest.approx(1.0)


def test_short_unique_text_still_lists_its_values(kernel):
    col = _col([f"u{i}" for i in range(150)])
    assert col["value_counts_ext"] and len(col["value_counts_ext"]) == 150


def test_wide_table_correlation_is_bounded(kernel):
    rng = np.random.default_rng(0)
    k = 300
    data = {f"c{i}": rng.normal(size=200) for i in range(k)}
    data["c1"] = data["c0"] * 2 + rng.normal(size=200) * 0.01  # one strong pair
    d = shape.profile(pa.table(data), name="w").to_dict()
    assert d["correlation_truncated"] is True
    matrix = d["correlation_matrix"]
    assert sum(len(r) for r in matrix.values()) <= 2 * k * 25
    assert matrix["c0"]["c1"] > 0.99 and matrix["c1"]["c0"] > 0.99  # strong pairs survive
    assert all(a in matrix[b] for a in matrix for b in matrix[a])  # symmetric
    safe = to_safe_profile(shape.profile(pa.table(data), name="w"), SafeConfig())
    assert safe.tables["w"].to_dict()["correlation_truncated"] is True


def test_narrow_table_keeps_the_full_matrix(kernel):
    rng = np.random.default_rng(0)
    d = shape.profile(pa.table({f"c{i}": rng.normal(size=50) for i in range(30)}), name="n")
    assert "correlation_truncated" not in d.to_dict()
    assert sum(len(r) for r in d.to_dict()["correlation_matrix"].values()) == 30 * 29


def test_safe_profile_compact_and_column_subset(kernel, tmp_path):
    table = pa.table(
        {
            "keep": pa.array([1.5, 2.5, 3.5, 4.5] * 30),
            "drop_a": pa.array([1, 2, 3, 4] * 30),
            "drop_b": pa.array(["x", "y", "z", "w"] * 30),
            "other": pa.array([4.0, 3.0, 2.0, 1.0] * 30),
        }
    )
    safe = to_safe_profile(shape.profile(table, name="t"), SafeConfig())
    full = safe.to_json()
    compact = safe.to_json(compact=True)
    assert len(compact) < len(full) / 2 and ":null" not in compact and "\n" not in compact[:-1]
    back = SafeProfile.from_dict(json.loads(compact))
    assert back.tables["t"].columns["keep"].mean == safe.tables["t"].columns["keep"].mean
    assert back.tables["t"].columns["keep"].pattern is None
    sub = safe.select_columns(exclude=["drop_*"])
    assert list(sub.tables["t"].columns) == ["keep", "other"]
    assert set(sub.tables["t"].correlation_matrix or {}) <= {"keep", "other"}
    assert list(safe.select_columns(include=["keep"]).tables["t"].columns) == ["keep"]
    assert list(safe.tables["t"].columns) == ["keep", "drop_a", "drop_b", "other"]  # not mutated


def test_cli_profile_safe_compact_and_columns(kernel, tmp_path):
    table = pa.table({"a": [1.0, 2.0, 3.0] * 20, "b": [4.0, 5.0, 6.0] * 20})
    src = tmp_path / "p.shape"
    shape.save(shape.profile(table, name="t"), str(src))
    out = tmp_path / "s.json"
    env = {**os.environ, "SHAPE_KERNEL": kernel}
    r = subprocess.run(
        [sys.executable, "-m", "shape.cli.main", "profile", "safe", str(src), "-o", str(out)]
        + ["--compact", "--columns", "a", "--exclude", "zzz"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text())
    assert list(doc["tables"]["t"]["columns"]) == ["a"]
    v = subprocess.run(
        [sys.executable, "-m", "shape.cli.main", "profile", "validate", "--safe", str(out)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert v.returncode == 0, v.stderr
