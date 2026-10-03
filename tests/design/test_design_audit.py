"""Regression tests for the AUD-design findings in the design engine and its input."""

from __future__ import annotations

from typing import Any

import pytest

from shape.design import DesignError, DesignInput
from shape.design.engine import derive
from shape.design.lint import lint


def _doc(entities: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return {"format": "shape-design", "version": 1, "name": "x", "entities": entities, **kw}


def _sale(*extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "sale",
        "attributes": [{"name": "id", "type": "integer"}, *extra],
        "keys": [["id"]],
    }


# ---- issue 384: colliding table names in star and snowflake ---------------------------------


def _date_entity_doc() -> dict[str, Any]:
    return _doc(
        [
            {
                "name": "date",
                "attributes": [{"name": "date_id", "type": "integer"}, {"name": "d"}],
                "keys": [["date_id"]],
            },
            _sale(
                {"name": "date_id", "type": "integer", "references": "date"},
                {"name": "when", "type": "date"},
            ),
        ],
        facts=[
            {
                "name": "sales",
                "source": "sale",
                "grain": ["id"],
                "degenerate": ["id"],
                "dimensions": [{"entity": "date", "via": "date_id"}],
                "dates": ["when"],
            }
        ],
    )


def _case_doc() -> dict[str, Any]:
    return _doc(
        [
            {"name": "Customer", "attributes": [{"name": "id"}], "keys": [["id"]]},
            {"name": "customer", "attributes": [{"name": "cid"}], "keys": [["cid"]]},
            _sale(
                {"name": "c1", "references": "Customer"}, {"name": "c2", "references": "customer"}
            ),
        ],
        facts=[
            {
                "name": "s",
                "source": "sale",
                "grain": ["id"],
                "degenerate": ["id"],
                "dimensions": [
                    {"entity": "Customer", "via": "c1", "role": "a"},
                    {"entity": "customer", "via": "c2", "role": "b"},
                ],
            }
        ],
    )


def _two_facts_doc() -> dict[str, Any]:
    fact = {"source": "sale", "grain": ["id"], "degenerate": ["id"]}
    return _doc([_sale()], facts=[{"name": "Sales", **fact}, {"name": "sales", **fact}])


@pytest.mark.parametrize("make", [_date_entity_doc, _case_doc, _two_facts_doc])
@pytest.mark.parametrize("mode", ["star", "snowflake"])
def test_colliding_table_names_are_refused_not_overwritten(make: Any, mode: str) -> None:
    design = DesignInput.from_dict(make())
    with pytest.raises(DesignError, match="duplicate name"):
        derive(design, mode)
    assert any(f.code == "G001" for f in lint(design, mode))


def test_an_outrigger_and_an_entity_with_the_same_table_name_are_refused() -> None:
    doc = _doc(
        [
            {
                "name": "geo",
                "attributes": [{"name": "id"}, {"name": "city"}, {"name": "region"}],
                "keys": [["id"]],
            },
            {"name": "geo_region", "attributes": [{"name": "rid"}], "keys": [["rid"]]},
            _sale({"name": "g", "references": "geo"}, {"name": "r", "references": "geo_region"}),
        ],
        hierarchies=[{"name": "h", "entity": "geo", "levels": ["city", "region"]}],
        facts=[
            {
                "name": "s",
                "source": "sale",
                "grain": ["id"],
                "degenerate": ["id"],
                "dimensions": [
                    {"entity": "geo", "via": "g"},
                    {"entity": "geo_region", "via": "r"},
                ],
            }
        ],
    )
    design = DesignInput.from_dict(doc)
    derive(design, "star")  # no outrigger in a star: no collision
    with pytest.raises(DesignError, match="dim_geo_region"):
        derive(design, "snowflake")


# ---- issue 385: a self-reference keeps its foreign key in 3NF --------------------------------


def test_a_self_reference_gets_its_foreign_key() -> None:
    from shape.design.ddl import emit_ddl
    from shape.design.result import ForeignKey

    design = DesignInput.from_dict(
        _doc(
            [
                {
                    "name": "emp",
                    "attributes": [{"name": "id"}, {"name": "mgr", "references": "emp"}],
                    "keys": [["id"]],
                }
            ]
        )
    )
    result = derive(design, "3nf")
    assert result.table("emp").foreign_keys == (ForeignKey(("mgr",), "emp", ("id",)),)
    assert "FOREIGN KEY ([mgr]) REFERENCES [emp] ([id])" in emit_ddl(result)


def test_a_key_that_references_its_own_entity_is_not_a_foreign_key() -> None:
    design = DesignInput.from_dict(
        _doc([{"name": "t", "attributes": [{"name": "id", "references": "t"}], "keys": [["id"]]}])
    )
    assert derive(design, "3nf").table("t").foreign_keys == ()


# ---- issue 386: decimal scale larger than precision ------------------------------------------


def test_a_decimal_scale_larger_than_its_precision_is_refused() -> None:
    doc = _doc([_sale({"name": "v", "type": "decimal", "precision": 5, "scale": 9})])
    with pytest.raises(DesignError, match=r"scale 9 .*precision 5"):
        DesignInput.from_dict(doc)


def test_a_decimal_scale_without_precision_is_accepted() -> None:
    DesignInput.from_dict(_doc([_sale({"name": "v", "type": "decimal", "scale": 2})]))


def test_from_data_gives_a_precision_that_holds_the_scale() -> None:
    from decimal import Decimal

    from shape.design.from_data import design_from_rows

    for values in (
        [Decimal("1E-50"), Decimal("2")],
        [Decimal("12345678901234567890123456789012345678901234.5"), Decimal("1")],
    ):
        rows = [{"id": i, "v": v} for i, v in enumerate(values)]
        attr = design_from_rows(rows, name="t").entities[0].attribute("v")
        assert attr.precision is not None and attr.scale is not None
        assert attr.scale <= attr.precision
        digits = max(len(v.as_tuple().digits) + max(0, int(v.as_tuple().exponent)) for v in values)
        assert attr.precision >= max(digits, attr.scale)
    small = design_from_rows([{"id": 1, "v": Decimal("1.25")}], name="t")
    assert small.entities[0].attribute("v").precision == 38  # unchanged for ordinary values


# ---- issue 387: repeated names in a key, a dependency or a hierarchy ------------------------


@pytest.mark.parametrize(
    ("entity", "extra", "where"),
    [
        ({"keys": [["id", "id"]]}, {}, "key"),
        ({"dependencies": [{"determinant": ["a", "a"], "dependent": ["b"]}]}, {}, "dependency"),
        ({"dependencies": [{"determinant": ["a"], "dependent": ["b", "b"]}]}, {}, "dependency"),
        ({}, {"hierarchies": [{"name": "h", "entity": "s", "levels": ["a", "a"]}]}, "hierarchy"),
    ],
)
def test_a_repeated_attribute_name_is_refused(
    entity: dict[str, Any], extra: dict[str, Any], where: str
) -> None:
    ent = {"name": "s", "attributes": [{"name": "id"}, {"name": "a"}, {"name": "b"}], **entity}
    with pytest.raises(DesignError, match=rf"{where}.*repeats attribute"):
        DesignInput.from_dict(_doc([ent], **extra))
