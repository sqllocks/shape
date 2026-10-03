"""HUNT2-profile: regression tests for contract emission (#641, #642, #643, #644)."""

from __future__ import annotations

import json
import math
import re
from typing import Any

import jsonschema
import pytest

from shape.contracts.emit import EmitError, contract_from, emit
from shape.contracts.v1 import ContractError
from shape.generation.ddl import from_ddl

ALL = ("tsql", "tsql-fabric-warehouse", "postgres", "mysql")


# ------------------------------------------------------------- #641 columns with no rules

_LOOSE: dict[str, Any] = {
    "columns": {
        "id": {"dtype": "integer", "nullable": False},
        "note": {},
        "memo": {"nullable": True},
    },
    "allow_extra_columns": False,
}


@pytest.mark.parametrize("dialect", ALL)
def test_641_ddl_creates_columns_with_no_rules(dialect: str) -> None:
    result = emit(_LOOSE, "ddl", table="t", dialect=dialect)
    schema, _ = from_ddl(result.text, smart=False)
    assert sorted(schema.tables["t"].columns) == ["id", "memo", "note"]


def test_641_json_schema_allows_columns_with_no_rules() -> None:
    schema = json.loads(emit(_LOOSE, "jsonschema", table="t").text)
    jsonschema.validate({"id": 1, "note": "hello", "memo": None}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"id": 1, "note": "x", "memo": "y", "other": 1}, schema)
    # shape check requires every listed column, so the schema does too
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"id": 1}, schema)


@pytest.mark.parametrize("target", ["pandera", "gx"])
def test_641_pandera_and_gx_name_columns_with_no_rules(target: str) -> None:
    text = emit(_LOOSE, target, table="t").text
    assert "note" in text and "memo" in text


@pytest.mark.parametrize("target", ["jsonschema", "gx"])
def test_641_round_trip_keeps_the_columns(target: str) -> None:
    back = contract_from(target, emit(_LOOSE, target, table="t").text)
    assert {"note", "memo"} <= set(back.get("required_columns", []))


# ------------------------------------------------------------------ #642 constraint names


def _names(text: str) -> list[str]:
    return re.findall(r'CONSTRAINT ["\[`]([^"\]`]+)["\]`]', text)


@pytest.mark.parametrize("dialect", ["tsql", "postgres", "mysql"])
def test_642_constraint_names_do_not_collide_across_tables(dialect: str) -> None:
    tables = {
        "order": {"columns": {"line_id": {"dtype": "integer", "unique": True, "min": 1}}},
        "order_line": {"columns": {"id": {"dtype": "integer", "unique": True, "min": 1}}},
    }
    names = _names(emit({"tables": tables}, "ddl", dialect=dialect).text)
    assert len(names) == 4 and len(set(names)) == 4


def test_642_a_name_without_a_collision_is_unchanged() -> None:
    contract = {"columns": {"id": {"dtype": "integer", "unique": True}}}
    assert _names(emit(contract, "ddl", table="orders", dialect="postgres").text) == [
        "UQ_orders_id"
    ]


def test_642_postgres_names_fit_63_bytes_and_stay_distinct() -> None:
    a, b = "顧客番号" * 7 + "甲", "顧客番号" * 7 + "乙"
    contract = {
        "columns": {a: {"dtype": "string", "unique": True}, b: {"dtype": "string", "unique": True}}
    }
    names = _names(emit(contract, "ddl", table="t", dialect="postgres").text)
    assert len(names) == 2
    assert all(len(n.encode("utf-8")) <= 63 for n in names)
    # distinct even after the engine's own cut at 63 bytes
    assert len({n.encode("utf-8")[:63] for n in names}) == 2


@pytest.mark.parametrize(("dialect", "limit"), [("tsql", 128), ("mysql", 64)])
def test_642_other_dialects_keep_their_character_limit(dialect: str, limit: int) -> None:
    contract = {"columns": {"c" * 200: {"dtype": "integer", "unique": True}}}
    (name,) = _names(emit(contract, "ddl", table="t", dialect=dialect).text)
    assert len(name) <= limit


# ------------------------------------------------------------------ #643 empty tables


@pytest.mark.parametrize("contract", [{}, {"row_count": {"min": 1}}])
@pytest.mark.parametrize("dialect", ALL)
def test_643_a_table_with_no_columns_is_refused(contract: dict[str, Any], dialect: str) -> None:
    with pytest.raises(EmitError, match="no column"):
        emit(contract, "ddl", table="t", dialect=dialect)


def test_643_an_empty_table_in_a_tables_contract_is_refused_by_name() -> None:
    contract = {"tables": {"a": {"columns": {"x": {"dtype": "integer"}}}, "e": {}}}
    with pytest.raises(EmitError, match="'e'"):
        emit(contract, "ddl", dialect="postgres")


def test_643_cli_exits_2_for_an_empty_table(tmp_path: Any, capsys: Any) -> None:
    from shape.cli.main import main

    path = tmp_path / "e.json"
    path.write_text("{}")
    assert main(["contract", "emit", str(path), "--to", "ddl", "--strict"]) == 2


# ----------------------------------------------------------------- #644 non-finite numbers


@pytest.mark.parametrize("target", ["jsonschema", "gx", "ddl", "pandera"])
@pytest.mark.parametrize(
    "rules",
    [
        {"dtype": "float", "max": math.inf},
        {"dtype": "float", "min": -math.inf},
        {"dtype": "float", "min": math.nan},
        {"dtype": "float", "max_null_rate": math.nan},
        {"dtype": "float", "max_null_rate": 1.5},
        {"dtype": "float", "max_null_rate": -0.1},
    ],
)
def test_644_non_finite_or_out_of_range_numbers_are_refused(
    target: str, rules: dict[str, Any]
) -> None:
    with pytest.raises(ContractError, match="'a'"):
        emit({"columns": {"a": rules}}, target, table="t")


@pytest.mark.parametrize("rate", [0, 0.0, 0.5, 1, 1.0])
def test_644_null_rates_from_zero_to_one_are_emitted_as_json(rate: float) -> None:
    contract = {"columns": {"a": {"dtype": "float", "max_null_rate": rate, "max": 10}}}
    for target in ("jsonschema", "gx"):
        json.loads(emit(contract, target, table="t").text, parse_constant=_no_constant)


def _no_constant(name: str) -> None:
    raise ValueError(f"not JSON: {name}")
