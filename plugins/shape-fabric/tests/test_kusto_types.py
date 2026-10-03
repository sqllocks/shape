"""Arrow to Kusto column types: unsigned 64-bit integers do not fit a long (#445)."""

from __future__ import annotations

import pyarrow as pa
import pytest
from shape_fabric.kusto import kusto_type


def test_uint64_is_a_decimal() -> None:
    assert kusto_type(pa.uint64()) == "decimal"
    assert kusto_type(pa.dictionary(pa.int8(), pa.uint64())) == "decimal"


@pytest.mark.parametrize(
    ("arrow", "kql"),
    [
        (pa.int8(), "int"),
        (pa.int32(), "int"),
        (pa.int64(), "long"),
        (pa.uint8(), "long"),
        (pa.uint32(), "long"),
    ],
)
def test_the_other_integers_are_unchanged(arrow: pa.DataType, kql: str) -> None:
    assert kusto_type(arrow) == kql
