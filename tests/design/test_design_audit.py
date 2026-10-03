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
