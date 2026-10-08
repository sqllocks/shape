"""Paths of shape.spec that had no test: YAML contracts, the v2 read helpers and the migrations."""

from __future__ import annotations

import pytest

from shape.spec import FieldContract, ShapeContract, load_contract, save_contract, view
from shape.spec.migrate import (
    legacy_view,
    migrate_capture_v1,
    migrate_dict,
    migrate_engine_v1,
    to_model,
)
from shape.spec.model import ModelError


@pytest.mark.parametrize("suffix", [".yaml", ".yml", ".json"])
def test_a_contract_round_trips_through_each_file_format(tmp_path, suffix):
    c = ShapeContract(
        "customer",
        fields=(FieldContract("id", "integer", False, sensitivity="CONFIDENTIAL"),),
        metadata={"purpose": "test"},
    )
    path = tmp_path / f"c{suffix}"
    save_contract(c, path)
    assert load_contract(path) == c


def test_a_yaml_contract_is_checked_like_a_json_one(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("name: x\nmandatory_capabilities: [future/9]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="future/9"):
        load_contract(path)


def test_migrate_dict_keeps_the_current_version_and_refuses_the_rest():
    assert migrate_dict({"version": 1, "a": 2}) == {"version": 1, "a": 2}
    with pytest.raises(ValueError, match="downgrade"):
        migrate_dict({"version": 2})
    with pytest.raises(ValueError, match="no migration registered from version 1"):
        migrate_dict({"version": 1}, target=2)


def _capture():
    return {
        "name": "orders",
        "rows": 4,
        "columns": {
            "id": {
                "kind": "numeric",
                "count": 4,
                "null_count": 0,
                "min": 1,
                "max": 4,
                "mean": 2.5,
                "distinct_estimate": 4,
                "distinct_exact": True,
                "q25": 1.75,
                "q50": 2.5,
                "quantiles": {"0.9": 3.7, "bad": "x"},
                "topk": [[1, 1], ["a", True], "junk"],
                "error_models": {"cardinality": {"algorithm": "exact", "exact": True}, "x": 1},
            },
            "email": {
                "kind": "text",
                "null_count": 1,
                "length": {"min": 5},
                "classification": "PII",
            },
        },
        "relationships": {
            "foreign_key": [{"source": "a.x", "target": "b.y"}, {"source": "a.z"}, "junk"]
        },
        "classifications": {"email": "PII", "bad": 3},
    }


def test_a_v1_capture_migrates_to_a_valid_v2_model():
    m = migrate_capture_v1(_capture())
    assert m["schema_version"] == 2 and list(m["tables"]) == ["orders"]
    cols = view.columns_of(view.table_of(m))
    ident = cols["id"]
    assert ident["kind"] == "float" and ident["distinct"] == 4 and ident["distinct_exact"]
    assert ident["quantiles"] == {"0.25": 1.75, "0.5": 2.5, "0.9": 3.7}
    assert ident["top"] == [[1, 1]]
    assert list(ident["error_models"]) == ["cardinality"]
    assert cols["email"]["kind"] == "text" and cols["email"]["classification"] == "PII"
    assert cols["email"]["count"] == 4  # no count: the table's rows
    assert m["relationships"] == [{"source": "a.x", "target": "b.y", "kind": "foreign_key"}]
    assert m["classifications"] == {"email": "PII"}
    assert legacy_view(m) == _capture()


def test_a_flat_relationship_list_and_a_given_name_are_kept():
    m = migrate_capture_v1(
        {"rows": 0, "relationships": [{"source": "a", "target": "b", "kind": "fk"}]}, name="t"
    )
    assert list(m["tables"]) == ["t"] and m["relationships"][0]["kind"] == "fk"


def test_to_model_routes_each_kind_of_document():
    v2 = migrate_capture_v1({"rows": 1, "columns": {"a": {}}})
    assert to_model(v2) is v2
    engine_v1 = {**v2, "schema_version": 1}
    engine_v1.pop("x_legacy")
    assert to_model(engine_v1)["schema_version"] == 2
    assert migrate_engine_v1(engine_v1) == {**engine_v1, "schema_version": 2}
    assert legacy_view(to_model(engine_v1))["schema_version"] == 2
    with pytest.raises(ModelError, match="must be an object"):
        to_model([])


def test_view_helpers():
    m = view.model_of({"rows": 4, "columns": {"a": {"kind": "numeric", "q50": 2.0}}})
    t = view.table_of(m, "table")
    with pytest.raises(KeyError, match="no table 'nope'"):
        view.table_of(m, "nope")
    two = {**m, "tables": {"a": t, "b": t}}
    with pytest.raises(ValueError, match="2 tables"):
        view.table_of(two)
    col = view.columns_of(t)["a"]
    assert view.median(col) == 2.0 and view.median({}) is None
    assert view.family("int") == "numeric" and view.family("text") == "text"
    assert view.kind_matches("numeric", "float") and not view.kind_matches("text", "float")
    assert view.null_rate({"null_count": 1}, 4) == 0.25 and view.null_rate({}, 0) == 0.0
    assert view.distinct_bounds({"distinct": 10, "distinct_exact": True}) == (10.0, 10.0)
    approx = {"distinct": 100, "error_models": {"cardinality": {"relative_error": 0.1}}}
    assert view.distinct_bounds(approx) == pytest.approx((90.0, 110.0))
    assert view.distinct_bounds({"distinct": 5}) == (5.0, 5.0)
