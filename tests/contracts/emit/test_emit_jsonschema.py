"""W5-04 item 3 (``jsonschema``) and item 4 (metadata, ``contract_from``)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from shape import schemacheck
from shape.contracts.emit import EmitError, contract_from, emit, expressible
from shape.contracts.emit._common import normalize_table

GOOD_ROW = {
    "order_id": 7,
    "customer_email": "a@example.com",
    "status": "paid",
    "amount": 12.5,
    "discount_code": None,
    "is_gift": False,
    "placed_at": "2026-10-03T10:00:00",
}


def schema_of(contract: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return json.loads(emit(contract, "jsonschema", **kw).text)  # type: ignore[no-any-return]


def test_the_schema_is_draft_2020_12_for_one_row(orders: dict[str, Any]) -> None:
    s = schema_of(orders, table="orders")
    assert s["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert s["type"] == "object" and s["title"] == "orders"
    assert s["additionalProperties"] is False
    assert s["required"] == sorted(orders["required_columns"])
    assert set(s["properties"]) == set(orders["columns"])
    p = s["properties"]
    assert p["order_id"] == {"type": "integer", "minimum": 1, "x-shape": {"unique": True}}
    assert p["amount"]["type"] == "number" and p["amount"]["maximum"] == 100000
    assert p["status"]["enum"] == ["new", "paid", "shipped", "cancelled"]
    assert p["customer_email"]["pattern"].startswith("^[a-zA-Z0-9._%+")
    assert p["placed_at"] == {"type": "string", "format": "date-time"}
    assert p["discount_code"]["type"] == ["string", "null"]
    assert p["is_gift"]["x-shape"] == {"min_true_rate": 0.01, "max_true_rate": 0.2}


def test_the_schema_is_valid_for_the_reference_validator(orders: dict[str, Any]) -> None:
    jsonschema = pytest.importorskip("jsonschema", reason="the jsonschema package is not installed")
    s = schema_of(orders, table="orders")
    jsonschema.Draft202012Validator.check_schema(s)
    v = jsonschema.Draft202012Validator(s)
    assert not list(v.iter_errors(GOOD_ROW))
    for row in broken_rows():
        assert list(v.iter_errors(row)), row


def broken_rows() -> list[dict[str, Any]]:
    def with_(**kw: Any) -> dict[str, Any]:
        row = {**GOOD_ROW, **kw}
        return {k: v for k, v in row.items() if v is not ...}

    return [
        with_(order_id=None),  # nullable: false
        with_(order_id="7"),  # dtype
        with_(order_id=0),  # min
        with_(amount=100000.5),  # max
        with_(amount=-0.01),  # min, float
        with_(status="lost"),  # allowed_values
        with_(customer_email="not an email"),  # pattern
        with_(amount=...),  # required
        with_(extra=1),  # allow_extra_columns: false
        with_(is_gift="yes"),  # dtype boolean
    ]


def test_the_schema_validates_with_shape_schemacheck(orders: dict[str, Any]) -> None:
    s = schema_of(orders, table="orders")
    assert schemacheck.validate(GOOD_ROW, s) == []
    for row in broken_rows():
        assert schemacheck.validate(row, s), row


def test_boundaries_are_inclusive(orders: dict[str, Any]) -> None:
    s = schema_of(orders, table="orders")
    for amount in (0, 0.0, 100000, 100000.0):
        assert schemacheck.validate({**GOOD_ROW, "amount": amount}, s) == []
    assert schemacheck.validate({**GOOD_ROW, "order_id": 1}, s) == []


def test_nullable_columns_accept_null_even_with_enum_and_a_non_nullable_one_does_not() -> None:
    c = {
        "columns": {
            "a": {"dtype": "string", "allowed_values": ["x"]},
            "b": {"allowed_values": ["x"]},
            "c": {"nullable": False},
            "d": {"dtype": "integer", "nullable": False},
        }
    }
    s = schema_of(c)
    assert s["properties"]["a"]["enum"] == ["x", None]
    assert s["properties"]["b"]["enum"] == ["x", None]
    assert s["properties"]["c"] == {"not": {"type": "null"}}
    assert "additionalProperties" not in s and "required" not in s
    assert schemacheck.validate({"a": None, "b": None, "c": 1, "d": 1}, s) == []
    assert schemacheck.validate({"c": None}, s)
    assert schemacheck.validate({"d": None}, s)


def test_allow_extra_columns_follows_the_contract() -> None:
    assert "additionalProperties" not in schema_of({"allow_extra_columns": True})
    assert schema_of({"allow_extra_columns": False})["additionalProperties"] is False
    s = schema_of({"required_columns": ["id"], "allow_extra_columns": False})
    assert s["properties"] == {"id": {}} and s["required"] == ["id"]
    assert schemacheck.validate({"id": 1}, s) == []
    assert schemacheck.validate({"id": 1, "x": 2}, s)


def test_what_json_schema_cannot_say_is_listed_and_kept_as_metadata(
    orders: dict[str, Any],
) -> None:
    result = emit(orders, "jsonschema")
    got = {(i["column"], i["rule"]) for i in result.not_expressed}
    assert got == {
        ("order_id", "unique"),
        ("amount", "distribution"),
        ("discount_code", "max_null_rate"),
        ("is_gift", "min_true_rate"),
        ("is_gift", "max_true_rate"),
        (None, "row_count"),
        (None, "fd"),
    }
    s = json.loads(result.text)
    assert s["x-shape"] == {
        "format": "shape-contract-emit",
        "version": 1,
        "table": "table",
        "rules": {
            "fd": [{"determinant": "order_id", "dependent": "customer_email", "min_confidence": 1}],
            "row_count": {"min": 1, "max": 1000000},
        },
    }
    assert s["properties"]["amount"]["x-shape"] == {"distribution": "lognormal"}
    assert "x-shape" not in s["properties"]["status"]


def test_unrepresentable_values_are_not_expressed() -> None:
    c = {
        "columns": {
            "a": {"dtype": "date", "min": "2020-01-01"},
            "b": {"dtype": "string", "allowed_values": []},
            "c": {"dtype": "string", "pattern": "no-such-label"},
            "d": {"dtype": "weird"},
        }
    }
    result = emit(c, "jsonschema")
    assert {(i["column"], i["rule"]) for i in result.not_expressed} == {
        ("a", "min"),
        ("b", "allowed_values"),
        ("c", "pattern"),
        ("d", "dtype"),
    }
    s = json.loads(result.text)
    assert s["properties"]["a"]["x-shape"] == {"min": "2020-01-01"}
    assert "enum" not in s["properties"]["b"] and "pattern" not in s["properties"]["c"]


# -- contract_from ------------------------------------------------------------------------


def test_contract_from_rebuilds_the_expressible_part(orders: dict[str, Any]) -> None:
    text = emit(orders, "jsonschema").text
    back = contract_from("jsonschema", text)
    assert back == expressible(orders, "jsonschema")
    # something was actually dropped, and something kept
    assert "distribution" not in back["columns"]["amount"] and "fd" not in back
    assert back["columns"]["amount"] == {
        "dtype": "float",
        "nullable": False,
        "min": 0,
        "max": 100000,
    }
    assert back["allow_extra_columns"] is False
    assert back["required_columns"] == sorted(orders["required_columns"])


def test_contract_from_with_metadata_recovers_the_whole_contract(orders: dict[str, Any]) -> None:
    text = emit(orders, "jsonschema").text
    back = contract_from("jsonschema", text, use_meta=True)
    assert back == normalize_table(orders)
    assert back["fd"] == orders["fd"] and back["row_count"] == orders["row_count"]
    assert back["columns"]["order_id"]["unique"] is True


def test_round_trip_on_a_tables_contract(two_tables: dict[str, Any]) -> None:
    for name in ("orders", "customers"):
        text = emit(two_tables, "jsonschema", table=name).text
        assert contract_from("jsonschema", text) == expressible(
            two_tables, "jsonschema", table=name
        )
        assert contract_from("jsonschema", text, use_meta=True) == normalize_table(
            two_tables["tables"][name]
        )


@pytest.mark.parametrize(
    "contract",
    [
        {},
        {"columns": {"a": {"dtype": "date"}, "b": {"dtype": "datetime", "nullable": False}}},
        {"columns": {"a": {"allowed_values": [1, 2, None]}}},
        {"columns": {"a": {"nullable": False}, "b": {"dtype": "boolean"}}},
        {"columns": {"a": {"min": 0.5, "max": 0.5, "dtype": "float"}}},
        {"required_columns": ["z", "a"], "allow_extra_columns": False},
        {"columns": {"a": {"nullable": True, "unique": False}}},
    ],
)
def test_round_trip_small_contracts(contract: dict[str, Any]) -> None:
    text = emit(contract, "jsonschema").text
    assert contract_from("jsonschema", text) == expressible(contract, "jsonschema")


def test_contract_from_rejects_what_it_cannot_read(orders: dict[str, Any]) -> None:
    with pytest.raises(EmitError, match="ddl"):
        contract_from("ddl", "CREATE TABLE t (a INT);")
    with pytest.raises(EmitError, match="not valid JSON"):
        contract_from("jsonschema", "{nope")
    with pytest.raises(EmitError, match="JSON Schema object"):
        contract_from("jsonschema", "[1]")
    s = schema_of(orders, table="orders")
    s["x-shape"]["version"] = 2
    with pytest.raises(EmitError, match="version 2.*newer"):
        contract_from("jsonschema", json.dumps(s))
    s["x-shape"]["format"] = "other"
    with pytest.raises(EmitError, match="format"):
        contract_from("jsonschema", json.dumps(s))


def test_a_schema_without_shape_metadata_is_still_read() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "integer", "minimum": 3}}}
    assert contract_from("jsonschema", json.dumps(schema)) == {
        "columns": {"a": {"dtype": "integer", "nullable": False, "min": 3}}
    }


def test_the_input_is_never_changed_by_contract_from(orders: dict[str, Any]) -> None:
    text = emit(orders, "jsonschema").text
    before = copy.deepcopy(json.loads(text))
    contract_from("jsonschema", text, use_meta=True)
    assert json.loads(text) == before
