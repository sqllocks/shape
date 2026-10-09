"""T-SQL column types from a declared max_length and from decimals (#446)."""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest
from shape_fabric._tsql import column_type

from shape.errors import ShapeError

TEXT = pa.field("c", pa.string())


@pytest.mark.parametrize(
    ("length", "warehouse", "sql"),
    [
        (100, False, "NVARCHAR(100)"),
        (4000, False, "NVARCHAR(4000)"),
        (4001, False, "NVARCHAR(MAX)"),
        ("5000", False, "NVARCHAR(MAX)"),
        (8000, True, "VARCHAR(8000)"),
    ],
)
def test_a_declared_length_fits_the_type(length: Any, warehouse: bool, sql: str) -> None:
    assert column_type(TEXT, {"max_length": length}, warehouse=warehouse) == sql


def test_a_synapse_columnstore_string_longer_than_8000_is_refused() -> None:
    with pytest.raises(ShapeError, match="8000"):
        column_type(TEXT, {"max_length": 9000}, warehouse=True, synapse=True)


@pytest.mark.parametrize("length", ["max", 1.5, -3, "ten", True])
def test_a_length_that_is_not_a_positive_whole_number_is_a_shape_error(length: Any) -> None:
    with pytest.raises(ShapeError, match="max_length"):
        column_type(TEXT, {"max_length": length}, warehouse=False)


def test_a_negative_decimal_scale_is_a_shape_error() -> None:
    with pytest.raises(ShapeError, match="scale"):
        column_type(pa.field("d", pa.decimal128(5, -2)), warehouse=False)
