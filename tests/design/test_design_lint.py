"""W5-02 item 3: the lint report (grain, additivity, foreign-key coverage, naming)."""

from __future__ import annotations

from typing import Any

from shape.design import DesignInput
from shape.design.lint import Finding, lint


def run(doc: dict[str, Any], mode: str = "star") -> list[Finding]:
    return lint(DesignInput.from_dict(doc), mode)


def codes(doc: dict[str, Any], mode: str = "star") -> list[str]:
    return [f.code for f in run(doc, mode)]


def test_the_sample_design_is_clean(doc: dict[str, Any]) -> None:
    assert run(doc) == []
    assert run(doc, "snowflake") == []
    assert run(doc, "3nf") == []


def test_grain_not_declared(doc: dict[str, Any]) -> None:
    del doc["facts"][0]["grain"]
    found = run(doc)
    assert [f.code for f in found] == ["D001"]
    assert found[0].severity == "error" and found[0].path == "facts.sales"


def test_grain_does_not_determine_a_measure(doc: dict[str, Any]) -> None:
    doc["facts"][0]["grain"] = ["order_id"]
    doc["facts"][0]["degenerate"] = ["order_id", "line_no"]
    assert "D002" in codes(doc)


def test_grain_attribute_without_a_column(doc: dict[str, Any]) -> None:
    doc["facts"][0]["degenerate"] = ["order_id"]
    assert "D003" in codes(doc)


def test_additivity_not_declared(doc: dict[str, Any]) -> None:
    del doc["facts"][0]["measures"][0]["additivity"]
    found = run(doc)
    assert [f.code for f in found] == ["M001"]
    assert found[0].severity == "warning"
    assert "quantity" in found[0].path


def test_semi_additive_needs_the_dimension_it_is_not_additive_over(doc: dict[str, Any]) -> None:
    doc["facts"][0]["measures"][0]["additivity"] = "semi_additive"
    assert codes(doc) == ["M002"]
    doc["facts"][0]["measures"][0]["not_additive_over"] = ["date"]
    assert codes(doc) == []


def test_additive_measure_on_a_non_numeric_attribute(doc: dict[str, Any]) -> None:
    doc["facts"][0]["measures"].append(
        {"name": "flag", "attribute": "is_gift", "additivity": "additive"}
    )
    assert codes(doc) == ["M003"]


def test_reference_attribute_not_covered_by_a_dimension(doc: dict[str, Any]) -> None:
    doc["facts"][0]["dimensions"] = doc["facts"][0]["dimensions"][:1]
    assert codes(doc) == ["F001", "E001"]


def test_via_attribute_references_another_entity(doc: dict[str, Any]) -> None:
    doc["facts"][0]["dimensions"][0]["entity"] = "Product"
    doc["facts"][0]["dimensions"][1]["entity"] = "Customer"
    assert "F002" in codes(doc)


def test_entity_without_a_declared_key(doc: dict[str, Any]) -> None:
    doc["entities"][1]["keys"] = []
    found = run(doc)
    assert [f.code for f in found] == ["K001"]
    assert found[0].path == "entities.Product"


def test_naming(doc: dict[str, Any]) -> None:
    doc["entities"][1]["attributes"][1]["name"] = "Stock Keeping"
    found = run(doc)
    assert [f.code for f in found] == ["N001"]
    assert found[0].path == "entities.Product.attributes.Stock Keeping"


def test_identifier_too_long_for_postgres(doc: dict[str, Any]) -> None:
    long = "x" * 64
    doc["entities"][1]["attributes"][1]["name"] = long
    assert "N002" in codes(doc)


def test_unused_entity_is_reported_for_dimensional_modes_only(doc: dict[str, Any]) -> None:
    doc["entities"].append(
        {
            "name": "Store",
            "attributes": [{"name": "store_id", "type": "integer"}],
            "keys": [["store_id"]],
        }
    )
    assert codes(doc) == ["E001"]
    assert codes(doc, "3nf") == []


def test_derivation_failure_is_reported_as_an_error(doc: dict[str, Any]) -> None:
    doc["entities"][0]["keys"] = [["customer_id", "name"]]
    out = run(doc, "3nf")
    assert [f.code for f in out] == ["G001"]
    assert out[0].severity == "error"


def test_findings_are_sorted_and_serialisable(doc: dict[str, Any]) -> None:
    del doc["facts"][0]["grain"]
    del doc["facts"][0]["measures"][0]["additivity"]
    found = run(doc)
    assert [f.severity for f in found] == ["error", "warning"]
    assert found[0].to_dict() == {
        "code": "D001",
        "severity": "error",
        "path": "facts.sales",
        "message": found[0].message,
    }
