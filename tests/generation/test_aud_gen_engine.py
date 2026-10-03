"""AUD-gen: the engine's own passes (output types, null rates, row counts)."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.engine import cast_output
from shape.generation.schema import Column


def test_a_timestamp_is_cut_down_to_its_precision_before_1970_too():
    # 188: pc.divide truncates toward zero, so 1969-12-31 23:59:59.5 became 1970-01-01 00:00:00.
    col = Column("t", "timestamp", {}, precision=0)
    values = pa.array(
        [
            dt.datetime(1969, 12, 31, 23, 59, 59, 500000),
            dt.datetime(1955, 6, 1, 5, 22, 24, 641277),
            None,
            dt.datetime(2020, 1, 1, 0, 0, 0, 700000),
        ],
        pa.timestamp("us"),
    )
    assert cast_output(values, "timestamp", "t.t", col).to_pylist() == [
        dt.datetime(1969, 12, 31, 23, 59, 59),
        dt.datetime(1955, 6, 1, 5, 22, 24),
        None,
        dt.datetime(2020, 1, 1, 0, 0, 0),
    ]


def _composite(null_rate: float):
    from aud_gen_fixtures import build, col

    tables = {
        "p": (
            ["a", "b"],
            {"a": col("sequence"), "b": col("sequence", start=100)},
        ),
        "c": (
            ["id"],
            {
                "id": col("sequence"),
                "pa": col(
                    "composite_foreign_key",
                    ref_table="p",
                    ref_columns=["a", "b"],
                    nullable=True,
                    null_rate=null_rate,
                ),
                "pb": col("composite_fk_field", source_column="pa", ref_column="b"),
            },
        ),
    }
    return build(tables, {"p": 5, "c": 1000})


def test_the_null_rate_applies_to_a_column_made_by_a_multi_column_strategy():
    # 185: a strategy returning a mapping skipped the null mask: null_rate 0.5 gave 0 nulls.
    from shape.generation.engine import Engine

    engine = Engine(_composite(0.5))
    c = engine.generate().tables["c"]
    nulls = c["pa"].null_count
    assert 400 < nulls < 600
    # the whole composite key is missing together, never half of it
    assert c["pb"].is_null().to_pylist() == c["pa"].is_null().to_pylist()
    assert engine.generate_column("c", "pa", 0, 1000).null_count == nulls
