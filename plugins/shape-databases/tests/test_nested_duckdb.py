"""W9-06 DuckDB recursive types and native Arrow read-back, without a service."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import duckdb
import pyarrow as pa
import pytest
from shape_databases import DuckDbSink
from shape_databases.duckdb_sink import duckdb_type

from shape.errors import ShapeError


@pytest.mark.parametrize(
    "kind,expected",
    [
        (pa.list_(pa.int16()), "SMALLINT[]"),
        (pa.large_list(pa.decimal128(12, 4)), "DECIMAL(12,4)[]"),
        (pa.list_(pa.string(), 2), "VARCHAR[]"),
        (pa.list_(pa.list_(pa.int32())), "INTEGER[][]"),
        (pa.struct([('a"b', pa.int32())]), 'STRUCT("a""b" INTEGER)'),
        (pa.map_(pa.string(), pa.int64()), "MAP(VARCHAR, BIGINT)"),
        (
            pa.map_(pa.string(), pa.list_(pa.struct([("x", pa.int8())]))),
            'MAP(VARCHAR, STRUCT("x" TINYINT)[])',
        ),
    ],
)
def test_recursive_native_types(kind, expected):
    assert duckdb_type(pa.field("payload", kind), {}) == expected


def nested_table():
    return pa.table(
        {
            "id": pa.array([1, 2, 3, 4], pa.int32()),
            "tags": pa.array([[1, None, 2], [], None, [3]], pa.list_(pa.int16())),
            "item": pa.array(
                [
                    {"code": "x", "count": 1},
                    {"code": None, "count": None},
                    None,
                    {"code": "", "count": 0},
                ],
                pa.struct([("code", pa.string()), ("count", pa.int32())]),
            ),
            "items": pa.array(
                [[{"x": 1}, None], [], None, [{"x": None}]],
                pa.list_(pa.struct([("x", pa.int16())])),
            ),
            "lookup": pa.array(
                [[("z", 2), ("a", None)], [], None, [("k", 0)]],
                pa.map_(pa.string(), pa.int64()),
            ),
        }
    )


def query_table(path):
    with duckdb.connect(str(path)) as con:
        con.execute("SET TimeZone='UTC'")
        return con.execute('SELECT * FROM "data" ORDER BY "id"').to_arrow_table()


@pytest.mark.conformance
def test_native_shapes_read_back_as_equal_arrow_values(tmp_path):
    """SHAPE-SINK-003: null/empty containers, null children and map ordering survive."""
    expected = nested_table()
    path = tmp_path / "nested.duckdb"
    assert (
        DuckDbSink().write(f"duckdb:///{path}", "data", expected.to_batches(max_chunksize=2)) == 4
    )
    actual = query_table(path)
    assert actual.cast(expected.schema).to_pylist() == expected.to_pylist()
    with duckdb.connect(str(path)) as con:
        types = dict(
            con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name='data'"
            ).fetchall()
        )
    assert types["tags"] == "SMALLINT[]"
    assert types["item"] == "STRUCT(code VARCHAR, count INTEGER)"
    assert types["items"] == "STRUCT(x SMALLINT)[]"
    assert types["lookup"] == "MAP(VARCHAR, BIGINT)"


def test_empty_native_table_uses_declared_nested_schema(tmp_path):
    expected = nested_table().slice(0, 0)
    path = tmp_path / "empty.duckdb"
    assert DuckDbSink().write(f"duckdb:///{path}", "data", [], schema=expected.schema) == 0
    assert query_table(path).cast(expected.schema).equals(expected)


def test_duckdb_distinct_non_ascii_struct_names_remain_distinct(tmp_path):
    schema = pa.schema(
        [("id", pa.int32()), ("payload", pa.struct([("Ä", pa.int16()), ("ä", pa.int16())]))]
    )
    expected = pa.Table.from_pylist([{"id": 1, "payload": {"Ä": 1, "ä": 2}}], schema=schema)
    path = tmp_path / "non-ascii.duckdb"
    assert DuckDbSink().write(f"duckdb:///{path}", "data", expected.to_batches()) == 1
    assert query_table(path).cast(schema).equals(expected)


@pytest.mark.parametrize("via_uri", [False, True])
def test_explicit_json_mode_keeps_nested_values_as_json_text(tmp_path, via_uri):
    expected = nested_table()
    path = tmp_path / "json.duckdb"
    uri = f"duckdb:///{path}" + ("?nested=json" if via_uri else "")
    options = {} if via_uri else {"nested": "json"}
    assert DuckDbSink().write(uri, "data", expected.to_batches(), **options) == 4
    actual = query_table(path)
    assert actual.column("tags").type == pa.string()
    assert [
        None if value is None else json.loads(value) for value in actual.column("tags").to_pylist()
    ] == [[1, None, 2], [], None, [3]]
    assert json.loads(actual.column("item")[0].as_py()) == {"code": "x", "count": 1}
    assert json.loads(actual.column("lookup")[0].as_py()) == [["z", 2], ["a", None]]
    assert actual.column("item")[2].as_py() is None


def test_json_fallback_preserves_decimal_zone_and_non_string_map_keys(tmp_path):
    zoned = dt.datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=dt.timezone(dt.timedelta(hours=5)))
    dtype = pa.struct(
        [
            ("amount", pa.decimal128(12, 4)),
            ("at", pa.timestamp("us", "Asia/Karachi")),
            ("lookup", pa.map_(pa.int16(), pa.string())),
        ]
    )
    expected = pa.table(
        {
            "id": [1],
            "payload": pa.array(
                [{"amount": Decimal("-1.2300"), "at": zoned, "lookup": [(2, "two"), (1, None)]}],
                dtype,
            ),
        }
    )
    path = tmp_path / "json-values.duckdb"
    assert (
        DuckDbSink().write(f"duckdb:///{path}", "data", expected.to_batches(), nested="json") == 1
    )
    actual = json.loads(query_table(path).column("payload")[0].as_py())
    assert actual == {
        "amount": "-1.2300",
        "at": "2026-01-02 03:04:05.123456+05:00",
        "lookup": [[2, "two"], [1, None]],
    }


def test_empty_struct_is_available_through_explicit_json_fallback(tmp_path):
    expected = pa.table({"id": [1, 2], "payload": pa.array([{}, None], pa.struct([]))})
    path = tmp_path / "empty-struct.duckdb"
    assert (
        DuckDbSink().write(f"duckdb:///{path}", "data", expected.to_batches(), nested="json") == 2
    )
    assert query_table(path).column("payload").to_pylist() == ["{}", None]


@pytest.mark.parametrize(
    "kind",
    [
        pa.list_(pa.duration("s")),
        pa.struct([]),
        pa.struct([("a", pa.int8()), ("A", pa.int8())]),
    ],
)
def test_unrepresentable_native_shape_fails_before_any_connection(tmp_path, monkeypatch, kind):
    from shape_databases import duckdb_sink

    def forbidden():
        raise AssertionError("driver loaded before nested validation")

    monkeypatch.setattr(duckdb_sink, "_driver", forbidden)
    with pytest.raises(ShapeError, match="payload.*nested=json"):
        DuckDbSink().write(
            f"duckdb:///{tmp_path / 'uncreated.duckdb'}",
            "data",
            [],
            schema=pa.schema([("payload", kind)]),
        )
    assert not (tmp_path / "uncreated.duckdb").exists()


def test_invalid_nested_option_fails_before_any_connection(tmp_path, monkeypatch):
    from shape_databases import duckdb_sink

    monkeypatch.setattr(duckdb_sink, "_driver", lambda: pytest.fail("driver loaded"))
    with pytest.raises(ShapeError, match="nested"):
        DuckDbSink().write(
            f"duckdb:///{tmp_path / 'uncreated.duckdb'}",
            "data",
            [],
            schema=pa.schema([("id", pa.int32())]),
            nested="invalid",
        )


@pytest.mark.parametrize("mode", ["native", "json"])
def test_exact_duplicate_struct_names_refuse_before_connection_with_rename_alternative(
    tmp_path, monkeypatch, mode
):
    from shape_databases import duckdb_sink

    monkeypatch.setattr(duckdb_sink, "_driver", lambda: pytest.fail("driver loaded"))
    schema = pa.schema([("payload", pa.list_(pa.struct([("x", pa.int32()), ("x", pa.int32())])))])
    with pytest.raises(ShapeError, match="payload.*rename.*distinct") as error:
        DuckDbSink().write(
            f"duckdb:///{tmp_path / 'duplicates.duckdb'}", "data", [], schema=schema, nested=mode
        )
    assert "use nested=json" not in str(error.value)


def test_nested_temporal_values_use_the_documented_microsecond_normalization(tmp_path):
    value = dt.datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=dt.timezone(dt.timedelta(hours=5)))
    dtype = pa.map_(pa.string(), pa.list_(pa.timestamp("ns", "Asia/Karachi")))
    expected = pa.table({"id": [1], "lookup": pa.array([[("time", [value, None])]], dtype)})
    path = tmp_path / "zones.duckdb"
    assert DuckDbSink().write(f"duckdb:///{path}", "data", expected.to_batches()) == 1
    actual = query_table(path)
    assert actual.column("lookup").to_pylist()[0][0][1][0] == value.astimezone(dt.UTC)
