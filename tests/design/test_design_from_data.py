"""W5-02 item 5: a design input built from data, with the existing FD and key discovery."""

from __future__ import annotations

import random
from typing import Any

import pytest

from shape.design import DesignError, DesignInput
from shape.design.engine import derive
from shape.design.from_data import design_from_rows


def orders(n: int = 60) -> list[dict[str, Any]]:
    rng = random.Random(7)
    regions = {"Oslo": "North", "Bergen": "West", "Turku": "North", "Lyon": "South"}
    rows = []
    for i in range(n):
        city = rng.choice(sorted(regions))
        rows.append(
            {
                "order_id": i + 1,
                "city": city,
                "region": regions[city],
                "qty": rng.randint(1, 9),
                "note": None if i % 5 == 0 else f"n{i}",
            }
        )
    return rows


def test_keys_and_dependencies_come_from_the_data() -> None:
    design = design_from_rows(orders(), name="orders")
    ent = design.entity("orders")
    assert ent.keys == (("order_id",),)
    deps = {(d.determinant, d.dependent) for d in ent.dependencies}
    assert (("city",), ("region",)) in deps
    # A key determines everything, so it is not listed as a dependency.
    assert not any(d.determinant == ("order_id",) for d in ent.dependencies)
    # region does not determine city (North has Oslo and Turku).
    assert (("region",), ("city",)) not in deps


def test_types_nullability_and_lengths_are_inferred() -> None:
    ent = design_from_rows(orders(), name="orders").entity("orders")
    by = {a.name: a for a in ent.attributes}
    assert by["order_id"].type == "integer" and by["order_id"].nullable is False
    assert by["city"].type == "string" and by["city"].max_length == 6
    assert by["note"].nullable is True
    assert [a.name for a in ent.attributes] == ["order_id", "city", "region", "qty", "note"]


def test_the_result_feeds_the_engine() -> None:
    design = design_from_rows(orders(), name="orders")
    assert DesignInput.from_dict(design.to_dict()) == design
    names = {t.name for t in derive(design, "3nf").tables}
    assert names == {"orders", "orders_city"}


def test_approximate_dependencies_are_not_reported() -> None:
    rows = orders()
    rows[3]["region"] = "Elsewhere"  # one violation of city -> region
    deps = design_from_rows(rows, name="orders").entity("orders").dependencies
    assert (("city",), ("region",)) not in {(d.determinant, d.dependent) for d in deps}


def test_composite_keys_are_found() -> None:
    rows = [{"a": i % 4, "b": i // 4, "v": i} for i in range(16)]
    ent = design_from_rows(rows, name="t", max_key_size=2).entity("t")
    assert ("a", "b") in ent.keys and ("v",) in ent.keys


def test_few_rows_do_not_invent_dependencies() -> None:
    # Every value is distinct, so every column is a key and nothing is "determined" by evidence.
    rows = [{"a": i, "b": i * 2, "c": i * 3} for i in range(5)]
    assert design_from_rows(rows, name="t").entity("t").dependencies == ()


def test_several_tables_become_several_entities() -> None:
    d = design_from_rows({"a": orders(10), "b": orders(12)}, name="two")
    assert [e.name for e in d.entities] == ["a", "b"]


def test_empty_data_is_an_error() -> None:
    with pytest.raises(DesignError, match="no rows"):
        design_from_rows([], name="t")


def test_unnameable_column_set_is_rejected() -> None:
    with pytest.raises(DesignError, match="duplicate|empty"):
        design_from_rows({}, name="t")


def test_other_value_types_are_inferred() -> None:
    import datetime as dt
    from decimal import Decimal

    rows = [
        {
            "d": dt.date(2024, 1, i + 1),
            "t": dt.datetime(2024, 1, 1, i),
            "m": Decimal(f"{i}.25"),
            "f": i + 0.5,
            "b": bool(i % 2),
            "mixed": i if i % 2 else "x",
        }
        for i in range(6)
    ]
    by = {a.name: a for a in design_from_rows(rows, name="t").entity("t").attributes}
    assert [by[k].type for k in ("d", "t", "m", "f", "b", "mixed")] == [
        "date",
        "timestamp",
        "decimal",
        "float",
        "boolean",
        "string",
    ]
    assert by["m"].scale == 2
