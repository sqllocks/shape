"""Shared SQL helpers: quoting, connection strings, redaction, type maps."""

import pytest
from shape_sqlserver import sql

pytestmark = pytest.mark.contract


def test_quote_ident_doubles_the_closing_bracket():
    assert sql.quote_ident("orders") == "[orders]"
    assert sql.quote_ident("a]; DROP TABLE x;--") == "[a]]; DROP TABLE x;--]"
    assert sql.qualified_name("dbo", "my table") == "[dbo].[my table]"


@pytest.mark.parametrize("bad", ["", None, "a\x00b", 5])
def test_quote_ident_rejects_unusable_names(bad):
    with pytest.raises(sql.SqlServerError):
        sql.quote_ident(bad)


def test_top_query_validates_the_row_limit_and_quotes_names():
    assert sql.top_query("dbo", "t", 5) == "SELECT TOP 5 * FROM [dbo].[t]"
    assert sql.top_query("s", "t", 2, ["a", "b]c"]) == "SELECT TOP 2 [a], [b]]c] FROM [s].[t]"
    for bad in (0, -1, True, "5; DROP TABLE t", 1.5):
        with pytest.raises(sql.SqlServerError):
            sql.top_query("dbo", "t", bad)  # type: ignore[arg-type]


def test_build_connection_string():
    text = sql.build_connection_string("db.example.test,1433", "shape", user="sa", password="p;w=d")
    assert "Server=db.example.test,1433" in text
    assert "Database=shape" in text
    assert "PWD={p;w=d}" in text  # a value with ; or = is braced
    assert text.startswith("Driver={ODBC Driver 18 for SQL Server};")
    assert "Encrypt=yes" in text and "TrustServerCertificate=no" in text
    with pytest.raises(sql.SqlServerError):
        sql.build_connection_string("")


def test_redact_hides_passwords_and_tokens_only():
    text = "Server=s;UID=sa;PWD={a;b};Database=d;password=x y;AccessToken=abc"
    red = sql.redact_connection_string(text)
    assert "a;b" not in red and "x y" not in red and "abc" not in red
    assert "Server=s" in red and "UID=sa" in red and "Database=d" in red
    assert sql.redact_connection_string("nothing secret here") == "nothing secret here"


@pytest.mark.parametrize(
    ("type_name", "dtype"),
    [
        ("int", "integer"),
        ("BIGINT", "integer"),
        ("tinyint", "integer"),
        ("decimal", "float"),
        ("money", "float"),
        ("real", "float"),
        ("date", "date"),
        ("datetime2", "datetime"),
        ("datetimeoffset", "datetime"),
        ("bit", "boolean"),
        ("varchar", "string"),
        ("uniqueidentifier", "string"),
        ("time", "string"),
        ("varbinary", "string"),
        ("xml", "string"),
    ],
)
def test_sql_type_to_dtype(type_name, dtype):
    assert sql.sql_type_to_dtype(type_name) == dtype


def test_sql_type_to_arrow():
    import pyarrow as pa

    assert sql.sql_type_to_arrow("int") == pa.int32()
    assert sql.sql_type_to_arrow("decimal", 12, 2) == pa.decimal128(12, 2)
    assert sql.sql_type_to_arrow("money") == pa.decimal128(19, 4)
    assert sql.sql_type_to_arrow("datetimeoffset") == pa.timestamp("us", tz="UTC")
    assert sql.sql_type_to_arrow("nvarchar") == pa.string()
    assert sql.sql_type_to_arrow("rowversion") == pa.binary()


def test_datetimeoffset_bytes_decode_to_the_utc_instant():
    import datetime as dt
    import struct

    raw = struct.pack("<6hI2h", 2024, 3, 5, 10, 30, 15, 123_456_700, -5, -30)
    assert sql.datetimeoffset_from_bytes(raw) == dt.datetime(
        2024, 3, 5, 16, 0, 15, 123456, tzinfo=dt.UTC
    )


def test_register_converters_only_touches_connections_that_support_it():
    seen = []

    class Conn:
        def add_output_converter(self, code, func):
            seen.append((code, func))

    sql.register_converters(Conn())
    assert seen == [(-155, sql.datetimeoffset_from_bytes)]
    sql.register_converters(object())  # a double without the method: no error


def test_spread_query_refuses_a_bad_row_limit_or_no_columns():
    with pytest.raises(sql.SqlServerError, match="positive integer"):
        sql.spread_query("dbo", "t", 0, ["id"])
    with pytest.raises(sql.SqlServerError, match="positive integer"):
        sql.spread_query("dbo", "t", True, ["id"])
    with pytest.raises(sql.SqlServerError, match="at least one column"):
        sql.spread_query("dbo", "t", 10, [])
