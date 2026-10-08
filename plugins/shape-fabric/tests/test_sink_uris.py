"""sql-database:// and warehouse:// URIs: the server is host[,port], never user info (#415)."""

from __future__ import annotations

import pytest
from shape_fabric.sinks import connection_string_for

from shape.errors import ShapeError


@pytest.mark.parametrize("scheme", ["sql-database", "warehouse"])
def test_a_port_becomes_the_odbc_host_comma_port(scheme: str) -> None:
    cs = connection_string_for(f"{scheme}://h.example.net:1433/db", {}, scheme)
    assert cs is not None
    assert "Server=h.example.net,1433;" in cs and "Database=db;" in cs


def test_without_a_port_the_server_is_the_host() -> None:
    cs = connection_string_for("warehouse://h.example.net/wh", {}, "warehouse")
    assert cs is not None and "Server=h.example.net;" in cs


@pytest.mark.parametrize("userinfo", ["user:s3cretpw@", "user@"])
def test_user_info_in_the_uri_is_refused_without_echoing_it(userinfo: str) -> None:
    uri = f"sql-database://{userinfo}h.example.net:1433/db"
    with pytest.raises(ShapeError, match="credential") as err:
        connection_string_for(uri, {}, "sql-database")
    assert "s3cretpw" not in str(err.value)


def test_a_bad_port_is_a_shape_error() -> None:
    with pytest.raises(ShapeError, match="port"):
        connection_string_for("sql-database://h.example.net:99999/db", {}, "sql-database")
