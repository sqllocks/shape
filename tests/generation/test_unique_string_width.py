"""Indexed width checks include UNIQUE columns without changing their nullability."""

import pyarrow as pa
import pytest

from shape.builtins.sinks.sql import SqlSink


@pytest.mark.parametrize("dialect,limit", [("tsql", 4000), ("tsql-fabric-warehouse", 8000)])
@pytest.mark.parametrize("width", [None, "oversize"])
def test_unique_string_width_refused_before_script_replacement(tmp_path, dialect, limit, width):
    path = tmp_path / "unchanged.sql"
    path.write_text("existing script")
    batch = pa.record_batch({"code": ["value"]})
    columns = {"code": {"max_length": limit + 1}} if width else {}
    with pytest.raises(ValueError, match="column 'code'.*max_length"):
        SqlSink().write(
            str(path), "t", [batch], sql_dialect=dialect, unique=[["code"]], columns=columns
        )
    assert path.read_text() == "existing script"


@pytest.mark.parametrize(
    "dialect,sql_type", [("tsql", "NVARCHAR(64)"), ("tsql-fabric-warehouse", "VARCHAR(64)")]
)
def test_declared_nullable_unique_stays_nullable(tmp_path, dialect, sql_type):
    path = tmp_path / "nullable.sql"
    batch = pa.record_batch({"code": [None, "value"]})
    SqlSink().write(
        str(path),
        "t",
        [batch],
        sql_dialect=dialect,
        unique=[["code"]],
        columns={"code": {"max_length": 64, "nullable": True}},
    )
    text = path.read_text()
    line = next(line for line in text.splitlines() if "[code]" in line and sql_type in line)
    assert line.rstrip(" ,").endswith("NULL") and "NOT NULL" not in line
    assert "UNIQUE" in text
