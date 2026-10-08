"""W5-02 item 1: the structured design input and its JSON Schema."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from shape.design import DesignError, DesignInput, design_input_schema
from shape.schemacheck import validate


def test_schema_accepts_the_sample_and_round_trips(doc: dict[str, Any]) -> None:
    schema = design_input_schema()
    assert schema["properties"]["format"]["const"] == "shape-design"
    assert validate(doc, schema) == []
    design = DesignInput.from_dict(doc)
    again = DesignInput.from_dict(design.to_dict())
    assert again == design
    assert json.dumps(again.to_dict(), sort_keys=True) == json.dumps(
        design.to_dict(), sort_keys=True
    )


def test_defaults_are_filled_in_the_canonical_form(doc: dict[str, Any]) -> None:
    del doc["entities"][2]["keys"]  # Promotion: no keys declared
    out = DesignInput.from_dict(doc).to_dict()
    promo = next(e for e in out["entities"] if e["name"] == "Promotion")
    assert promo["keys"] == [] and promo["dependencies"] == []
    assert promo["attributes"][0]["nullable"] is False


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(format="other"), "format"),
        (lambda d: d.update(version=2), "newer|version"),
        (lambda d: d.update(version=0), "version"),
        (lambda d: d.pop("name"), "name"),
        (lambda d: d.update(surprise=1), "surprise"),
        (lambda d: d["entities"].append(copy.deepcopy(d["entities"][0])), "duplicate entity"),
        (lambda d: d["entities"][0]["attributes"].append({"name": "city"}), "duplicate attribute"),
        (lambda d: d["entities"][0]["attributes"][0].update(type="money"), "type"),
        (lambda d: d["entities"][0].update(keys=[["nope"]]), "unknown attribute"),
        (lambda d: d["entities"][0].update(keys=[[]]), "empty"),
        (
            lambda d: d["entities"][0]["dependencies"].append(
                {"determinant": [], "dependent": ["name"]}
            ),
            "determinant",
        ),
        (
            lambda d: d["entities"][0]["dependencies"].append(
                {"determinant": ["city"], "dependent": ["nope"]}
            ),
            "unknown attribute",
        ),
        (lambda d: d["hierarchies"][0].update(entity="Nope"), "unknown entity"),
        (lambda d: d["hierarchies"][0].update(levels=["city"]), "at least two"),
        (lambda d: d["hierarchies"][0].update(levels=["city", "nope"]), "unknown attribute"),
        (lambda d: d["facts"][0].update(source="Nope"), "unknown entity"),
        (lambda d: d["facts"][0].update(grain=["nope"]), "unknown attribute"),
        (lambda d: d["facts"][0]["measures"][0].update(additivity="sometimes"), "additivity"),
        (lambda d: d["facts"][0]["measures"][0].update(attribute="nope"), "unknown attribute"),
        (lambda d: d["facts"][0]["dimensions"][0].update(entity="Nope"), "unknown entity"),
        (lambda d: d["facts"][0]["dimensions"][0].update(via="nope"), "unknown attribute"),
        (lambda d: d["facts"][0].update(dates=["nope"]), "unknown attribute"),
        (lambda d: d["facts"][0].update(many_to_many=["Nope"]), "unknown entity"),
        (
            lambda d: d["entities"][0]["history"]["attributes"].update(city=4),
            "history",
        ),
        (lambda d: d["entities"][0]["history"]["attributes"].update(nope=2), "unknown attribute"),
        (lambda d: d["entities"][3]["attributes"][2].update(references="Nope"), "unknown entity"),
    ],
)
def test_invalid_inputs_are_rejected_with_a_message(
    doc: dict[str, Any], mutate: Any, message: str
) -> None:
    mutate(doc)
    with pytest.raises(DesignError, match=message):
        DesignInput.from_dict(doc)


def test_a_non_object_is_rejected() -> None:
    with pytest.raises(DesignError):
        DesignInput.from_dict([])  # type: ignore[arg-type]


def test_a_fact_may_omit_its_grain_to_be_flagged_by_lint(doc: dict[str, Any]) -> None:
    del doc["facts"][0]["grain"]
    assert DesignInput.from_dict(doc).facts[0].grain == ()


def test_load_design_reads_a_file(tmp_path: Any, doc: dict[str, Any]) -> None:
    from shape.design import load_design

    p = tmp_path / "d.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert load_design(p).name == "retail"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(DesignError, match="JSON"):
        load_design(p)
