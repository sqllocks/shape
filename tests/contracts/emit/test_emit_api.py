"""W5-04 item 1: the shared ``emit`` API, determinism and the error cases."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from shape.contracts.emit import EmitError, EmitResult, emit
from shape.contracts.v1 import ContractError

TARGETS = ("ddl", "jsonschema", "pandera", "gx")
KEYS = {"table", "column", "rule", "reason"}


@pytest.mark.parametrize("target", TARGETS)
def test_emit_returns_text_and_a_list_of_what_is_not_expressed(
    orders: dict[str, Any], target: str
) -> None:
    result = emit(orders, target)
    assert isinstance(result, EmitResult)
    assert isinstance(result.text, str) and result.text.endswith("\n")
    assert result.not_expressed, "the sample has rules every target leaves out"
    for item in result.not_expressed:
        assert set(item) == KEYS
        assert item["table"] == "table"
        assert isinstance(item["rule"], str) and item["reason"]
        assert item["column"] is None or item["column"] in orders["columns"]


@pytest.mark.parametrize("target", TARGETS)
def test_not_expressed_is_sorted_and_names_the_joint_rule(
    orders: dict[str, Any], target: str
) -> None:
    items = emit(orders, target).not_expressed
    keys = [(i["table"], i["column"] or "", i["rule"]) for i in items]
    assert keys == sorted(keys)
    assert any(i["rule"] == "fd" and i["column"] is None for i in items)


@pytest.mark.parametrize("target", TARGETS)
def test_an_empty_contract_emits(target: str) -> None:
    result = emit({}, target)
    assert result.not_expressed == []
    assert result.text.endswith("\n")


@pytest.mark.parametrize("target", TARGETS)
def test_the_contract_can_be_a_path(orders_path: Path, orders: dict[str, Any], target: str) -> None:
    assert emit(orders_path, target) == emit(orders, target)
    assert emit(str(orders_path), target) == emit(orders, target)


def test_unknown_target_is_an_error(orders: dict[str, Any]) -> None:
    with pytest.raises(EmitError, match="unknown target 'xml'.*ddl"):
        emit(orders, "xml")


def test_dialect_is_only_for_ddl(orders: dict[str, Any]) -> None:
    for target in ("jsonschema", "pandera", "gx"):
        with pytest.raises(EmitError, match="dialect"):
            emit(orders, target, dialect="postgres")
    emit(orders, "ddl", dialect="postgres")


def test_unknown_dialect_and_unknown_option(orders: dict[str, Any]) -> None:
    with pytest.raises(EmitError, match="unknown dialect 'oracle'"):
        emit(orders, "ddl", dialect="oracle")
    with pytest.raises(EmitError, match="unknown option"):
        emit(orders, "ddl", colour="red")


@pytest.mark.parametrize(
    "bad",
    [
        {"nope": 1},
        {"columns": {"a": {"allowed_values": "x"}}},
        {"columns": {"a": {"mystery": 1}}},
        {"columns": []},
        {"required_columns": "a"},
        {"tables": {"t": {"nope": 1}}},
        {"tables": {"t": 3}},
        {"tables": [1]},
        {"tables": {"t": {"tables": {}}}},
    ],
)
@pytest.mark.parametrize("target", TARGETS)
def test_a_malformed_contract_is_the_contract_error(bad: dict[str, Any], target: str) -> None:
    with pytest.raises(ContractError):
        emit(bad, target)


def test_a_file_that_is_not_json_is_the_contract_error(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ContractError, match="not valid JSON"):
        emit(p, "ddl")


def test_a_tables_contract_emits_every_table_or_the_chosen_one(two_tables: dict[str, Any]) -> None:
    both = emit(two_tables, "ddl")
    assert "CREATE TABLE [orders]" in both.text and "CREATE TABLE [customers]" in both.text
    one = emit(two_tables, "ddl", table="customers")
    assert "[orders]" not in one.text
    assert {i["table"] for i in one.not_expressed} <= {"customers"}
    assert {i["table"] for i in both.not_expressed} == {"orders"}
    with pytest.raises(EmitError, match="unknown table 'nope'.*customers.*orders"):
        emit(two_tables, "ddl", table="nope")


@pytest.mark.parametrize("target", ["jsonschema", "gx"])
def test_one_document_targets_need_a_table_for_a_tables_contract(
    two_tables: dict[str, Any], target: str
) -> None:
    with pytest.raises(EmitError, match="--table"):
        emit(two_tables, target)
    assert emit(two_tables, target, table="customers").text


def test_pandera_emits_one_schema_per_table(two_tables: dict[str, Any]) -> None:
    text = emit(two_tables, "pandera").text
    assert '"orders": DataFrameSchema(' in text and '"customers": DataFrameSchema(' in text


def test_a_single_table_contract_takes_its_name_from_the_table_option(
    orders: dict[str, Any],
) -> None:
    assert "CREATE TABLE [orders]" in emit(orders, "ddl", table="orders").text
    assert "CREATE TABLE [table]" in emit(orders, "ddl").text


def test_to_dict_declares_format_and_version(orders: dict[str, Any]) -> None:
    result = emit(orders, "jsonschema")
    d = result.to_dict()
    assert d["format"] == "shape-contract-emit" and d["version"] == 1
    assert d["target"] == "jsonschema" and d["text"] == result.text
    assert d["not_expressed"] == result.not_expressed
    assert list(json.loads(json.dumps(d))) == sorted(d)


def test_the_input_contract_is_not_changed(orders: dict[str, Any]) -> None:
    before = json.dumps(orders)
    for target in TARGETS:
        emit(orders, target)
    assert json.dumps(orders) == before


def test_key_order_of_unordered_parts_does_not_change_the_bytes(orders: dict[str, Any]) -> None:
    shuffled = json.loads(json.dumps(orders))
    shuffled["required_columns"].reverse()
    shuffled["columns"]["amount"] = dict(reversed(list(shuffled["columns"]["amount"].items())))
    for target in TARGETS:
        assert emit(orders, target).text == emit(shuffled, target).text


_PROBE = """
import json, sys
from shape.contracts.emit import emit
c = json.load(open(sys.argv[1]))
out = []
for t in ("ddl", "jsonschema", "pandera", "gx"):
    for d in ("tsql", "tsql-fabric-warehouse", "postgres", "mysql") if t == "ddl" else (None,):
        r = emit(c, t, **({"dialect": d} if d else {}))
        out.append(r.text + json.dumps(r.not_expressed, sort_keys=True))
sys.stdout.write("\\x00".join(out))
"""


def test_output_is_byte_identical_across_runs_and_hash_seeds(orders_path: Path) -> None:
    outputs = []
    for seed in ("0", "1", "42", "random"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        run = subprocess.run(
            [sys.executable, "-c", _PROBE, str(orders_path)],
            capture_output=True,
            env=env,
            check=True,
        )
        outputs.append(run.stdout)
    assert len(set(outputs)) == 1 and outputs[0]


def test_emitting_imports_neither_pandera_nor_great_expectations(orders_path: Path) -> None:
    code = (
        "import json, sys\n"
        "from shape.contracts.emit import emit\n"
        f"c = json.load(open({str(orders_path)!r}))\n"
        "[emit(c, t) for t in ('pandera', 'gx', 'jsonschema')]\n"
        "bad = [m for m in ('pandera', 'great_expectations') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


@pytest.mark.parametrize("target", TARGETS)
def test_the_drift_policy_is_not_a_rule(orders: dict[str, Any], target: str) -> None:
    with_drift = {**orders, "drift": {"thresholds": {"mean_shift_std": 1}}}
    assert emit(with_drift, target) == emit(orders, target)


def test_expressible_follows_the_same_split_as_emit(two_tables: dict[str, Any]) -> None:
    from shape.contracts.emit import expressible

    got = expressible(two_tables, "pandera")
    assert set(got["tables"]) == {"orders", "customers"}
    assert "distribution" not in got["tables"]["orders"]["columns"]["amount"]
    assert got["tables"]["orders"]["row_count"] == {"min": 1, "max": 1000000}
    one = expressible(two_tables, "ddl", table="customers", dialect="postgres")
    assert one["columns"]["id"] == {"dtype": "integer", "nullable": False, "unique": True}
    with pytest.raises(EmitError):
        expressible(two_tables, "ddl", dialect="oracle")
