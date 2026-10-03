"""AUD-gen: incremental (``continue``, time travel) keeps the input's types (#171)."""

from __future__ import annotations

from decimal import Decimal

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from shape.generation.incremental import ContinueConfig, ContinueEngine


@pytest.mark.parametrize(
    ("values", "type_"),
    [([120, 125, 127], pa.int8()), ([250, 252, 255], pa.uint8()), ([2**63 - 2] * 3, pa.int64())],
)
def test_continue_does_not_wrap_small_integer_columns(values, type_):
    # 171: int8 [120, 125, 127] came out as [-125, 124, -127, ...] (astype wrapped).
    t = pa.table({"t_id": pa.array([1, 2, 3]), "q": pa.array(values, type_)})
    d = ContinueEngine().continue_from(
        {"t": t},
        config=ContinueConfig(seed=3, insert_count=6, update_fraction=1.0, delete_fraction=0),
    )
    low = min(values) * 0.9 - 1
    for part in (d.inserts["t"], d.updates["t"]):
        assert part.schema.field("q").type == type_
        assert all(v >= low for v in part["q"].to_pylist())


def test_continue_keeps_decimals_inside_their_precision():
    # 171: decimal128(5,2) 999.99 scaled above the type became 0.00 (cast with safe=False).
    m = pa.array([Decimal("999.99")] * 3, pa.decimal128(5, 2))
    t = pa.table({"t_id": pa.array([1, 2, 3]), "m": m})
    d = ContinueEngine().continue_from(
        {"t": t}, config=ContinueConfig(seed=3, insert_count=6, delete_fraction=0)
    )
    got = d.inserts["t"]["m"].to_pylist()
    assert all(Decimal("899") <= v <= Decimal("999.99") for v in got), got


@pytest.mark.parametrize(
    ("values", "transitions", "moved"),
    [
        ([1, 1, 2, 3], {"1": {"5": 1.0}}, [5, 5, 2, 3]),
        ([True, True, False, None], {"true": {"false": 1.0}}, [False, False, False, None]),
        (["a", "a", "b", None], {"a": {"c": 1.0}}, ["c", "c", "b", None]),
    ],
)
def test_state_transitions_keep_the_column_type(values, transitions, moved):
    # 189: new states were strings mixed with the untouched values:
    # ArrowTypeError: Expected bytes, got a 'int' object.
    t = pa.table({"t_id": pa.array([1, 2, 3, 4]), "status": pa.array(values)})
    d = ContinueEngine().continue_from(
        {"t": t},
        config=ContinueConfig(
            seed=1,
            insert_count=0,
            delete_fraction=0,
            update_fraction=1.0,
            state_transitions={"t.status": transitions},
        ),
    )
    status = d.updates["t"].sort_by("t_id")["status"]
    assert status.type == t["status"].type
    assert status.to_pylist() == moved
