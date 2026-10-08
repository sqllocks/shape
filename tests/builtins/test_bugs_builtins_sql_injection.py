"""#285: line breaks must not let a name or value start a GO batch; PostgreSQL backslashes."""

from __future__ import annotations

import pyarrow as pa
import pytest

from shape.builtins.sinks.sql import SqlSink, _literal, _quote

EVIL = "x\nGO\nDROP TABLE dbo.victim\nGO\n--"


@pytest.mark.parametrize("dialect", ["tsql", "tsql-fabric-warehouse"])
def test_value_has_no_raw_line_break(tmp_path, dialect):
    table = pa.table({"id": [1, 2], "name": ["ok", EVIL]})
    SqlSink().write(f"{tmp_path}/", "customers", table.to_batches(), sql_dialect=dialect)
    lines = (tmp_path / "customers.sql").read_text().splitlines()
    assert "DROP TABLE dbo.victim" not in lines
    assert not [ln for ln in lines if ln.startswith("DROP TABLE dbo")]
    text = "\n".join(lines)
    assert "N'x' + NCHAR(10) + N'GO'" in text or "'x' + CHAR(10) + 'GO'" in text


def test_tsql_literal_uses_nchar():
    # a line break that would start a GO line is written as (N)CHAR (#285; #724 keeps every other
    # line break as it is)
    assert _literal("a\r\nGO\r\nb", "tsql") == (
        "N'a' + NCHAR(13) + NCHAR(10) + N'GO' + NCHAR(13) + NCHAR(10) + N'b'"
    )
    assert _literal("a\nGO\nb", "tsql-fabric") == "'a' + CHAR(10) + 'GO' + CHAR(10) + 'b'"


@pytest.mark.parametrize("dialect", ["tsql", "tsql-fabric-warehouse"])
def test_identifier_line_break_is_refused(dialect):
    # #724: a T-SQL name cannot hold a line break (refused rather than flattened, which could make
    # two names one)
    with pytest.raises(ValueError, match="line break"):
        _quote("a\nGO\nb", dialect)
    with pytest.raises(ValueError, match="line break"):
        _quote("a\r\nGO\r\nb", dialect)
    assert _quote("plain", "tsql") == "[plain]"
