"""W5-04 item 3 (``pandera``): a Python source file with a ``DataFrameSchema`` per table."""

from __future__ import annotations

import ast
from typing import Any

import pytest

from shape.contracts.emit import EmitError, contract_from, emit


def source(contract: dict[str, Any], **kw: Any) -> str:
    return emit(contract, "pandera", **kw).text


def test_the_text_is_valid_python_and_states_the_mapping(orders: dict[str, Any]) -> None:
    text = source(orders, table="orders")
    ast.parse(text)
    assert "pa.Check, pa.Column, pa.DataFrameSchema" in text
    assert '"orders": DataFrameSchema(' in text
    assert '"order_id": Column("int64", nullable=False, unique=True, checks=[Check.ge(1)])' in text
    assert "Check.in_range(0, 100000)" in text
    assert 'Check.isin(["new", "paid", "shipped", "cancelled"])' in text
    assert "Check.str_matches(" in text
    assert '"discount_code": Column("str", nullable=True, required=False, metadata=' in text
    assert '{"shape": {"max_null_rate": 0.8}}' in text
    assert "strict=True" in text
    assert "1 <= len(df) <= 1000000" in text
    assert text.index("SCHEMAS = {") < text.index('"orders": DataFrameSchema(')


def test_what_pandera_cannot_express(orders: dict[str, Any]) -> None:
    got = {(i["column"], i["rule"]) for i in emit(orders, "pandera").not_expressed}
    assert got == {
        ("amount", "distribution"),
        ("discount_code", "max_null_rate"),
        ("is_gift", "min_true_rate"),
        ("is_gift", "max_true_rate"),
        (None, "fd"),
    }


def test_nullable_integers_and_booleans_use_the_nullable_pandas_types() -> None:
    c = {
        "columns": {
            "i": {"dtype": "integer"},
            "j": {"dtype": "integer", "nullable": False},
            "b": {"dtype": "boolean"},
            "c": {"dtype": "boolean", "nullable": False},
            "f": {"dtype": "float"},
            "d": {"dtype": "date"},
            "t": {"dtype": "datetime"},
            "s": {"dtype": "string"},
            "n": {},
        }
    }
    text = source(c)
    ast.parse(text)
    for want in (
        'Column("Int64"',
        'Column("int64"',
        'Column("boolean"',
        'Column("bool"',
        'Column("float64"',
        'Column("datetime64[ns]"',
        'Column("str"',
    ):
        assert want in text, want
    assert '"d": Column(pa.Date, nullable=True, required=False)' in text


def test_required_columns_are_required_and_other_columns_are_optional() -> None:
    c = {
        "required_columns": ["a", "z"],
        "columns": {"a": {"dtype": "integer"}, "b": {"dtype": "integer"}},
    }
    text = source(c)
    assert '"b": Column(' in text and "required=False" in text
    assert text.count("required=False") == 1
    assert '"z": Column(' in text


def test_one_sided_and_degenerate_ranges() -> None:
    c = {
        "columns": {
            "lo": {"dtype": "integer", "min": 0},
            "hi": {"dtype": "integer", "max": 9},
            "eq": {"dtype": "integer", "min": 5, "max": 5},
        }
    }
    text = source(c)
    assert "Check.ge(0)" in text and "Check.le(9)" in text and "Check.in_range(5, 5)" in text


def test_row_count_bounds() -> None:
    assert "1 <= len(df) <= 10" in source({"row_count": {"min": 1, "max": 10}})
    assert "len(df) >= 3" in source({"row_count": {"min": 3}})
    assert "len(df) <= 0" in source({"row_count": {"max": 0}})
    assert "checks=" not in source({"row_count": {}})


def test_literals_are_python_literals() -> None:
    c = {
        "columns": {
            "s": {"dtype": "string", "allowed_values": ["it's", 'say "x"', "\\", None]},
            "b": {"dtype": "boolean", "allowed_values": [True, False]},
        }
    }
    text = source(c)
    ast.parse(text)
    assert 'Check.isin(["it\'s", "say \\"x\\"", "\\\\"])' in text
    assert "Check.isin([True, False])" in text


def test_names_that_are_not_identifiers_are_fine() -> None:
    text = source({"columns": {"my col": {"dtype": "integer"}, 'q"uote': {"unique": True}}})
    ast.parse(text)
    assert '"my col"' in text and '"q\\"uote"' in text


def test_there_is_no_reader_for_pandera() -> None:
    with pytest.raises(EmitError, match="jsonschema.*gx"):
        contract_from("pandera", "x")


# -- with pandera installed ---------------------------------------------------------------


def schema_from(text: str, name: str) -> Any:
    ns: dict[str, Any] = {}
    exec(compile(text, "<emitted>", "exec"), ns)  # noqa: S102 - the text is what we emitted
    return ns["SCHEMAS"][name]


def frame(**overrides: Any) -> Any:
    pd = pytest.importorskip("pandas", reason="pandas is not installed")
    row = {
        "order_id": 7,
        "customer_email": "a@example.com",
        "status": "paid",
        "amount": 12.5,
        "discount_code": None,
        "is_gift": False,
        "placed_at": "2026-10-03T10:00:00",
    }
    row.update(overrides)
    df = pd.DataFrame([row, {**row, "order_id": 8}])
    df = df.astype(
        {"order_id": "int64", "discount_code": "str", "is_gift": "boolean", "amount": "float64"}
    )
    df["placed_at"] = pd.to_datetime(df["placed_at"]).astype("datetime64[ns]")
    return df


@pytest.fixture
def pandera_schema(orders: dict[str, Any]) -> Any:
    pytest.importorskip("pandera", reason="pandera is not installed")
    pytest.importorskip("pandas", reason="pandas is not installed")
    return schema_from(source(orders, table="orders"), "orders")


def test_a_conforming_frame_passes(pandera_schema: Any) -> None:
    pandera_schema.validate(frame(), lazy=True)


@pytest.mark.parametrize(
    "change",
    [
        {"order_id": 0},  # min
        {"amount": -1.0},  # min
        {"amount": 100001.0},  # max
        {"status": "lost"},  # allowed_values
        {"customer_email": "nope"},  # pattern
        {"amount": None},  # nullable false
        {"order_id": 8},  # unique (both rows 8)
    ],
)
def test_each_broken_rule_fails(pandera_schema: Any, change: dict[str, Any]) -> None:
    pa = pytest.importorskip("pandera")
    df = frame(**change)
    with pytest.raises((pa.errors.SchemaError, pa.errors.SchemaErrors)):
        pandera_schema.validate(df, lazy=True)


def test_extra_and_missing_columns_and_row_count_fail(pandera_schema: Any) -> None:
    pa = pytest.importorskip("pandera")
    errors = (pa.errors.SchemaError, pa.errors.SchemaErrors)
    with pytest.raises(errors):
        pandera_schema.validate(frame().assign(extra=1), lazy=True)
    with pytest.raises(errors):
        pandera_schema.validate(frame().drop(columns=["amount"]), lazy=True)
    with pytest.raises(errors):
        pandera_schema.validate(frame().iloc[0:0], lazy=True)
