"""W1-04: the persisted-format compatibility test and the JSON Schema."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from shape.project import FORMAT, VERSION, load_project, problems, schema
from shape.schemacheck import validate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "project"


def test_every_frozen_version_still_loads_and_validates():
    found = sorted(FIXTURES.glob("v*/shape.yml"))
    assert found, "the time-capsule fixtures are missing"
    for path in found:
        p = load_project(path)
        assert p.version <= VERSION
        assert p.document["format"] == FORMAT


def test_v1_capsule_content():
    p = load_project(FIXTURES / "v1" / "shape.yml")
    assert p.version == 1
    assert sorted(p.sources) == ["events", "orders"]
    assert p.source("events").baseline.ref == "production"
    assert p.source("orders").annotations_of("amount") == {"unit": "EUR", "pii": "no"}


def test_schema_is_shipped_and_declares_format_and_version():
    text = resources.files("shape").joinpath("schemas/shape-project-v1.schema.json").read_text()
    s = json.loads(text)
    assert s == schema()
    assert s["$schema"].endswith("2020-12/schema")
    assert s["properties"]["format"] == {"const": FORMAT}
    assert s["properties"]["version"]["type"] == "integer"
    assert s["additionalProperties"] is False
    assert set(s["required"]) == {"format", "version", "sources"}


def test_schema_thresholds_are_the_drift_engines_thresholds():
    from shape.drift.engine import DEFAULT_THRESHOLDS

    assert set(schema()["$defs"]["thresholds"]["properties"]) == set(DEFAULT_THRESHOLDS)


def test_capsule_conforms_to_the_json_schema():
    p = load_project(FIXTURES / "v1" / "shape.yml")
    assert validate(p.document, schema()) == []


def test_schema_and_validator_agree_on_structure():
    """Structural rules come from the schema itself, so a document the schema refuses is
    refused by ``problems`` too."""
    bad = [
        {"format": FORMAT, "version": 1},
        {"format": FORMAT, "version": 1, "sources": {"a": {"path": "x", "dataset": 1}}},
        {"format": FORMAT, "version": 1, "sources": {"a": {"path": "x", "zzz": 1}}},
        {"format": FORMAT, "version": 1.5, "sources": {}},
        {"format": FORMAT, "version": 1, "sources": {}, "gates": {"g": {"mode": "warn"}}},
    ]
    for document in bad:
        assert validate(document, schema()), document
        assert problems(document), document
