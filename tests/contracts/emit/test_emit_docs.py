"""W5-04 item 5: the worked example in ``docs/CONTRACT_EMIT.md`` is what the command emits."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from shape.contracts.emit import emit

DOC = Path(__file__).resolve().parents[3] / "docs" / "CONTRACT_EMIT.md"


def blocks(lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def test_the_sample_contract_in_the_doc_is_the_example_file(orders: dict[str, Any]) -> None:
    docs = [b for b in blocks("json") if b.lstrip().startswith('{\n  "row_count"')]
    assert len(docs) == 1
    assert json.loads(docs[0]) == orders


def test_the_ddl_block_is_what_the_ddl_target_emits(orders: dict[str, Any]) -> None:
    (sql,) = blocks("sql")
    text = emit(orders, "ddl", table="orders").text
    assert sql.strip() in text


@pytest.mark.parametrize(
    ("target", "lang", "needle"),
    [
        ("jsonschema", "json", '"amount": {"maximum": 100000'),
        ("pandera", "python", '"amount": Column("float64"'),
        ("gx", "json", '{"type": "expect_column_values_to_be_between"'),
    ],
)
def test_the_one_column_snippets_are_in_the_output(
    orders: dict[str, Any], target: str, lang: str, needle: str
) -> None:
    (snippet,) = [b for b in blocks(lang) if needle in b]
    text = emit(orders, target, table="orders").text
    if target == "pandera":
        assert squash(snippet).rstrip(",") in squash(text)
    else:
        wanted = (
            json.loads("{" + snippet.strip().rstrip(",") + "}")
            if target == "jsonschema"
            else (json.loads(snippet))
        )
        doc = json.loads(text)
        if target == "jsonschema":
            assert doc["properties"]["amount"] == wanted["amount"]
        else:
            assert wanted in doc["expectations"]


def test_the_not_expressed_table_matches_the_results(orders: dict[str, Any]) -> None:
    ddl = {(i["column"], i["rule"]) for i in emit(orders, "ddl").not_expressed}
    assert (None, "row_count") in ddl and ("customer_email", "pattern") in ddl
    js = {(i["column"], i["rule"]) for i in emit(orders, "jsonschema").not_expressed}
    assert ("order_id", "unique") in js and ("customer_email", "pattern") not in js
    pa = {(i["column"], i["rule"]) for i in emit(orders, "pandera").not_expressed}
    assert (None, "row_count") not in pa and ("order_id", "unique") not in pa
    gx = {(i["column"], i["rule"]) for i in emit(orders, "gx").not_expressed}
    assert ("discount_code", "max_null_rate") not in gx and ("amount", "dtype") in gx
