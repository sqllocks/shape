"""W3-01: JSON Schemas and the compatibility corpus of the four persisted formats.

Every format declares ``format`` and an integer ``version``. The files in ``data/`` are frozen
version-1 documents: they must keep validating against the schema, and the readers must keep
accepting them; a document of a newer version must be refused, never half-read.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from shape.rules import backtest, mutation_test
from shape.rules.history import BacktestError, load_incidents
from shape.rules.mutation import MutationError, load_plan

HERE = Path(__file__).parent
DATA = HERE / "data"
SCHEMAS = Path(__import__("shape").__file__).parent / "schemas"
NAMES = {
    "plan_v1.json": "shape-mutation-plan-v1.schema.json",
    "mutation_report_v1.json": "shape-mutation-report-v1.schema.json",
    "incidents_v1.json": "shape-incidents-v1.schema.json",
    "backtest_report_v1.json": "shape-backtest-report-v1.schema.json",
}
TYPES: dict[str, Any] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def validate(
    value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$"
) -> list[str]:
    """The subset of JSON Schema these schemas use."""
    if "$ref" in schema:
        node: Any = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return validate(value, node, root, path)
    if "type" in schema:
        ts = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(
            isinstance(value, TYPES[x])
            and not (x in ("integer", "number") and isinstance(value, bool))
            for x in ts
        ):
            return [f"{path}: expected {schema['type']}"]
    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: pattern")
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: too short")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    if isinstance(value, dict):
        errors += [f"{path}: missing {k}" for k in schema.get("required", []) if k not in value]
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        for k, v in value.items():
            if k in props:
                errors += validate(v, props[k], root, f"{path}.{k}")
            elif extra is False:
                errors.append(f"{path}: unknown key {k}")
            elif isinstance(extra, dict):
                errors += validate(v, extra, root, f"{path}.{k}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if "items" in schema:
            for i, v in enumerate(value):
                errors += validate(v, schema["items"], root, f"{path}[{i}]")
    return errors


def schema_of(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS / name).read_text())  # type: ignore[no-any-return]


@pytest.mark.parametrize("corpus, schema_name", sorted(NAMES.items()))
def test_the_frozen_version_1_documents_still_validate(corpus: str, schema_name: str) -> None:
    doc = json.loads((DATA / corpus).read_text())
    schema = schema_of(schema_name)
    assert validate(doc, schema, schema) == []
    assert doc["version"] == 1 and isinstance(doc["version"], int) and doc["format"]


@pytest.mark.parametrize("corpus, schema_name", sorted(NAMES.items()))
def test_the_schemas_refuse_a_wrong_format_a_newer_version_and_an_unknown_key(
    corpus: str, schema_name: str
) -> None:
    doc = json.loads((DATA / corpus).read_text())
    schema = schema_of(schema_name)
    assert validate({**doc, "format": "other"}, schema, schema)
    assert validate({**doc, "version": 2}, schema, schema)
    assert validate({**doc, "surprise": 1}, schema, schema)
    assert validate({k: v for k, v in doc.items() if k != "version"}, schema, schema)


def test_the_readers_accept_the_corpus_and_refuse_newer_versions() -> None:
    plan = json.loads((DATA / "plan_v1.json").read_text())
    assert len(load_plan(DATA / "plan_v1.json")) == 3
    with pytest.raises(MutationError, match="version 2"):
        load_plan({**plan, "version": 2})
    assert len(load_incidents(DATA / "incidents_v1.json")) == 2
    incidents = json.loads((DATA / "incidents_v1.json").read_text())
    with pytest.raises(BacktestError, match="version 2"):
        load_incidents({**incidents, "version": 2})
    with pytest.raises(BacktestError, match="integer 'version'"):
        load_incidents({**incidents, "version": "1"})
    with pytest.raises(MutationError, match="integer 'version'"):
        load_plan({**plan, "version": "1"})


def test_the_corpus_plan_and_contract_still_give_the_corpus_report() -> None:
    import pyarrow as pa

    t = pa.table(
        {
            "order_id": list(range(1, 61)),
            "status": [["new", "paid"][i % 2] for i in range(60)],
            "amount": [float(10 + i % 7) for i in range(60)],
        }
    )
    got = mutation_test(
        {"orders": t}, DATA / "contract_v1.json", plan=DATA / "plan_v1.json", seed=3
    )
    assert got.to_dict() == json.loads((DATA / "mutation_report_v1.json").read_text())


def test_reports_written_today_validate_against_the_schemas(tmp_path: Path) -> None:
    import pyarrow as pa

    import shape
    from shape.registry import LocalRegistry

    t = pa.table({"id": list(range(80)), "s": [["a", "b"][i % 2] for i in range(80)]})
    rep = mutation_test({"t": t}, {"columns": {"id": {"unique": True}}}, seed=1).to_dict()
    schema = schema_of("shape-mutation-report-v1.schema.json")
    assert validate(rep, schema, schema) == []
    reg = LocalRegistry(tmp_path / "r")
    for n in range(2):
        shape.save(shape.profile(t, name="t"), tmp_path / "p.shape")
        reg.commit(
            "t",
            (tmp_path / "p.shape").read_bytes(),
            {"business_date": f"2026-05-0{n + 1}"},
            allow_raw=True,
        )
    inc = {
        "format": "shape-incidents",
        "version": 1,
        "incidents": [{"id": "a", "from": "2026-05-01"}],
    }
    contract = {"columns": {"id": {"max": 5, "unique": True}}}
    for window in ("day", "week", "month"):
        doc = backtest(reg, "t", contract, window=window, incidents=inc, compare={}).to_dict()
        schema = schema_of("shape-backtest-report-v1.schema.json")
        assert validate(doc, schema, schema) == [], window
    (tmp_path / "bad").write_bytes(b"x")
    reg.commit("t", b"not a profile", {"business_date": "2026-05-03"})
    doc = backtest(reg, "t", contract).to_dict()
    assert any(e.get("reason") for e in doc["entries"]) and validate(doc, schema, schema) == []
