"""W9-06 Delta nested maps, native source reads and explicit JSON fallback."""

from __future__ import annotations

import datetime as dt
import json

import pyarrow as pa
import pytest

from shape.builtins.sinks.delta import DeltaSink, _utc_timestamps
from shape.builtins.sources.delta import DeltaSource
from shape.errors import ShapeError


def nested_table():
    kind = pa.map_(
        pa.string(),
        pa.struct([("at", pa.timestamp("us")), ("items", pa.list_(pa.int32()))]),
    )
    return pa.table(
        {
            "id": [1, 2, 3],
            "payload": pa.array(
                [
                    [
                        ("a", {"at": dt.datetime(2026, 1, 2, 3, 4, 5, 123456), "items": [1, None]}),
                        ("b", None),
                    ],
                    [],
                    None,
                ],
                kind,
            ),
        }
    )


def read(path):
    source = DeltaSource()
    assert source.can_open(str(path))
    table = pa.Table.from_batches(list(source.read(str(path))), schema=source.schema(str(path)))
    return table.sort_by("id") if "id" in table.schema.names else table


@pytest.mark.conformance
def test_delta_native_map_utc_recursion_and_readback_preserve_nulls_and_containers(tmp_path):
    """SHAPE-SINK-003: map values use the existing recursive UTC rewrite."""
    table = nested_table()
    assert DeltaSink().write(str(tmp_path), "data", table.to_batches(max_chunksize=2)) == 3
    actual = read(tmp_path / "data")
    expected = table.cast(_utc_timestamps(table.schema))
    assert actual.column("payload").type == expected.column("payload").type
    assert actual.to_pylist() == expected.to_pylist()


def test_delta_native_map_duplicate_entries_are_preserved_where_representable(tmp_path):
    expected = pa.table(
        {"payload": pa.array([[("x", 1), ("x", 2)], [], None], pa.map_(pa.string(), pa.int32()))}
    )
    assert DeltaSink().write(str(tmp_path), "data", expected.to_batches()) == 3
    actual = read(tmp_path / "data")
    assert actual.column("payload").type == expected.column("payload").type
    assert actual.to_pylist() == expected.to_pylist()


@pytest.mark.conformance
@pytest.mark.parametrize("zone", [None, "UTC", "America/New_York"])
def test_delta_nanosecond_map_timestamps_match_scalar_and_list_microsecond_normalization(
    tmp_path, zone
):
    """SHAPE-SINK-003: compare native Source integers after Delta's explicit normalization."""
    nanos = 1767323045123456789
    times = pa.array([nanos], pa.int64()).cast(pa.timestamp("ns", zone))
    expected = pa.table(
        {
            "scalar": times,
            "list": pa.ListArray.from_arrays([0, 1], times),
            "map": pa.MapArray.from_arrays([0, 1], pa.array(["at"]), times),
        }
    )
    assert DeltaSink().write(str(tmp_path), "data", expected.to_batches()) == 1
    actual = read(tmp_path / "data")
    scalar = actual.column("scalar").combine_chunks()
    list_items = actual.column("list").combine_chunks().values
    map_values = actual.column("map").combine_chunks().items
    for values in (scalar, list_items, map_values):
        assert values.type == pa.timestamp("us", "UTC")
        assert values.cast(pa.int64()).to_pylist() == [1767323045123456]


def test_delta_json_mode_stores_json_text_and_retains_null_vs_empty(tmp_path):
    table = nested_table()
    assert DeltaSink().write(str(tmp_path), "data", table.to_batches(), nested="json") == 3
    actual = read(tmp_path / "data")
    assert actual.column("payload").type == pa.string()
    values = actual.column("payload").to_pylist()
    assert json.loads(values[0]) == [
        ["a", {"at": "2026-01-02 03:04:05.123456", "items": [1, None]}],
        ["b", None],
    ]
    assert values[1:] == ["[]", None]


def test_delta_streaming_json_mode_and_empty_schema(tmp_path):
    table = nested_table()
    writer = DeltaSink().open_table(
        str(tmp_path), "stream", table.schema, nested="json", commit_rows=1
    )
    for batch in table.to_batches(max_chunksize=1):
        writer.write_batch(batch)
    assert writer.close() == 3
    assert read(tmp_path / "stream").column("payload").type == pa.string()
    assert DeltaSink().write(str(tmp_path), "empty", [], schema=table.schema, nested="json") == 0
    assert read(tmp_path / "empty").num_rows == 0
    assert read(tmp_path / "empty").column("payload").type == pa.string()


@pytest.mark.parametrize("streaming", [False, True])
def test_invalid_delta_nested_option_fails_before_storage_or_writer_loading(
    tmp_path, monkeypatch, streaming
):
    from shape.builtins.sinks import delta

    monkeypatch.setattr(delta, "_location", lambda *a, **k: pytest.fail("storage accessed"))
    monkeypatch.setattr(delta, "_deltalake", lambda: pytest.fail("writer loaded"))
    table = nested_table()
    with pytest.raises(ShapeError, match="nested"):
        if streaming:
            DeltaSink().open_table(str(tmp_path), "data", table.schema, nested="invalid")
        else:
            DeltaSink().write(str(tmp_path), "data", table.to_batches(), nested="invalid")


@pytest.mark.parametrize("kind", [pa.struct([]), pa.list_(pa.duration("s")), pa.list_(pa.uint64())])
def test_unrepresentable_delta_nested_shape_names_column_and_json_alternative_before_storage(
    tmp_path, monkeypatch, kind
):
    from shape.builtins.sinks import delta

    monkeypatch.setattr(delta, "_location", lambda *a, **k: pytest.fail("storage accessed"))
    with pytest.raises(ShapeError, match="payload.*nested=json"):
        DeltaSink().write(str(tmp_path), "data", [], schema=pa.schema([("payload", kind)]))
