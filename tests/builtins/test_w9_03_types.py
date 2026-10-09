"""W9-03: SQL Arrow widths, dimensions and UTC instants."""

import datetime as dt

import pyarrow as pa
import pytest

from shape.builtins.sinks.sql import _column_type, _literal


@pytest.mark.parametrize(
    "dialect,expected",
    [
        ("tsql", ["SMALLINT", "SMALLINT", "INT", "BIGINT"]),
        ("tsql-fabric-warehouse", ["SMALLINT", "SMALLINT", "INT", "BIGINT"]),
        ("postgres", ["SMALLINT", "SMALLINT", "INTEGER", "BIGINT"]),
        ("mysql", ["TINYINT", "SMALLINT", "INT", "BIGINT"]),
    ],
)
def test_wanted1_signed_widths(dialect, expected):
    for typ, sql in zip([pa.int8(), pa.int16(), pa.int32(), pa.int64()], expected, strict=True):
        assert _column_type(pa.field("value", typ), {}, dialect) == sql


@pytest.mark.parametrize("dialect", ["tsql", "tsql-fabric-warehouse", "postgres", "mysql"])
def test_wanted1_unsigned_decimal_uuid_and_float(dialect):
    for typ, sql in [
        (pa.uint8(), "TINYINT" if dialect == "tsql" else "SMALLINT"),
        (pa.uint16(), "INTEGER" if dialect == "postgres" else "INT"),
        (pa.uint32(), "BIGINT"),
    ]:
        assert _column_type(pa.field("value", typ), {}, dialect) == sql
    assert _column_type(pa.field("value", pa.uint64()), {}, dialect) == (
        "NUMERIC(20,0)" if dialect == "postgres" else "DECIMAL(20,0)"
    )
    assert _column_type(
        pa.field("value", pa.decimal128(12, 4)), {"precision": 18, "scale": 2}, dialect
    ) == ("NUMERIC(12,4)" if dialect == "postgres" else "DECIMAL(12,4)")
    uuid = {
        "tsql": "UNIQUEIDENTIFIER",
        "tsql-fabric-warehouse": "VARCHAR(36)",
        "postgres": "UUID",
        "mysql": "CHAR(36)",
    }
    assert (
        _column_type(pa.field("value", pa.string(), metadata={b"type": b"uuid"}), {}, dialect)
        == uuid[dialect]
    )
    assert _column_type(pa.field("value", pa.float32()), {}, dialect) == (
        "FLOAT" if dialect == "mysql" else "REAL"
    )


@pytest.mark.parametrize(
    "dialect,zoned,naive",
    [
        ("tsql", "DATETIMEOFFSET(6)", "DATETIME2(3)"),
        ("tsql-fabric-warehouse", "DATETIME2(6)", "DATETIME2(3)"),
        ("postgres", "TIMESTAMPTZ(6)", "TIMESTAMP(3)"),
        ("mysql", "TIMESTAMP(6)", "DATETIME(3)"),
    ],
)
def test_wanted1_timestamp_and_time_precision(dialect, zoned, naive):
    assert _column_type(pa.field("value", pa.timestamp("us", "Asia/Tokyo")), {}, dialect) == zoned
    assert _column_type(pa.field("value", pa.timestamp("ms")), {}, dialect) == naive
    assert _column_type(pa.field("value", pa.time32("s")), {}, dialect) == "TIME(0)"
    assert _column_type(pa.field("value", pa.time64("ns")), {}, dialect) == (
        "TIME(7)" if dialect == "tsql" else "TIME(6)"
    )


@pytest.mark.parametrize(
    "meta",
    [
        {},
        {"precision": 0},
        {"precision": 39, "scale": 2},
        {"precision": 12, "scale": 13},
        {"precision": True, "scale": 2},
    ],
)
def test_wanted1_declared_decimal_refuses_missing_or_invalid_dimensions(meta):
    with pytest.raises(ValueError, match="amount"):
        _column_type(pa.field("amount", pa.float64()), {"type": "decimal", **meta}, "tsql")


@pytest.mark.parametrize(
    "dialect,unbounded",
    [
        ("tsql", "NVARCHAR(MAX)"),
        ("tsql-fabric-warehouse", "VARCHAR(MAX)"),
        ("postgres", "TEXT"),
        ("mysql", "TEXT"),
    ],
)
def test_wanted2_declared_width_or_unbounded(dialect, unbounded):
    field = pa.field("text", pa.string())
    assert _column_type(field, {}, dialect) == unbounded
    assert _column_type(field, {"max_length": 1}, dialect).endswith("(1)")
    for bad in [0, -1, True, 1.5, "20); DROP TABLE x"]:
        with pytest.raises(ValueError, match="text"):
            _column_type(field, {"max_length": bad}, dialect)


def test_wanted3_script_zoned_instant_microsecond_boundary():
    value = dt.datetime(2020, 1, 1, 1, 2, 3, 999999, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    assert _literal(value, "postgres") == "'2019-12-31 16:02:03.999999+00:00'"
    assert _literal(value, "mysql") == "'2019-12-31 16:02:03.999999'"
    assert _literal(None, "postgres") == "NULL"


def test_wanted4_ddl_corrections_do_not_change_insert_bytes(tmp_path):
    from shape.builtins.sinks.sql import SqlSink

    batch = pa.RecordBatch.from_pydict({"id": [1], "text": ["a"]})
    paths = [tmp_path / "ddl.sql", tmp_path / "rows.sql"]
    for path, ddl in zip(paths, [True, False], strict=True):
        SqlSink().write(str(path), "items", [batch], ddl=ddl, go=False)
    scripts = [path.read_bytes() for path in paths]
    assert (
        scripts[0][scripts[0].index(b"INSERT INTO") :]
        == scripts[1][scripts[1].index(b"INSERT INTO") :]
    )
    assert "[text] NVARCHAR(MAX)" in " ".join(scripts[0].decode().split())


def test_wanted6_type_docs_and_default_change():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    docs = (root / "docs/SINKS.md").read_text()
    for term in ["SMALLINT", "TIMESTAMPTZ", "microsecond", "UTC", "VARCHAR(255)", "max_length"]:
        assert term in docs
    assert "unbounded" in (root / "CHANGELOG.md").read_text()


@pytest.mark.parametrize(
    "precision,dialect,supported",
    [
        (38, "tsql", True),
        (39, "tsql", False),
        (38, "tsql-fabric-warehouse", True),
        (39, "tsql-fabric-warehouse", False),
        (65, "mysql", True),
        (66, "mysql", False),
        (76, "postgres", True),
    ],
)
def test_wanted1_decimal256_precision_limits(precision, dialect, supported):
    field = pa.field("amount", pa.decimal256(precision, 4))
    if supported:
        assert _column_type(field, {}, dialect).endswith(f"({precision},4)")
    else:
        with pytest.raises(ValueError, match="amount"):
            _column_type(field, {}, dialect)


def test_wanted1_postgres_negative_and_excess_scale():
    assert (
        _column_type(pa.field("amount", pa.decimal128(12, -2)), {}, "postgres") == "NUMERIC(12,-2)"
    )
    assert _column_type(pa.field("amount", pa.decimal128(5, 8)), {}, "postgres") == "NUMERIC(5,8)"
    with pytest.raises(ValueError, match="amount"):
        _column_type(pa.field("amount", pa.decimal128(12, -2)), {}, "tsql")


def test_wanted3_mysql_script_sets_utc_session(tmp_path):
    from shape.builtins.sinks.sql import SqlSink

    batch = pa.RecordBatch.from_arrays(
        [pa.array([0, None], type=pa.timestamp("us", "Asia/Tokyo"))], names=["at"]
    )
    target = tmp_path / "rows.sql"
    SqlSink().write(str(target), "items", [batch], sql_dialect="mysql")
    script = target.read_text()
    assert "SET time_zone = '+00:00';" in script
    assert "TIMESTAMP(6)" in script
    assert "'1970-01-01 00:00:00'" in script
    assert "NULL" in script
