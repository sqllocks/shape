"""W5-04 item 3 (``gx``): a Great Expectations 1.x expectation suite as JSON."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from shape.contracts.emit import EmitError, contract_from, emit, expressible
from shape.contracts.emit._common import normalize_table


def suite_of(contract: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return json.loads(emit(contract, "gx", **kw).text)  # type: ignore[no-any-return]


def of_type(suite: dict[str, Any], type_: str) -> list[dict[str, Any]]:
    return [e for e in suite["expectations"] if e["type"] == type_]


def test_the_suite_has_the_expectations_of_the_mapping(orders: dict[str, Any]) -> None:
    s = suite_of(orders, table="orders")
    assert s["name"] == "orders"
    types = {e["type"] for e in s["expectations"]}
    assert types == {
        "expect_column_to_exist",
        "expect_column_values_to_not_be_null",
        "expect_column_values_to_be_unique",
        "expect_column_values_to_be_between",
        "expect_column_values_to_be_in_set",
        "expect_column_values_to_match_regex",
        "expect_table_row_count_to_be_between",
        "expect_table_columns_to_match_set",
    }
    assert [e["kwargs"]["column"] for e in of_type(s, "expect_column_to_exist")] == sorted(
        orders["required_columns"]
    )
    assert of_type(s, "expect_table_row_count_to_be_between")[0]["kwargs"] == {
        "min_value": 1,
        "max_value": 1000000,
    }
    assert of_type(s, "expect_column_values_to_be_unique")[0]["kwargs"] == {"column": "order_id"}
    between = {
        e["kwargs"]["column"]: e["kwargs"] for e in of_type(s, "expect_column_values_to_be_between")
    }
    assert between["order_id"] == {"column": "order_id", "min_value": 1}
    assert between["amount"] == {"column": "amount", "min_value": 0, "max_value": 100000}
    in_set = of_type(s, "expect_column_values_to_be_in_set")[0]["kwargs"]
    assert in_set == {"column": "status", "value_set": ["new", "paid", "shipped", "cancelled"]}
    regex = of_type(s, "expect_column_values_to_match_regex")[0]["kwargs"]
    assert regex["column"] == "customer_email" and regex["mostly"] == 0.9
    assert of_type(s, "expect_table_columns_to_match_set")[0]["kwargs"] == {
        "column_set": sorted(orders["columns"]),
        "exact_match": True,
    }


def test_mostly_comes_from_max_null_rate_and_boundaries_hold() -> None:
    def mostly(rate: float) -> Any:
        s = suite_of({"columns": {"a": {"max_null_rate": rate}}})
        (e,) = of_type(s, "expect_column_values_to_not_be_null")
        return e["kwargs"].get("mostly")

    assert mostly(0.8) == 0.2
    assert mostly(0.1) == 0.9
    assert mostly(0.3) == 0.7  # no 0.7000000000000001
    assert mostly(0) == 1.0
    assert mostly(1) == 0.0
    s = suite_of({"columns": {"a": {"nullable": False}}})
    assert of_type(s, "expect_column_values_to_not_be_null")[0]["kwargs"] == {"column": "a"}


def test_nullable_false_and_max_null_rate_give_two_expectations() -> None:
    s = suite_of({"columns": {"a": {"nullable": False, "max_null_rate": 0.5}}})
    got = of_type(s, "expect_column_values_to_not_be_null")
    assert [e["meta"]["shape_rule"] for e in got] == ["nullable", "max_null_rate"]


def test_what_great_expectations_cannot_express_is_listed_and_kept_in_meta(
    orders: dict[str, Any],
) -> None:
    result = emit(orders, "gx")
    got = {(i["column"], i["rule"]) for i in result.not_expressed}
    assert got == {
        ("order_id", "dtype"),
        ("customer_email", "dtype"),
        ("status", "dtype"),
        ("amount", "dtype"),
        ("amount", "distribution"),
        ("discount_code", "dtype"),
        ("is_gift", "dtype"),
        ("is_gift", "min_true_rate"),
        ("is_gift", "max_true_rate"),
        ("placed_at", "dtype"),
        (None, "fd"),
    }
    meta = json.loads(result.text)["meta"]["shape"]
    assert meta["format"] == "shape-contract-emit" and meta["version"] == 1
    assert meta["table"] == "table"
    assert meta["rules"]["fd"] == orders["fd"]
    assert meta["columns"]["amount"] == {"dtype": "float", "distribution": "lognormal"}
    assert "status" in meta["columns"] and "dtype" in meta["columns"]["status"]


def test_empty_sets_and_unknown_labels_are_not_expressed() -> None:
    c = {
        "columns": {
            "a": {"allowed_values": []},
            "b": {"pattern": "nope"},
            "c": {"min": "x"},
        }
    }
    result = emit(c, "gx")
    assert {(i["column"], i["rule"]) for i in result.not_expressed} == {
        ("a", "allowed_values"),
        ("b", "pattern"),
        ("c", "min"),
    }
    assert json.loads(result.text)["expectations"] == []


def test_the_same_suite_for_the_same_contract_has_no_ids_or_timestamps(
    orders: dict[str, Any],
) -> None:
    s = suite_of(orders)
    assert set(s) == {"name", "expectations", "meta"}
    assert set(s["meta"]) == {"shape"}


# -- contract_from ------------------------------------------------------------------------


def test_round_trip_expressible_part_and_with_metadata(orders: dict[str, Any]) -> None:
    text = emit(orders, "gx").text
    back = contract_from("gx", text)
    assert back == expressible(orders, "gx")
    assert back["columns"]["discount_code"] == {"max_null_rate": 0.8}
    assert "dtype" not in back["columns"]["amount"]
    assert contract_from("gx", text, use_meta=True) == normalize_table(orders)


def test_round_trip_on_tables(two_tables: dict[str, Any]) -> None:
    for name in ("orders", "customers"):
        text = emit(two_tables, "gx", table=name).text
        assert contract_from("gx", text) == expressible(two_tables, "gx", table=name)
        assert contract_from("gx", text, use_meta=True) == normalize_table(
            two_tables["tables"][name]
        )


@pytest.mark.parametrize(
    "contract",
    [
        {},
        {"columns": {"a": {"max_null_rate": 0}}},
        {"columns": {"a": {"max_null_rate": 1}, "b": {"nullable": False, "max_null_rate": 0.25}}},
        {"columns": {"a": {"min": 1, "max": 1}}},
        {"row_count": {"min": 0}, "allow_extra_columns": False},
        {"required_columns": ["b", "a"]},
        {"columns": {"a": {"allowed_values": ["x", None]}}},
    ],
)
def test_round_trip_small_contracts(contract: dict[str, Any]) -> None:
    text = emit(contract, "gx").text
    assert contract_from("gx", text) == expressible(contract, "gx")
    assert contract_from("gx", text, use_meta=True) == normalize_table(contract)


def test_contract_from_rejects_what_it_cannot_read(orders: dict[str, Any]) -> None:
    with pytest.raises(EmitError, match="not valid JSON"):
        contract_from("gx", "{")
    with pytest.raises(EmitError, match="suite"):
        contract_from("gx", "[]")
    s = suite_of(orders)
    s["meta"]["shape"]["version"] = 9
    with pytest.raises(EmitError, match="version 9.*newer"):
        contract_from("gx", json.dumps(s))


def test_a_suite_written_by_hand_is_read_for_what_it_has() -> None:
    suite = {
        "name": "x",
        "expectations": [
            {"type": "expect_column_values_to_not_be_null", "kwargs": {"column": "a"}, "meta": {}},
            {"type": "expect_column_values_to_be_unique", "kwargs": {"column": "a"}, "meta": {}},
            {"type": "expect_column_kl_divergence_to_be_less_than", "kwargs": {}, "meta": {}},
        ],
    }
    assert contract_from("gx", json.dumps(suite)) == {
        "columns": {"a": {"nullable": False, "unique": True}}
    }


# -- with Great Expectations installed ----------------------------------------------------


_VALIDATE = """
import json, sys
import great_expectations as gx
import pandas as pd

suite = gx.ExpectationSuite(**json.load(open(sys.argv[1], encoding="utf-8")))
df = pd.read_pickle(sys.argv[2])
ctx = gx.get_context(mode="ephemeral")
asset = ctx.data_sources.add_pandas("pd").add_dataframe_asset("a")
batch = asset.add_batch_definition_whole_dataframe("b").get_batch(
    batch_parameters={"dataframe": df}
)
print("RESULT " + json.dumps(bool(batch.validate(suite).success)))
"""


def validate(suite_text: str, df: Any) -> bool:
    """Validate ``df`` against the suite in a subprocess: importing Great Expectations puts cloud
    SDK names in ``sys.modules``, which other tests in the run assert are absent. The script goes
    in on stdin: with ``-c``, a pyspark check that Great Expectations triggers ends the process."""
    if importlib.util.find_spec("great_expectations") is None:
        pytest.skip("great_expectations is not installed")
    pytest.importorskip("pandas", reason="pandas is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        suite_path, frame_path = Path(tmp, "suite.json"), Path(tmp, "frame.pkl")
        suite_path.write_text(suite_text, encoding="utf-8")
        df.to_pickle(frame_path)
        run = subprocess.run(
            [sys.executable, "-", str(suite_path), str(frame_path)],
            input=_VALIDATE,
            capture_output=True,
            text=True,
            check=False,
        )
    lines = [line for line in run.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, run.stderr[-2000:]
    return bool(json.loads(lines[-1][len("RESULT ") :]))


def frame(rows: int = 20, **change: Any) -> Any:
    pd = pytest.importorskip("pandas", reason="pandas is not installed")
    df = pd.DataFrame(
        {
            "order_id": list(range(1, rows + 1)),
            "customer_email": ["a@example.com"] * rows,
            "status": ["paid"] * rows,
            "amount": [10.0] * rows,
            "discount_code": [None] * (rows // 2) + ["X"] * (rows - rows // 2),
            "is_gift": [False] * rows,
            "placed_at": pd.to_datetime(["2026-10-03"] * rows),
        }
    )
    for key, value in change.items():
        df[key] = value
    return df


def test_a_conforming_frame_passes_the_suite(orders: dict[str, Any]) -> None:
    assert validate(emit(orders, "gx").text, frame())


@pytest.mark.parametrize(
    "change",
    [
        {"order_id": 0},  # min and unique
        {"amount": 100001.0},  # max
        {"status": "lost"},  # in set
        {"customer_email": "nope"},  # regex
        {"order_id": None},  # not null
        {"order_id": 5},  # unique
        {"discount_code": None},  # fine: nullable, but the rate is 1 > 0.8 -> fails mostly
    ],
)
def test_each_broken_rule_fails_the_suite(orders: dict[str, Any], change: dict[str, Any]) -> None:
    assert not validate(emit(orders, "gx").text, frame(**change))


def test_extra_missing_columns_and_row_count_fail(orders: dict[str, Any]) -> None:
    text = emit(orders, "gx").text
    assert not validate(text, frame().assign(extra=1))
    assert not validate(text, frame().drop(columns=["amount"]))
    assert not validate(text, frame().iloc[0:0])
