"""``GenSchema.clone`` and the ``validated`` path of ``GenSchema.from_dict`` (P6-01-perf round 4):
a copy shares nothing mutable with the original, and a schema that is trusted parses to the same
object as one that is checked."""

from __future__ import annotations

import copy
import dataclasses
import json
from typing import Any

import pytest
from gen_fixtures import schema

import shape.generation.schema as schema_module
from shape.generation.domains import domain_names, load_domain
from shape.generation.schema import GenSchema, GenSchemaError


def _mutables(value: Any, found: dict[int, Any] | None = None) -> dict[int, Any]:
    """Every mutable container and dataclass reachable from ``value``, by identity."""
    found = {} if found is None else found
    if isinstance(value, dict):
        found[id(value)] = value
        for v in value.values():
            _mutables(v, found)
    elif isinstance(value, (list, set)):
        found[id(value)] = value
        for v in value:
            _mutables(v, found)
    elif isinstance(value, tuple):
        for v in value:
            _mutables(v, found)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        found[id(value)] = value
        for f in dataclasses.fields(value):
            _mutables(getattr(value, f.name), found)
    return found


def _schemas() -> list[tuple[str, GenSchema]]:
    out = [("fixture", schema())]
    for name in domain_names():
        for mode in ("3nf", "star"):
            out.append((f"{name}/{mode}", load_domain(name, mode=mode).schema))
    return out


@pytest.mark.parametrize(
    ("label", "original"), _schemas(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_clone_equals_the_original_and_shares_nothing(label: str, original: GenSchema) -> None:
    copied = original.clone()
    assert copied == original
    assert copied == copy.deepcopy(original)
    assert copied.to_dict() == original.to_dict()
    assert not set(_mutables(copied)) & set(_mutables(original))


def test_changing_the_clone_leaves_the_original_alone() -> None:
    original = schema()
    before = json.dumps(original.to_dict(), sort_keys=True)
    copied = original.clone()
    copied.generation.scale = "large"
    copied.model.seed = 7
    table = next(iter(copied.tables.values()))
    column = next(iter(table.columns.values()))
    column.generator["strategy"] = "changed"
    column.generator.setdefault("added", []).append(1)
    table.primary_key.append("x")
    assert json.dumps(original.to_dict(), sort_keys=True) == before


@pytest.mark.parametrize("name", domain_names())
def test_trusted_parse_equals_checked_parse(name: str) -> None:
    for mode in ("3nf", "star"):
        document = json.loads(json.dumps(load_domain(name, mode=mode).schema.to_dict()))
        checked = GenSchema.from_dict(document)
        trusted = GenSchema.from_dict(document, validated=True)
        assert trusted == checked
        assert not set(_mutables(trusted)) & set(_mutables(document))


def test_the_trusted_parse_skips_the_check_and_the_default_keeps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = schema().to_dict()
    calls: list[int] = []
    real = schema_module.schema_problems

    def counting(doc: Any) -> list[str]:
        calls.append(1)
        return real(doc)

    monkeypatch.setattr(schema_module, "schema_problems", counting)
    GenSchema.from_dict(document, validated=True)
    assert calls == []
    GenSchema.from_dict(document)
    assert calls == [1]
    broken = schema().to_dict()
    broken.pop("tables")
    with pytest.raises(GenSchemaError):
        GenSchema.from_dict(broken)
