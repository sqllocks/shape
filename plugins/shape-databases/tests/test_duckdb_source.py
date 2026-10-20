"""Native DuckDB source reads match the values and physical types actually stored."""

import pyarrow as pa
import pytest
from shape_databases import DuckDbSink
from shape_databases.duckdb_source import DuckDbSource

from shape.errors import ShapeError
from shape.plugins import kit


@pytest.mark.conformance
def test_duckdb_source_native_nested_roundtrip_and_source_v1(tmp_path):
    """SHAPE-SINK-003: read through the matching Source, without reconstruction."""
    uri = "duckdb:///" + str(tmp_path / "values.duckdb")
    table = pa.table(
        {
            "id": [1, 2, 3],
            "payload": pa.array(
                [
                    {"items": [1, None], "lookup": [("x", ["a", None])]},
                    {"items": [], "lookup": []},
                    None,
                ],
                pa.struct(
                    [
                        ("items", pa.list_(pa.int32())),
                        ("lookup", pa.map_(pa.string(), pa.list_(pa.string()))),
                    ]
                ),
            ),
        }
    )
    DuckDbSink().write(uri, "data", table.to_batches())
    source = DuckDbSource()
    target = uri + "?schema=main&table=data"
    kit.check_source(source, target)
    batches = list(source.read(target, batch_size=1))
    assert [batch.num_rows for batch in batches] == [1, 1, 1]
    actual = pa.Table.from_batches(batches)
    assert actual.schema == table.schema
    assert actual.equals(table)


def test_duckdb_source_empty_and_quoted_table(tmp_path):
    uri = "duckdb:///" + str(tmp_path / "values.duckdb")
    schema = pa.schema([("payload", pa.list_(pa.int32()))])
    DuckDbSink().write(uri, 'odd"table', [], schema=schema)
    source = DuckDbSource()
    assert source.schema(uri, table='odd"table') == schema
    assert sum(batch.num_rows for batch in source.read(uri, table='odd"table')) == 0


@pytest.mark.parametrize(
    "suffix,options",
    [
        ("", {}),
        ("?table=data&nested=json", {}),
        ("?table=data&password=secret", {}),
        ("?table=data", {"batch_size": 0}),
        ("?table=data", {"batch_size": True}),
        ("?table=data", {"unknown": "x"}),
        ("?table=data&table=other", {}),
    ],
)
def test_duckdb_invalid_source_options_fail_before_driver(tmp_path, monkeypatch, suffix, options):
    from shape_databases import duckdb_source

    monkeypatch.setattr(duckdb_source, "_driver", lambda: pytest.fail("driver loaded"))
    with pytest.raises(ShapeError):
        list(
            DuckDbSource().read("duckdb:///" + str(tmp_path / "absent.duckdb") + suffix, **options)
        )
    assert not (tmp_path / "absent.duckdb").exists()


def test_duckdb_source_missing_file_does_not_create_it(tmp_path):
    with pytest.raises(ShapeError, match="read"):
        list(DuckDbSource().read("duckdb:///" + str(tmp_path / "absent.duckdb") + "?table=data"))
    assert not (tmp_path / "absent.duckdb").exists()
