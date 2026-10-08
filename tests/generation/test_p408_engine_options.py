"""P4-08: the engine options the profile fit relies on: ``output_type``, temporal ``granularity``
and per-hour weights, the ``ipv4`` / ``postcode`` / ``zip_plus4`` providers and the copula's
null-aware mode."""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from shape.generation.correlation import apply_copula
from shape.generation.engine import Engine
from shape.generation.schema import SCHEMA_VERSION, GenSchema


def column(
    generator: dict[str, Any], ctype: str = "string", rows: int = 20_000, **opts: Any
) -> pa.Array:
    doc = {
        "schema_version": SCHEMA_VERSION,
        "model": {"name": "m", "seed": 3},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": ["k"],
                "columns": {
                    "k": {"name": "k", "type": "integer", "generator": {"strategy": "sequence"}},
                    "x": {"name": "x", "type": ctype, "generator": generator, **opts},
                },
            }
        },
        "generation": {"scale": "s", "scales": {"s": {"t": rows}}},
    }
    return (
        Engine(GenSchema.from_dict(doc), scale="s", seed=5)
        .generate()
        .tables["t"]["x"]
        .combine_chunks()
    )


def test_output_type_casts_and_rounds() -> None:
    gen = {
        "strategy": "distribution",
        "distribution": "normal",
        "params": {"mean": 50, "std_dev": 9},
    }
    assert column(gen).type == pa.float64()
    ints = column({**gen, "output_type": "int64"})
    assert ints.type == pa.int64()
    floats = column(gen).to_numpy()
    assert (ints.to_numpy() == np.round(floats)).all()
    enum = {"strategy": "weighted_enum", "values": {"1": 0.5, "2": 0.5}, "output_type": "int64"}
    assert set(column(enum).to_pylist()) == {1, 2}
    flags = {
        "strategy": "weighted_enum",
        "values": {"true": 0.7, "false": 0.3},
        "output_type": "bool",
    }
    col = column(flags)
    assert col.type == pa.bool_() and abs(col.to_numpy(zero_copy_only=False).mean() - 0.7) < 0.02


def test_output_type_must_be_known() -> None:
    with pytest.raises(ValueError, match="output_type"):
        column({"strategy": "uuid", "output_type": "decimal128"})


def test_temporal_granularity_day_gives_midnights() -> None:
    spec = {
        "strategy": "temporal",
        "start": "2020-01-01",
        "end": "2020-12-31",
        "pattern": "seasonal",
        "profiles": {
            "day_of_week": {
                "Sat": 1.0,
                "Sun": 1.0,
                "Mon": 0.0,
                "Tue": 0.0,
                "Wed": 0.0,
                "Thu": 0.0,
                "Fri": 0.0,
            }
        },
        "granularity": "day",
    }
    values = column(spec, "datetime")
    assert values.type == pa.timestamp("us")
    arr = values.to_numpy()
    assert (arr == arr.astype("datetime64[D]").astype("datetime64[us]")).all()
    weekdays = (arr.astype("datetime64[D]").astype(np.int64) + 3) % 7
    assert set(weekdays) <= {5, 6}
    with pytest.raises(Exception, match="granularity"):
        column({**spec, "granularity": "week"}, "datetime")


def test_temporal_hour_weights_follow_the_profile() -> None:
    hours = {str(h): (3.0 if h in (9, 10) else 0.0) for h in range(24)}
    spec = {
        "strategy": "temporal",
        "start": "2020-01-01",
        "end": "2020-03-01",
        "pattern": "seasonal",
        "profiles": {"hour_of_day": hours},
    }
    arr = column(spec, "datetime", rows=30_000).to_numpy()
    hour = (arr.astype("datetime64[h]").astype(np.int64)) % 24
    share = np.bincount(hour, minlength=24) / len(hour)
    assert share[9] == pytest.approx(0.5, abs=0.02) and share[10] == pytest.approx(0.5, abs=0.02)
    assert share.sum() == pytest.approx(1.0) and share[[9, 10]].sum() == pytest.approx(1.0)
    with pytest.raises(Exception, match="hour"):
        column({**spec, "profiles": {"hour_of_day": {"24": 1.0}}}, "datetime")


def test_native_network_providers() -> None:
    ips = column({"strategy": "faker", "provider": "ipv4"}).to_pylist()
    assert all(re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", v) for v in ips)
    octets = np.array([[int(p) for p in v.split(".")] for v in ips])
    assert octets.min() >= 0 and octets.max() <= 255
    assert octets[:, 0].min() >= 1 and octets[:, 3].min() >= 1
    codes = column({"strategy": "faker", "provider": "postcode"}).to_pylist()
    assert all(re.fullmatch(r"\d{5}", v) for v in codes)
    plus4 = column({"strategy": "native", "provider": "zip_plus4"}).to_pylist()
    assert all(re.fullmatch(r"\d{5}-\d{4}", v) for v in plus4)


def _frame(n: int = 4000) -> pa.Table:
    rng = np.random.default_rng(1)
    a, b = np.sort(rng.normal(size=n)), rng.normal(size=n)
    return pa.table({"a": a, "b": pa.array(b, mask=rng.random(n) < 0.2)})


def test_copula_skips_a_column_with_nulls_unless_asked_to_rank() -> None:
    table = _frame()
    skipped = apply_copula(table, [["a", "b", 0.9]], 1, "t", threshold=0.0)
    assert skipped["b"].equals(table["b"])  # b has nulls: left alone (the default)
    ranked = apply_copula(table, [["a", "b", 0.9]], 1, "t", threshold=0.0, nulls="rank")
    assert ranked["b"].null_count == table["b"].null_count
    assert ranked["b"].is_null().equals(table["b"].is_null())  # the nulls stay where they were
    assert sorted(v for v in ranked["b"].to_pylist() if v is not None) == sorted(
        v for v in table["b"].to_pylist() if v is not None
    )  # same values, reordered
    both = np.array(
        [
            (x, y)
            for x, y in zip(ranked["a"].to_pylist(), ranked["b"].to_pylist(), strict=True)
            if y is not None
        ]
    )
    assert np.corrcoef(both.T)[0, 1] > 0.8


def test_copula_threshold_comes_from_the_schema() -> None:
    doc = {
        "schema_version": SCHEMA_VERSION,
        "model": {"name": "m", "seed": 3},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": ["k"],
                "columns": {
                    "k": {"name": "k", "type": "integer", "generator": {"strategy": "sequence"}},
                    "a": {
                        "name": "a",
                        "type": "decimal",
                        "generator": {
                            "strategy": "distribution",
                            "distribution": "normal",
                            "params": {"mean": 0, "std_dev": 1},
                        },
                    },
                    "b": {
                        "name": "b",
                        "type": "decimal",
                        "generator": {
                            "strategy": "distribution",
                            "distribution": "normal",
                            "params": {"mean": 0, "std_dev": 1},
                        },
                    },
                },
            }
        },
        "generation": {"scale": "s", "scales": {"s": {"t": 20_000}}, "output": {}},
        "correlated_columns": {"t": [["a", "b", 0.3]]},
    }

    def corr(output: dict[str, Any]) -> float:
        doc["generation"]["output"] = output  # type: ignore[index]
        t = Engine(GenSchema.from_dict(doc), scale="s", seed=2).generate().tables["t"]
        return float(np.corrcoef(t["a"].to_numpy(), t["b"].to_numpy())[0, 1])

    assert abs(corr({})) < 0.03  # 0.3 is below the default threshold of 0.5: ignored
    assert corr({"copula_threshold": 0.0}) == pytest.approx(0.3, abs=0.03)
