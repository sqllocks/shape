"""AUD-gen: the engine's own passes (output types, null rates, row counts)."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

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


def _counted(derived=None, date_range=None):
    from aud_gen_fixtures import build, col

    from shape.generation.schema import GenSchema

    s = build(
        {
            "p": (["id"], {"id": col("sequence")}),
            "c": (["id"], {"id": col("sequence"), "p_id": col("foreign_key", ref="p.id")}),
        },
        {"p": 10},
        rels=(("p", "c", "id", "p_id"),),
    )
    doc = s.to_dict()
    doc["generation"]["derived_counts"] = derived or {}
    if date_range is not None:
        doc["model"]["date_range"] = date_range
    return GenSchema.from_dict(doc)


@pytest.mark.parametrize(
    ("derived", "date_range"),
    [
        ({"c": {"per_parent": "p", "ratio": "2"}}, None),
        ({"c": {"per_parent": "p", "ratio": -3}}, None),
        ({"c": {"fixed": "ten"}}, None),
        ({"c": {"fixed": -1}}, None),
        ({"c": {"per_year": 10}}, {"start": "2030-01-01"}),
        ({"c": {"per_year": 10}}, {"start": "2026-01-01", "end": "2024-12-31"}),
    ],
)
def test_bad_derived_counts_are_errors_naming_the_table(derived, date_range):
    # 187: a string ratio gave a 100-digit row count, a negative ratio an IndexError in
    # generate(), per_year with no end assumed 2025 (a negative count) and start > end 0 rows.
    from shape.errors import ShapeSchemaError
    from shape.generation.engine import calculate_row_counts

    with pytest.raises(ShapeSchemaError, match="'c'"):
        calculate_row_counts(_counted(derived, date_range))


def test_an_integral_float_fixed_count_is_a_count():
    # 187: fixed 10.0 was ignored silently (the table got the default 100 rows).
    from shape.generation.engine import calculate_row_counts

    assert calculate_row_counts(_counted({"c": {"fixed": 10.0}}))["c"] == 10


def test_a_negative_row_count_override_is_an_error():
    # 187: Engine(s, row_counts={"c": -5}).generate() raised IndexError: list index out of range.
    from shape.errors import ShapeSchemaError
    from shape.generation.engine import Engine

    with pytest.raises(ShapeSchemaError, match="'c'"):
        Engine(_counted(), row_counts={"c": -5})


def _lookup_schema():
    from aud_gen_fixtures import build, col

    return build(
        {
            "src": (
                ["id"],
                {"id": col("sequence"), "name": col("weighted_enum", "string", values={"x": 1})},
            ),
            "dst": (
                ["id"],
                {
                    "id": col("sequence"),
                    "k": col("uniform", "integer", low=1, high=1000, output_type="int64"),
                    "nm": col(
                        "lookup",
                        "string",
                        source_table="src",
                        source_column="name",
                        via="k",
                        key="id",
                    ),
                },
            ),
        },
        {"src": 40_000, "dst": 40_000},
    )


def test_a_lookup_source_is_a_level_before_its_reader():
    # 186: dependency_levels read foreign keys only: [['dst', 'src']].
    from shape.generation.engine import dependency_levels

    assert dependency_levels(_lookup_schema()) == [["src"], ["dst"]]
    assert dependency_levels(_composite(0.0)) == [["p"], ["c"]]


def test_threads_generate_a_parent_once(monkeypatch):
    # 186: with 4 threads, src (40k rows) was generated once per thread plus once.
    from collections import Counter

    from shape.generation import engine as engine_module

    rows: Counter[str] = Counter()
    original = engine_module.Engine.generate_chunk

    def counted(self, table, row_start, n_rows, *, chunk=None):
        rows[table] += n_rows
        return original(self, table, row_start, n_rows, chunk=chunk)

    monkeypatch.setattr(engine_module.Engine, "generate_chunk", counted)
    monkeypatch.setenv("SHAPE_THREADS", "4")
    engine_module.Engine(_lookup_schema()).generate()
    assert rows == {"src": 40_000, "dst": 40_000}


def test_an_integer_output_near_the_int64_bounds_is_held_inside_them():
    # 181: floats that round to 2**63 were cast with safe=False to -9223372036854775808.
    values = pa.array([9.223372036854776e18, -9.3e18, 1e19, 12.6, None, float("nan")])
    got = cast_output(values, "int64", "t.a").to_pylist()
    assert got[0] > 9.2e18 and got[2] > 9.2e18
    assert got[1] < -9.2e18
    assert got[3:] == [13, None, None]  # NaN has no integer: null
