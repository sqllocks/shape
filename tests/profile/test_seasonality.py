"""W7-03 item 2: seasonality of numeric columns against a date or timestamp column."""

from __future__ import annotations

import json
import time
import tracemalloc
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.cli.main import main
from shape.profile import seasonality as S

CODE = (
    "import shape,json;"
    "d=shape.profile({path!r}).to_dict();"
    "print(json.dumps(d['columns']['sales']['seasonality']))"
)


def _daily(values: np.ndarray, start: str = "2023-01-02") -> pa.Table:
    days = np.datetime64(start) + np.arange(len(values)).astype("timedelta64[D]")
    return pa.table({"day": pa.array(days.astype("datetime64[s]")), "sales": pa.array(values)})


def _weekly_cycle(weeks: int = 30, strength: float = 0.8, seed: int = 5) -> np.ndarray:
    """A weekly cycle whose seasonal variance share is ``strength``, plus noise."""
    rng = np.random.default_rng(seed)
    n = weeks * 7
    figure = np.array([0.0, 1.0, 2.0, 3.0, 2.0, -3.0, -5.0])
    figure -= figure.mean()
    seasonal = np.tile(figure, weeks)
    noise_sd = float(seasonal.std()) * np.sqrt((1.0 - strength) / strength)
    return 100.0 + seasonal + rng.normal(0.0, noise_sd, n)


def _entry(t: pa.Table, name: str = "sales", **kw: Any) -> dict[str, Any]:
    return dict(shape.profile(t, **kw).to_dict()["columns"][name]["seasonality"])


def test_a_planted_weekly_cycle_gives_period_7_and_seasonal() -> None:
    e = _entry(_daily(_weekly_cycle()))
    assert e["applicable"] is True
    assert (e["time_column"], e["granularity"], e["period"]) == ("day", "day", 7)
    assert e["seasonal"] is True
    assert abs(e["strength"] - 0.8) < 0.1
    assert e["acf"] > 0.4


def test_white_noise_is_not_seasonal() -> None:
    for seed in range(5):
        e = _entry(_daily(np.random.default_rng(seed).normal(0.0, 1.0, 210)))
        assert e["applicable"] is True and e["seasonal"] is False, seed
        assert e["strength"] < 0.3, seed


def test_a_trend_alone_is_not_seasonal() -> None:
    e = _entry(_daily(np.arange(210.0) + np.random.default_rng(1).normal(0, 1, 210)))
    assert e["applicable"] is True and e["seasonal"] is False


def test_the_strength_boundary_is_at_least_0_6() -> None:
    assert S.SEASONAL_STRENGTH == 0.6
    weak = _entry(_daily(_weekly_cycle(strength=0.3)))
    assert weak["seasonal"] is False and weak["strength"] < 0.6
    strong = _entry(_daily(_weekly_cycle(strength=0.9)))
    assert strong["seasonal"] is True and strong["strength"] >= 0.6


def test_three_full_periods_are_needed() -> None:
    assert _entry(_daily(_weekly_cycle(weeks=3, strength=0.9)))["applicable"] is True
    e = _entry(_daily(_weekly_cycle(weeks=3, strength=0.9)[:20]))  # 20 days: under 3 weeks
    assert e["applicable"] is False and "three full periods" in e["reason"]


def test_a_constant_series_is_not_applicable() -> None:
    e = _entry(_daily(np.full(100, 5.0)))
    assert e["applicable"] is False and e["reason"] == "constant series"


def test_no_time_column_gives_a_reason() -> None:
    p = shape.profile(pa.table({"v": pa.array(np.arange(100.0))}))
    e = p.to_dict()["columns"]["v"]["seasonality"]
    assert e["applicable"] is False and "no date or timestamp column" in e["reason"]


def test_two_time_columns_need_the_option() -> None:
    t = _daily(_weekly_cycle()).append_column(
        "other", pa.array(np.arange(210).astype("datetime64[D]"))
    )
    e = _entry(t)
    assert e["applicable"] is False and "time_column" in e["reason"]
    named = _entry(t, time_column="day")
    assert named["applicable"] is True and named["time_column"] == "day"


def test_time_column_errors() -> None:
    t = _daily(_weekly_cycle())
    with pytest.raises(ValueError, match="not a column"):
        shape.profile(t, time_column="nope")
    with pytest.raises(ValueError, match="not a date or timestamp"):
        shape.profile(t, time_column="sales")


def test_a_dict_of_tables_uses_the_named_column_where_it_exists() -> None:
    a, b = _daily(_weekly_cycle()), pa.table({"x": pa.array(np.arange(60.0))})
    doc = shape.profile({"a": a, "b": b}, time_column="day").to_dict()["tables"]
    assert doc["a"]["columns"]["sales"]["seasonality"]["applicable"] is True
    assert doc["b"]["columns"]["x"]["seasonality"]["applicable"] is False
    with pytest.raises(ValueError, match="any table"):
        shape.profile({"a": a, "b": b}, time_column="zzz")


def test_a_span_under_14_days_is_aggregated_per_hour_with_period_24() -> None:
    n = 24 * 6
    t0 = np.datetime64("2023-03-01T00:00:00")
    hours = t0 + np.arange(n).astype("timedelta64[h]")
    rng = np.random.default_rng(2)
    v = 10.0 + 4.0 * np.sin(2 * np.pi * np.arange(n) / 24) + rng.normal(0, 0.5, n)
    e = _entry(pa.table({"ts": pa.array(hours.astype("datetime64[s]")), "kw": pa.array(v)}), "kw")
    assert (e["granularity"], e["period"], e["seasonal"]) == ("hour", 24, True)


def test_monthly_and_weekly_candidates() -> None:
    n = 365 * 4
    days = np.datetime64("2020-01-01") + np.arange(n).astype("timedelta64[D]")
    rng = np.random.default_rng(4)
    month = days.astype("datetime64[M]").astype(int) % 12
    v = 50.0 + 10.0 * np.cos(2 * np.pi * month / 12) + rng.normal(0, 1.0, n)
    e = _entry(pa.table({"d": pa.array(days.astype("datetime64[s]")), "v": pa.array(v)}), "v")
    assert (e["granularity"], e["period"], e["seasonal"]) == ("month", 12, True)


def test_gaps_are_filled_and_a_sparse_series_is_not_computed() -> None:
    values = _weekly_cycle(weeks=30, strength=0.9)
    t = _daily(values)
    keep = np.ones(len(values), bool)
    keep[::9] = False  # some empty days
    e = _entry(t.filter(pa.array(keep)))
    assert e["applicable"] is True and e["period"] == 7
    sparse = np.zeros(len(values), bool)
    sparse[::5] = True  # one day in five
    e2 = _entry(t.filter(pa.array(sparse)))
    assert e2["applicable"] is False and "half" in e2["reason"]


def test_nulls_and_non_finite_values_are_ignored() -> None:
    values = _weekly_cycle(strength=0.9)
    values[3] = np.nan
    values[10] = np.inf
    t = _daily(values)
    t = t.set_column(
        0, "day", pa.array(t["day"].to_pylist()[:5] + [None] + t["day"].to_pylist()[6:])
    )
    e = _entry(t)
    assert e["applicable"] is True and e["period"] == 7


def test_integer_and_date_typed_time_columns() -> None:
    v = np.round(_weekly_cycle(strength=0.9)).astype(np.int64)
    days = np.datetime64("2023-01-02") + np.arange(len(v)).astype("timedelta64[D]")
    t = pa.table({"d": pa.array(days), "n": pa.array(v)})  # date32
    assert shape.profile(t).to_dict()["columns"]["n"]["seasonality"]["period"] == 7


def test_the_acf_and_strength_helpers_on_known_series() -> None:
    y = np.tile([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0], 6)
    assert S.acf_at(y, 7) > 0.8
    assert S.strength(y, 7) > 0.99
    assert S.acf_at(np.full(30, 2.0), 7) == 0.0
    assert S.strength(np.random.default_rng(0).normal(size=300), 7) < 0.2


def test_the_seasonality_is_in_both_kernels(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    p = tmp_path / "t.parquet"
    import pyarrow.parquet as pq

    pq.write_table(_daily(_weekly_cycle()), p)
    out = {}
    for kernel in ("python", "rust"):
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                CODE.format(path=str(p)),
            ],
            env={**os.environ, "SHAPE_KERNEL": kernel},
            capture_output=True,
            text=True,
            check=True,
        )
        out[kernel] = json.loads(r.stdout)
    assert out["python"] == out["rust"]


def test_the_cli_option(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import pyarrow.parquet as pq

    t = _daily(_weekly_cycle()).append_column(
        "other", pa.array(np.arange(210).astype("datetime64[D]"))
    )
    src = tmp_path / "t.parquet"
    pq.write_table(t, src)
    out = tmp_path / "t.shape"
    assert main(["profile", str(src), "-o", str(out), "--time-column", "day"]) == 0
    col = shape.load(out).to_dict()["columns"]["sales"]
    assert col["seasonality"]["time_column"] == "day" and col["seasonality"]["period"] == 7
    assert main(["profile", str(src), "-o", str(out), "--time-column", "nope"]) != 0


@pytest.mark.heavy
def test_the_cost_on_a_10_million_row_table_is_bounded() -> None:
    n = 10_000_000
    base = np.datetime64("2020-01-01T00:00:00").astype("datetime64[s]").astype(np.int64)
    secs = base + (np.arange(n, dtype=np.int64) * 5)  # about 580 days
    ts = pa.array(secs.astype("datetime64[s]"))
    vals = pa.array(np.random.default_rng(0).normal(size=n))
    cols = [
        type("C", (), {"name": "t", "kind": "dt64", "arr": pa.chunked_array([ts])})(),
        type("C", (), {"name": "v", "kind": "float", "arr": pa.chunked_array([vals])})(),
    ]
    tracemalloc.start()
    start = time.perf_counter()
    out = S.table_seasonality(cols, {"t": "datetime", "v": "float"}, None)
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert out["v"]["applicable"] is True
    assert elapsed < 10.0
    assert peak < 0.2 * n * 8
