"""ADO.NET connection strings to ODBC: nothing is weakened or silently lost (#449)."""

from __future__ import annotations

import pytest
from shape_fabric._tsql import normalize_connection_string

from shape.errors import ShapeError

BASE = "Server=tcp:h.database.windows.net,1433;Initial Catalog=d"


@pytest.mark.parametrize("value", ["Strict", "strict", "STRICT"])
def test_encrypt_strict_is_kept(value: str) -> None:
    out = normalize_connection_string(f"{BASE};Encrypt={value}")
    assert "Encrypt=strict" in out and "Encrypt=yes" not in out


def test_application_name_and_mars_become_their_odbc_keywords() -> None:
    out = normalize_connection_string(
        f"{BASE};Application Name=loader;MultipleActiveResultSets=True"
    )
    assert "APP=loader" in out and "MARS_Connection=yes" in out


def test_a_timeout_that_is_not_a_number_is_a_shape_error() -> None:
    with pytest.raises(ShapeError, match="timeout"):
        normalize_connection_string(f"{BASE};Connect Timeout=soon")


def test_the_portal_string_is_unchanged() -> None:
    out = normalize_connection_string(
        f"{BASE};Encrypt=True;TrustServerCertificate=False;Connection Timeout=30"
    )
    assert "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30" in out
