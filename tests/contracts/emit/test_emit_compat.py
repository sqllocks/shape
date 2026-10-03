"""W5-04: ``shape-contract-emit`` is a persisted format: declared, versioned, and old documents
keep reading (the W1-01 compatibility policy)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from shape.contracts.emit import FORMAT, VERSION, EmitError, contract_from, emit

# A version-1 result as the first release wrote it. It stays here unchanged: a later version of
# Shape must still read it.
V1_RESULT = {
    "format": "shape-contract-emit",
    "not_expressed": [
        {
            "column": "amount",
            "reason": "a distribution is a property of many rows, not a rule one row can break",
            "rule": "distribution",
            "table": "table",
        }
    ],
    "target": "jsonschema",
    "text": json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {
                "amount": {
                    "maximum": 10,
                    "minimum": 0,
                    "type": "number",
                    "x-shape": {"distribution": "lognormal"},
                }
            },
            "title": "table",
            "type": "object",
            "x-shape": {
                "format": "shape-contract-emit",
                "table": "table",
                "version": 1,
                "rules": {},
            },
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    "version": 1,
}


def test_the_declared_format_and_version() -> None:
    assert FORMAT == "shape-contract-emit" and VERSION == 1 and isinstance(VERSION, int)
    d = emit({}, "ddl").to_dict()
    assert d["format"] == FORMAT and d["version"] == VERSION


def test_a_version_1_document_still_reads() -> None:
    back = contract_from("jsonschema", V1_RESULT["text"], use_meta=True)  # type: ignore[arg-type]
    assert back == {
        "columns": {
            "amount": {
                "dtype": "float",
                "nullable": False,
                "min": 0,
                "max": 10,
                "distribution": "lognormal",
            }
        }
    }


def test_the_current_output_for_the_same_contract_is_the_version_1_document() -> None:
    contract: dict[str, Any] = {
        "columns": {
            "amount": {
                "dtype": "float",
                "nullable": False,
                "min": 0,
                "max": 10,
                "distribution": "lognormal",
            }
        }
    }
    assert emit(contract, "jsonschema").to_dict() == V1_RESULT


@pytest.mark.parametrize("target", ["jsonschema", "gx"])
def test_a_newer_version_is_refused_with_a_message(target: str) -> None:
    doc = json.loads(emit({"columns": {"a": {"dtype": "integer"}}}, target).text)
    holder = doc["x-shape"] if target == "jsonschema" else doc["meta"]["shape"]
    holder["version"] = VERSION + 1
    with pytest.raises(EmitError, match=r"newer"):
        contract_from(target, json.dumps(doc))
    holder["version"] = "1"
    with pytest.raises(EmitError, match="integer"):
        contract_from(target, json.dumps(doc))
