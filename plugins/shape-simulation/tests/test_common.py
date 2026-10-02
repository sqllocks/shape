"""The helpers the pattern simulators share."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest
from shape_simulation import _patterns as p


def test_uuid_strings_are_seeded_valid_and_distinct():
    a = p.uuid_strings(np.random.default_rng(1), 500)
    assert a == p.uuid_strings(np.random.default_rng(1), 500)
    assert a != p.uuid_strings(np.random.default_rng(2), 500)
    assert len(set(a)) == 500
    for u in a[:50]:
        assert len(u) == 36 and u[14] == "4" and u[19] in "89ab"
    assert p.uuid_strings(np.random.default_rng(1), 0) == []


def test_hex_strings_length():
    out = p.hex_strings(np.random.default_rng(1), 20, 7)
    assert all(len(s) == 7 and int(s, 16) >= 0 for s in out)


def test_as_table_accepts_the_usual_inputs():
    t = pa.table({"a": [1, 2]})
    assert p.as_table(t) is t
    assert p.as_table(t.to_batches()[0]).equals(t)
    assert p.as_table({"a": [1, 2]}).equals(t)
    assert p.as_table(t.to_pandas()).equals(t)
    with pytest.raises(TypeError):
        p.as_table(3)


def test_table_mapping_reads_generation_results_and_dicts():
    t = pa.table({"a": [1]})

    class Result:
        tables = {"t": t}

    assert p.table_mapping(Result()) == {"t": t}
    assert p.table_mapping({"t": {"a": [1]}})["t"].equals(t)
    with pytest.raises(TypeError):
        p.table_mapping([1])


def test_timestamp_round_trip_with_nulls_and_dates():
    ts = pa.array([1_000_000, None, 5_000_000], pa.timestamp("us", "UTC"))
    us, ok, tz = p.timestamp_us(ts)
    assert (
        us.tolist() == [1_000_000, 0, 5_000_000]
        and ok.tolist() == [True, False, True]
        and tz == "UTC"
    )
    assert p.timestamps(us, tz, ok).equals(ts)
    days, ok2, tz2 = p.timestamp_us(pa.array([1, 2], pa.date32()))
    assert days.tolist() == [86_400_000_000, 172_800_000_000] and tz2 is None and ok2.all()
    text, _, _ = p.timestamp_us(pa.array(["1970-01-01T00:00:01"]))
    assert text.tolist() == [1_000_000]
    with pytest.raises(TypeError):
        p.timestamp_us(pa.array([1, 2]))


def test_parse_start_reads_naive_as_utc():
    assert p.parse_start("1970-01-01T00:00:01") == 1_000_000
    assert p.parse_start("1970-01-01T01:00:00+01:00") == 0


def test_float_values_and_array_map_null_and_nan():
    values, valid = p.float_values(pa.array([1.5, None, 3.0]))
    assert np.isnan(values[1]) and valid.tolist() == [True, False, True]
    assert p.float_array(np.array([1.0, np.nan])).null_count == 1


def test_combine_unions_columns_and_promotes_types():
    a = pa.table({"x": pa.array([1, 2], pa.int64()), "y": ["a", "b"]})
    b = pa.table({"x": pa.array([1.5], pa.float64()), "z": [True]})
    out = p.combine([a, b])
    assert out.column_names == ["x", "y", "z"] and out.num_rows == 3
    assert out.schema.field("x").type == pa.float64()
    assert out.column("y").to_pylist() == ["a", "b", None]
    dec = pa.table({"x": pa.array([1], pa.decimal128(10, 2))})
    assert p.combine([dec, b]).schema.field("x").type == pa.float64()


def test_value_counts_orders_by_count_then_first_appearance():
    assert p.value_counts(np.array(["b", "a", "b", "c", "a", "b"])) == {"b": 3, "a": 2, "c": 1}
    assert p.value_counts(np.array([])) == {}


def test_pick_follows_weights():
    out = p.pick(np.random.default_rng(0), ["a", "b"], 5000, [9, 1])
    assert 0.85 < (out == "a").mean() < 0.95
    assert set(p.pick(np.random.default_rng(0), [1, 2, 3], 100)) <= {1, 2, 3}
