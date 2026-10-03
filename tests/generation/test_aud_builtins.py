"""Regression tests of the AUD-builtins audit (issues #129 to #149): the built-in strategies,
distribution families and calendars."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pyarrow as pa

from shape.generation.engine import Engine
from shape.generation.schema import GenSchema


def _table(
    columns: dict[str, dict[str, Any]],
    rows: int = 1000,
    seed: int = 7,
    chunk_rows: int | None = None,
) -> pa.Table:
    doc = {
        "schema_version": 1,
        "model": {"name": "t", "seed": seed},
        "tables": {
            "t": {"name": "t", "columns": {k: {"name": k, **v} for k, v in columns.items()}}
        },
        "relationships": [],
        "generation": {"scale": "s", "scales": {"s": {"t": rows}}},
    }
    options = {} if chunk_rows is None else {"chunk_rows": chunk_rows}
    return Engine(GenSchema.from_dict(doc), seed=seed, **options).generate().tables["t"]


def _times(
    generator: dict[str, Any], rows: int = 2000, chunk_rows: int | None = None
) -> list[datetime]:
    table = _table(
        {"ts": {"type": "datetime", "generator": {"strategy": "temporal", **generator}}},
        rows,
        chunk_rows=chunk_rows,
    )
    return list(table["ts"].to_pylist())


# ---- #129: seasonal temporal values stay inside a start and end with a time of day ----------


def test_seasonal_values_stay_inside_timed_bounds() -> None:
    start, end = datetime(2024, 1, 1, 12), datetime(2024, 1, 3, 6)
    bounds = {"pattern": "seasonal", "start": start.isoformat(), "end": end.isoformat()}
    for profiles in (
        {"month": {"Jan": 1}},
        {"day_of_week": {"Mon": 3, "Tue": 1, "Wed": 1}},
        {"hour_of_day": {"10": 1, "20": 1}},
        {"month": {"Jan": 1}, "hour_of_day": {"3": 1, "13": 1}},
    ):
        values = _times({**bounds, "profiles": profiles})
        assert min(values) >= start and max(values) < end, profiles


def test_seasonal_exclusive_midnight_end_is_not_a_possible_day() -> None:
    values = _times(
        {
            "pattern": "seasonal",
            "start": "2024-01-01",
            "end": "2024-01-03T00:00:00",
            "profiles": {"month": {"Jan": 1}},
        }
    )
    assert max(values) < datetime(2024, 1, 3)
    assert {v.day for v in values} == {1, 2}


def test_seasonal_timed_bounds_do_not_depend_on_chunking() -> None:
    spec = {
        "pattern": "seasonal",
        "start": "2024-01-01T12:00:00",
        "end": "2024-01-09T06:30:00",
        "profiles": {"day_of_week": {"Mon": 2}, "hour_of_day": {"9": 1, "17": 2}},
    }
    assert _times(spec, 700) == _times(spec, 700, chunk_rows=97)


def test_seasonal_partial_edge_days_weigh_their_share_of_the_day() -> None:
    # 18:00 on the 1st to 06:00 on the 3rd with every day and hour equally likely: the 1st and
    # the 3rd hold a quarter of a day each and the 2nd a whole day, so about two thirds of the
    # values fall on the 2nd
    values = _times(
        {
            "pattern": "seasonal",
            "start": "2024-01-01T18:00:00",
            "end": "2024-01-03T06:00:00",
            "profiles": {"hour_of_day": {str(h): 1 for h in range(24)}},
        },
        rows=6000,
    )
    share = sum(v.day == 2 for v in values) / len(values)
    assert abs(share - 2 / 3) < 0.03


# ---- #130: truncation works for ordinary intervals, the same for any chunking -----------------

_TRUNCATED = {
    "strategy": "distribution",
    "distribution": "truncated",
    "base": "normal",
    "base_params": {"mean": 0, "std_dev": 1},
    "low": 1.3,
    "high": 10,
}


def test_truncated_ten_percent_interval_fills_a_large_table() -> None:
    values = _table({"x": {"type": "float", "generator": _TRUNCATED}}, rows=20_000)["x"]
    numbers = values.to_numpy()
    assert values.null_count == 0 and numbers.min() >= 1.3 and numbers.max() <= 10


def test_truncated_does_not_depend_on_chunking() -> None:
    column = {"x": {"type": "float", "generator": _TRUNCATED}}
    whole = _table(column, rows=300)["x"].to_pylist()
    assert _table(column, rows=300, chunk_rows=1)["x"].to_pylist() == whole
    assert _table(column, rows=300, chunk_rows=7)["x"].to_pylist() == whole


# ---- #131: the hierarchy sampler cache never serves another dataset's sampler ----------------


def test_hierarchy_sampler_cache_survives_a_reused_object_id(monkeypatch: Any) -> None:
    from shape.builtins.strategies import reference_hierarchy as rh
    from shape.generation.reference import Dataset
    from shape.plugins.api.v1 import GenerationContext

    # a freed dataset's id() can be given to a new dataset; simulate it for every object
    monkeypatch.setattr(rh, "id", lambda obj: 1, raising=False)
    monkeypatch.setattr(rh, "_CACHE", {})
    ctx = GenerationContext(1, "t", "c", 0, 0, 2000)
    spec = {"levels": ["state", "city"]}
    small = Dataset.from_rows("geo", [{"state": "S0", "city": "x"}])
    rh._sampler(small, spec, ctx)
    big = Dataset.from_rows("geo", [{"state": f"S{i}", "city": "x"} for i in range(4)])
    rows = rh._sampler(big, spec, ctx).records(2000, 1, start=0, table="t", key="c")
    assert set(rh._take(big, "state", rows, ctx).to_pylist()) == {"S0", "S1", "S2", "S3"}


# ---- #132: empirical cubic interpolation stays within the outermost anchors -----------------


def test_empirical_cubic_never_leaves_the_outermost_anchors() -> None:
    import pytest

    pytest.importorskip("scipy")
    quantiles = {k: 0 for k in ("p1", "p5", "p10", "p25", "p50", "p75", "p90")}
    quantiles.update({"p95": 100, "p99": 101})
    generator = {"strategy": "empirical", "quantiles": quantiles, "interpolation": "cubic"}
    values = _table({"x": {"type": "float", "generator": generator}}, rows=20_000)["x"].to_numpy()
    assert values.min() >= 0 and values.max() <= 101


# ---- #133: digits is uniform at every width --------------------------------------------------


def test_wide_digits_are_uniform_in_every_position() -> None:
    import numpy as np

    for width in (16, 18):
        generator = {"strategy": "native", "provider": "digits", "width": width}
        text = _table({"d": {"type": "string", "generator": generator}}, rows=20_000)["d"]
        values = text.to_pylist()
        assert {len(v) for v in values} == {width}
        last = np.bincount([int(v[-1]) for v in values], minlength=10)
        assert last.min() > 1700, last  # 2000 expected per digit
        assert len({v[-3:] for v in values}) > 990


# ---- #134: zero-padded hour_of_day keys are hours ---------------------------------------------


def test_zero_padded_hour_keys_are_read_as_hours() -> None:
    values = _times({"pattern": "seasonal", "profiles": {"hour_of_day": {"00": 1, "07": 1}}})
    assert {v.hour for v in values} == {0, 7}


# ---- #135: an nth-weekday rule stays in its month ----------------------------------------------


def test_nth_weekday_rule_has_no_date_in_a_month_without_that_weekday() -> None:
    from datetime import date

    import pytest

    from shape.builtins.calendars import rule_from_spec

    fifth_monday = rule_from_spec({"month": 2, "weekday": "mon", "n": 5})
    assert fifth_monday.on(2021) is None  # February 2021 has four Mondays
    assert fifth_monday.on(2016) == date(2016, 2, 29)
    for n in (0, -2, 6):
        with pytest.raises(ValueError, match="n must be"):
            rule_from_spec({"month": 2, "weekday": "mon", "n": n})


# ---- #136: a monthly payday pays once a month --------------------------------------------------


def test_monthly_payday_defaults_to_the_28th() -> None:
    from datetime import date

    from shape.builtins.calendars import Payday, calendar_from_spec

    payday = Payday(1.5, "monthly", adjust="none")
    assert payday._dates(date(2024, 3, 1), date(2024, 3, 31)) == [
        date(2024, 2, 28),
        date(2024, 3, 28),
        date(2024, 4, 28),
    ]
    factors = calendar_from_spec({"payday": {"kind": "monthly", "lift": 2, "adjust": "none"}})
    lifted = factors.factors(date(2024, 3, 1), date(2024, 3, 31))
    assert [i + 1 for i, f in enumerate(lifted) if f != 1.0] == [28]
    assert Payday(1.5)._dates(date(2024, 3, 2), date(2024, 3, 14))[2:4] == [
        date(2024, 3, 1),
        date(2024, 3, 15),
    ]


# ---- #137: formula errors are StrategyErrors that name the column -----------------------------


def test_formula_misuse_is_a_strategy_error_naming_the_column() -> None:
    import pytest

    from shape.generation.strategy_kit import StrategyError

    base = {"x": {"type": "float", "generator": {"strategy": "uniform", "low": 0, "high": 1}}}
    for expression, message in (("abs + x", "abs is a function"), ("-" * 1500 + "x", "deep")):
        formula = {"type": "float", "generator": {"strategy": "formula", "expression": expression}}
        with pytest.raises(StrategyError, match=f"t\\.y.*{message}|{message}.*t\\.y"):
            _table({**base, "y": formula}, rows=3)


# ---- #138: spec mistakes are StrategyErrors that name the column ------------------------------

_BAD_SPECS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("float", {"strategy": "constant"}),
    ("float", {"strategy": "uniform", "high": 1}),
    ("float", {"strategy": "normal", "mean": 1}),
    ("float", {"strategy": "distribution", "distribution": "histogram"}),
    ("float", {"strategy": "distribution", "distribution": "histogram", "edges": [0, 1, 2]}),
    ("float", {"strategy": "distribution", "distribution": "mixture"}),
    ("string", {"strategy": "weighted_enum", "values": {"a": float("inf"), "b": 1}}),
    ("string", {"strategy": "weighted_enum", "values": {"a": "x", "b": 1}}),
    ("string", {"strategy": "choice", "values": ["a", "b"], "weights": [float("inf"), 1]}),
    ("string", {"strategy": "choice", "values": [1, "a"]}),
    ("string", {"strategy": "lifecycle", "phases": {"a": "x"}}),
    ("string", {"strategy": "lifecycle", "phases": {"a": float("inf"), "b": 1}}),
    ("string", {"strategy": "native", "provider": "digits", "width": "x"}),
    ("string", {"strategy": "native", "provider": "digit_ids", "width": "x"}),
    ("string", {"strategy": "pattern", "format": "{random:5000}"}),
    ("string", {"strategy": "address", "field": "nope", "reference": [{"city": "A"}]}),
)


def test_spec_mistakes_are_strategy_errors_naming_the_column() -> None:
    import pytest

    from shape.generation.strategy_kit import StrategyError

    for kind, generator in _BAD_SPECS:
        with pytest.raises(StrategyError, match=r"t\.y"):
            _table({"y": {"type": kind, "generator": generator}}, rows=3)


def test_derived_and_foreign_key_spec_mistakes_name_the_column() -> None:
    import pytest

    from shape.generation.strategy_kit import StrategyError

    when = {"type": "datetime", "generator": {"strategy": "temporal"}}
    derived = {"strategy": "derived", "source": "a", "days": "x"}
    with pytest.raises(StrategyError, match=r"t\.y"):
        _table({"a": when, "y": {"type": "datetime", "generator": derived}}, rows=3)
    fan = {"strategy": "foreign_key", "ref": "p.k", "fan_out": {"top_share": [1]}}
    with pytest.raises(StrategyError, match=r"t\.y"):
        _two_tables({"y": {"type": "integer", "generator": fan}})


def _two_tables(child: dict[str, dict[str, Any]], rows: int = 3) -> pa.Table:
    """``child`` as table ``t`` next to a parent ``p`` with a sequence primary key ``k``."""
    doc = {
        "schema_version": 1,
        "model": {"name": "t", "seed": 7},
        "tables": {
            "p": {
                "name": "p",
                "primary_key": ["k"],
                "columns": {
                    "k": {"name": "k", "type": "integer", "generator": {"strategy": "sequence"}}
                },
            },
            "t": {"name": "t", "columns": {k: {"name": k, **v} for k, v in child.items()}},
        },
        "relationships": [],
        "generation": {"scale": "s", "scales": {"s": {"p": 5, "t": rows}}},
    }
    return Engine(GenSchema.from_dict(doc), seed=7).generate().tables["t"]


# ---- #201: a foreign key into its own table without a primary key is a clear error ------------


def test_self_foreign_key_without_primary_key_is_a_circular_error() -> None:
    import pytest

    from shape.generation.strategy_kit import StrategyError

    key = {"type": "integer", "generator": {"strategy": "sequence"}}
    ref = {"type": "integer", "generator": {"strategy": "foreign_key", "ref": "t.k"}}
    with pytest.raises(StrategyError, match=r"circular.*t\.y|t\.y.*circular"):
        _table({"k": key, "y": ref}, rows=5)


# ---- #140: only Faker's public provider methods are providers ----------------------------------


def test_faker_accepts_only_provider_methods() -> None:
    import pytest

    from shape.generation.strategy_kit import StrategyError

    pytest.importorskip("faker")
    for name in ("seed_instance", "__class__", "add_provider", "_Faker__config"):
        generator = {"strategy": "faker", "provider": name}
        with pytest.raises(StrategyError, match="unknown faker provider"):
            _table({"y": {"type": "string", "generator": generator}}, rows=3)
    generator = {"strategy": "faker", "provider": "color_name"}
    assert _table({"y": {"type": "string", "generator": generator}}, rows=3)["y"].null_count == 0


# ---- #144: fitting a degenerate sample is a FamilyError ---------------------------------------


def test_fit_family_refuses_degenerate_samples_with_a_family_error() -> None:
    import pytest

    from shape.builtins.distributions.families import FAMILIES, FamilyError, fit_family

    fittable = [name for name in FAMILIES if name != "truncated"]
    for name in fittable:
        with pytest.raises(FamilyError):
            fit_family(name, [])
        with pytest.raises(FamilyError):
            fit_family(name, [1.0, float("nan")])
    for name in ("gamma", "beta", "pareto"):
        with pytest.raises(FamilyError):
            fit_family(name, [0.5] * 10)
    with pytest.raises(FamilyError):
        fit_family("triangular", [0.5])
    assert fit_family("normal", [1.0, 2.0, 3.0])["mu"] == 2.0


# ---- #146: the faker docs do not promise distinct values ---------------------------------------


def test_faker_docs_do_not_promise_distinct_values() -> None:
    from pathlib import Path

    from shape.builtins.strategies import providers

    root = Path(__file__).resolve().parents[2]
    doc = (root / "docs" / "GENERATION_STRATEGIES.md").read_text("utf-8")
    for text in (doc, providers.Faker.__doc__ or ""):
        assert "every value distinct" not in text and "all values distinct" not in text
        assert "pool entry" in " ".join(text.split())


# ---- #147: a time-zone offset on a temporal bound is converted to UTC, without warnings --------


def test_temporal_bound_with_an_offset_is_the_utc_instant() -> None:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        values = _times({"start": "2024-01-01T05:00:00+05:00", "end": "2024-01-01T01:00:00Z"})
    assert min(values) >= datetime(2024, 1, 1) and max(values) < datetime(2024, 1, 1, 1)


# ---- #148: a sequence that leaves int64 is an error, not wrapped keys ---------------------------


def test_sequence_past_int64_is_a_strategy_error() -> None:
    import pytest

    from shape.generation.strategy_kit import StrategyError

    big = {"strategy": "sequence", "start": 2**62, "step": 2**62}
    with pytest.raises(StrategyError, match=r"int64.*t\.y|t\.y.*int64"):
        _table({"y": {"type": "integer", "generator": big}}, rows=3)
    edge = {"strategy": "sequence", "start": 2**63 - 3, "step": 1}
    assert _table({"y": {"type": "integer", "generator": edge}}, rows=3)["y"].to_pylist()[-1] == (
        2**63 - 1
    )


# ---- #149: calendar and seasonal-profile inputs are checked -------------------------------------


def test_calendar_inputs_are_checked() -> None:
    from datetime import date

    import pytest

    from shape.builtins.calendars import Trend, UsFederalCalendar
    from shape.generation.strategy_kit import StrategyError

    with pytest.raises(ValueError, match="annual_growth"):
        Trend(float("nan"))
    with pytest.raises(ValueError, match="end must not be before start"):
        UsFederalCalendar().factors(date(2024, 1, 5), date(2024, 1, 1))
    for profiles in ({"month": {"January": 100}}, {"day_of_week": {"Monday": 2}}):
        with pytest.raises(StrategyError, match=r"Jan|Mon"):
            _times({"pattern": "seasonal", "profiles": profiles}, rows=10)
