"""W5-02 item 2: star and snowflake derivation (Kimball's rules)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from shape.design import DesignError, DesignInput
from shape.design.engine import derive


def star(doc: dict[str, Any], mode: str = "star") -> dict[str, Any]:
    return {t.name: t for t in derive(DesignInput.from_dict(doc), mode).tables}


def cols(t: Any) -> list[str]:
    return [c.name for c in t.columns]


def fk_set(t: Any) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {(f.columns, f.ref_table, f.ref_columns) for f in t.foreign_keys}


def test_star_tables(doc: dict[str, Any]) -> None:
    t = star(doc)
    assert sorted(t) == [
        "bridge_sales_promotion",
        "dim_customer",
        "dim_date",
        "dim_product",
        "dim_promotion",
        "dim_sales_junk",
        "fact_sales",
    ]


def test_fact_is_atomic_grain_with_surrogate_keys(doc: dict[str, Any]) -> None:
    f = star(doc)["fact_sales"]
    assert cols(f) == [
        "sk_customer",
        "sk_product",
        "sk_order_date",
        "sk_ship_date",
        "sk_junk",
        "sk_promotion_group",
        "order_id",
        "line_no",
        "quantity",
        "amount",
        "discount_pct",
    ]
    assert f.kind == "fact"
    # The declared grain (order_id, line_no) maps to the degenerate dimensions.
    assert f.primary_key == ("order_id", "line_no")
    assert fk_set(f) == {
        (("sk_customer",), "dim_customer", ("sk_customer",)),
        (("sk_product",), "dim_product", ("sk_product",)),
        (("sk_order_date",), "dim_date", ("sk_date",)),
        (("sk_ship_date",), "dim_date", ("sk_date",)),
        (("sk_junk",), "dim_sales_junk", ("sk_junk",)),
    }
    # Measures keep the source types.
    by = {c.name: c for c in f.columns}
    assert (by["amount"].type, by["amount"].precision, by["amount"].scale) == ("decimal", 18, 2)
    assert by["sk_customer"].nullable is False and by["sk_customer"].type == "integer"


def test_dimensions_have_surrogate_and_natural_keys_and_history(doc: dict[str, Any]) -> None:
    d = star(doc)["dim_customer"]
    assert cols(d) == [
        "sk_customer",
        "customer_id",
        "name",
        "city",
        "region",
        "country",
        "previous_name",
        "valid_from",
        "valid_to",
        "is_current",
    ]
    assert d.primary_key == ("sk_customer",)
    assert d.scd_type == 2
    p = star(doc)["dim_product"]
    assert cols(p) == ["sk_product", "product_id", "sku", "category", "department"]
    assert p.scd_type == 1


def test_date_dimension_reuses_the_star_transform_columns(doc: dict[str, Any]) -> None:
    d = star(doc)["dim_date"]
    assert cols(d)[:3] == ["sk_date", "date", "year"]
    assert d.primary_key == ("sk_date",)
    assert d.kind == "date"
    assert {c.name: c.type for c in d.columns}["date"] == "date"


def test_junk_dimension_holds_the_flags(doc: dict[str, Any]) -> None:
    j = star(doc)["dim_sales_junk"]
    assert cols(j) == ["sk_junk", "status", "priority", "is_gift"]
    assert j.kind == "junk"


def test_many_to_many_goes_through_a_bridge(doc: dict[str, Any]) -> None:
    b = star(doc)["bridge_sales_promotion"]
    assert cols(b) == ["sk_promotion_group", "sk_promotion", "weighting_factor"]
    assert b.primary_key == ("sk_promotion_group", "sk_promotion")
    assert fk_set(b) == {(("sk_promotion",), "dim_promotion", ("sk_promotion",))}
    # The fact points at the group; many bridge rows share a group, so there is no foreign key.
    assert not any(
        f.columns == ("sk_promotion_group",) for f in star(doc)["fact_sales"].foreign_keys
    )


def test_snowflake_splits_hierarchy_levels(doc: dict[str, Any]) -> None:
    t = star(doc, "snowflake")
    assert cols(t["dim_customer"]) == [
        "sk_customer",
        "customer_id",
        "name",
        "city",
        "sk_customer_region",
        "previous_name",
        "valid_from",
        "valid_to",
        "is_current",
    ]
    assert cols(t["dim_customer_region"]) == ["sk_customer_region", "region", "sk_customer_country"]
    assert cols(t["dim_customer_country"]) == ["sk_customer_country", "country"]
    assert fk_set(t["dim_customer"]) == {
        (("sk_customer_region",), "dim_customer_region", ("sk_customer_region",))
    }
    assert fk_set(t["dim_customer_region"]) == {
        (("sk_customer_country",), "dim_customer_country", ("sk_customer_country",))
    }
    assert cols(t["dim_product_department"]) == ["sk_product_department", "department"]
    # The date dimension stays flat and the fact is unchanged.
    assert star(doc)["fact_sales"] == t["fact_sales"]
    assert "dim_date" in t


def test_conformed_dimension_is_shared_by_facts(doc: dict[str, Any]) -> None:
    second = copy.deepcopy(doc["facts"][0])
    second["name"] = "returns"
    second["many_to_many"] = []
    second["junk"] = []
    second["dates"] = ["order_date"]
    doc["facts"].append(second)
    result = derive(DesignInput.from_dict(doc), "star")
    names = [t.name for t in result.tables]
    assert names.count("dim_customer") == 1 and names.count("dim_date") == 1
    fk_targets = {
        f.ref_table
        for t in result.tables
        if t.kind == "fact"
        for f in t.foreign_keys
        if f.ref_table == "dim_customer"
    }
    assert fk_targets == {"dim_customer"}
    assert any("conformed" in n for n in result.notes)


def test_role_playing_dimension(doc: dict[str, Any]) -> None:
    doc["facts"][0]["dimensions"].append(
        {"entity": "Customer", "via": "customer_id", "role": "bill_to"}
    )
    f = star(doc)["fact_sales"]
    assert "sk_bill_to" in cols(f)
    assert (("sk_bill_to",), "dim_customer", ("sk_customer",)) in fk_set(f)


def test_no_dates_means_no_date_dimension(doc: dict[str, Any]) -> None:
    doc["facts"][0]["dates"] = []
    assert "dim_date" not in star(doc)


def test_missing_grain_is_an_error(doc: dict[str, Any]) -> None:
    del doc["facts"][0]["grain"]
    with pytest.raises(DesignError, match="grain"):
        derive(DesignInput.from_dict(doc), "star")


def test_grain_attribute_that_maps_to_nothing_is_an_error(doc: dict[str, Any]) -> None:
    doc["facts"][0]["degenerate"] = ["order_id"]  # line_no is in the grain but nowhere else
    with pytest.raises(DesignError, match="line_no"):
        derive(DesignInput.from_dict(doc), "star")


def test_grain_must_determine_the_measures(doc: dict[str, Any]) -> None:
    doc["facts"][0]["grain"] = ["order_id"]
    doc["facts"][0]["degenerate"] = ["order_id", "line_no"]
    with pytest.raises(DesignError, match="does not determine"):
        derive(DesignInput.from_dict(doc), "star")


def test_facts_required_for_dimensional_modes(doc: dict[str, Any]) -> None:
    doc["facts"] = []
    with pytest.raises(DesignError, match="no facts"):
        derive(DesignInput.from_dict(doc), "star")


def test_type_2_attribute_in_an_outrigger_puts_history_there(doc: dict[str, Any]) -> None:
    doc["entities"][0]["history"]["attributes"]["region"] = 2
    t = star(doc, "snowflake")
    assert cols(t["dim_customer_region"])[-3:] == ["valid_from", "valid_to", "is_current"]
