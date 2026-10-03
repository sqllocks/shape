"""W1-02: the JSON Schema of the decision file and its compatibility corpus."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.proposals import DecisionError, DecisionFile

HERE = Path(__file__).parent
SCHEMA = Path(__import__("shape").__file__).parent / "schemas" / "decisions-v1.schema.json"
CORPUS = sorted((HERE / "data").glob("decisions_v1*.json"))


def validate(value, schema, root, path="$"):
    """The subset of JSON Schema the decision schema uses (type, enum, const, required, properties,
    additionalProperties, items, pattern, minimum, maximum, minLength, $ref, oneOf)."""
    import re

    errors: list[str] = []
    if "$ref" in schema:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return validate(value, node, root, path)
    if "oneOf" in schema:
        ok = [not validate(value, s, root, path) for s in schema["oneOf"]]
        return [] if sum(ok) == 1 else [f"{path}: oneOf matched {sum(ok)}"]
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    if "type" in schema:
        t = schema["type"]
        ts = t if isinstance(t, list) else [t]
        okay = any(
            isinstance(value, types[x])
            and not (x in ("integer", "number") and isinstance(value, bool))
            for x in ts
        )
        if not okay:
            return [f"{path}: expected {t}"]
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
        for k in schema.get("required", []):
            if k not in value:
                errors.append(f"{path}: missing {k}")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                errors += validate(v, props[k], root, f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unknown key {k}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += validate(v, schema["additionalProperties"], root, f"{path}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errors += validate(v, schema["items"], root, f"{path}[{i}]")
    return errors


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def test_the_schema_ships_and_names_the_format_and_version(schema):
    assert schema["$schema"].startswith("https://json-schema.org/")
    assert schema["properties"]["format"]["const"] == "shape-decisions"
    assert schema["properties"]["version"]["const"] == 1


def test_the_corpus_is_not_empty():
    assert CORPUS, "tests/proposals/data/decisions_v1*.json must hold the version 1 corpus"


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.name)
def test_every_version_1_file_validates_loads_and_round_trips_byte_for_byte(path, schema):
    text = path.read_text(encoding="utf-8")
    assert validate(json.loads(text), schema, schema) == []
    assert DecisionFile.loads(text).dumps() == text  # the corpus is in canonical form


def test_files_the_writer_produces_validate_against_the_schema(schema):
    from .conftest import LATER, NOW
    from .test_decision_file import prop

    f = DecisionFile.empty()
    f.update([prop("pii:a.x"), prop("semantic:a.x", claim={"semantic": "email"})], now=NOW)
    f.decide("pii:a.x", "deferred", actor="ana", note="", now=LATER)
    assert validate(f.to_dict(), schema, schema) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(version=2),
        lambda d: d.update(format="x"),
        lambda d: d["proposals"][0].update(confidence=2),
        lambda d: d["decisions"][0].update(status="maybe"),
        lambda d: d["proposals"][0].update(proposed_at="soon"),
        lambda d: d.update(extra=1),
    ],
)
def test_the_schema_rejects_what_the_reader_rejects(mutate, schema):
    doc = json.loads(CORPUS[0].read_text(encoding="utf-8"))
    mutate(doc)
    assert validate(doc, schema, schema)
    with pytest.raises(DecisionError):
        DecisionFile.from_dict(doc)
