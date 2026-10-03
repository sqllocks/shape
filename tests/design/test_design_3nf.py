"""W5-02 item 2: 3NF synthesis from a design input (guarantees checked on the output)."""

from __future__ import annotations

from typing import Any

import pytest

from shape.design import DesignError, DesignInput
from shape.design.engine import derive
from shape.design.fd import FD, is_lossless, preserves_dependencies


def tables(doc: dict[str, Any]) -> dict[str, Any]:
    return {t.name: t for t in derive(DesignInput.from_dict(doc), "3nf").tables}


def test_customer_splits_along_its_dependencies(doc: dict[str, Any]) -> None:
    t = tables(doc)
    assert [c.name for c in t["customer"].columns] == ["customer_id", "name", "city"]
    assert [c.name for c in t["customer_city"].columns] == ["city", "region"]
    assert [c.name for c in t["customer_region"].columns] == ["region", "country"]
    assert t["customer"].primary_key == ("customer_id",)
    assert t["customer_city"].primary_key == ("city",)
    fks = {(f.columns, f.ref_table, f.ref_columns) for f in t["customer"].foreign_keys}
    assert fks == {(("city",), "customer_city", ("city",))}
    fks = {(f.columns, f.ref_table, f.ref_columns) for f in t["customer_city"].foreign_keys}
    assert fks == {(("region",), "customer_region", ("region",))}


def test_decomposition_is_lossless_and_preserving(doc: dict[str, Any]) -> None:
    design = DesignInput.from_dict(doc)
    result = derive(design, "3nf")
    for ent in design.entities:
        parts = [t for t in result.tables if t.source_entity == ent.name]
        deps = [FD(d.determinant, d.dependent) for d in ent.dependencies]
        deps += [FD(k, tuple(a for a in ent.attribute_names if a not in k)) for k in ent.keys]
        deps = [d for d in deps if d.rhs]
        schemes = [[c.name for c in t.columns] for t in parts]
        assert is_lossless(ent.attribute_names, schemes, deps), ent.name
        assert preserves_dependencies(schemes, deps), ent.name


def test_reference_attributes_become_foreign_keys(doc: dict[str, Any]) -> None:
    t = tables(doc)
    fks = {(f.columns, f.ref_table, f.ref_columns) for f in t["order_line"].foreign_keys}
    assert fks == {
        (("customer_id",), "customer", ("customer_id",)),
        (("product_id",), "product", ("product_id",)),
    }
    assert t["order_line"].primary_key == ("order_id", "line_no")


def test_key_relation_is_added_when_the_dependencies_do_not_cover_a_key() -> None:
    doc = {
        "format": "shape-design",
        "version": 1,
        "name": "x",
        "entities": [
            {
                "name": "T",
                "attributes": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
                "dependencies": [{"determinant": ["a"], "dependent": ["b"]}],
            }
        ],
    }
    t = tables(doc)
    # The relation that holds the entity's key is named after the entity.
    assert [c.name for c in t["t"].columns] == ["a", "c"]
    assert t["t"].primary_key == ("a", "c")
    assert [c.name for c in t["t_a"].columns] == ["a", "b"]
    assert t["t_a"].primary_key == ("a",)


def test_nullable_key_columns_are_not_null(doc: dict[str, Any]) -> None:
    doc["entities"][0]["attributes"][0]["nullable"] = True
    t = tables(doc)
    assert t["customer"].columns[0].nullable is False


def test_unknown_mode_is_rejected(doc: dict[str, Any]) -> None:
    with pytest.raises(DesignError, match="mode"):
        derive(DesignInput.from_dict(doc), "bogus")


def test_too_many_attributes_for_key_search_is_a_clear_error() -> None:
    attrs = [{"name": f"a{i}"} for i in range(30)]
    deps = [{"determinant": [f"a{i}"], "dependent": [f"a{i + 1}"]} for i in range(29)]
    deps += [{"determinant": ["a29"], "dependent": ["a0"]}]
    doc = {
        "format": "shape-design",
        "version": 1,
        "name": "x",
        "entities": [{"name": "T", "attributes": attrs, "dependencies": deps}],
    }
    with pytest.raises(DesignError, match="declare the keys"):
        derive(DesignInput.from_dict(doc), "3nf")
