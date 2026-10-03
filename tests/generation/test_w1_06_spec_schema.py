"""W1-06 (#64) deliverable 1: the published JSON Schema of the generation spec covers every
built-in strategy and its parameters, and cannot drift from the strategy registry or
``shape.generation.spec_keys``."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

import pytest

from shape import schemacheck
from shape.builtins.catalog import BUILTINS
from shape.generation import spec_keys
from shape.generation.schema import STRATEGY_REQUIRED_KEYS, GenSchema, schema_problems
from shape.generation.spec_schema import LEGACY_IGNORED_KEYS, build_schema, published_schema

REGISTRY = sorted(name for group, name, _ in BUILTINS if group == "shape.strategies")


def _doc(generator: dict[str, Any], **column: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "model": {"name": "t", "seed": 1},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": ["id"],
                "columns": {
                    "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}},
                    "v": {"name": "v", "type": "float", "generator": generator, **column},
                },
            }
        },
    }


def _problems(doc: Any) -> list[str]:
    return schemacheck.validate(doc, published_schema())


def _branches() -> dict[str, dict[str, Any]]:
    """strategy -> the ``then`` block of its branch in ``$defs.generator``."""
    out: dict[str, dict[str, Any]] = {}
    for branch in published_schema()["$defs"]["generator"]["allOf"]:
        out[branch["if"]["properties"]["strategy"]["const"]] = branch["then"]
    return out


def test_the_shipped_file_is_what_the_builder_makes() -> None:
    text = resources.files("shape").joinpath("schemas/generation-spec-v1.schema.json").read_text()
    assert json.loads(text) == build_schema()
    assert text == json.dumps(build_schema(), indent=2) + "\n"


def test_the_schema_declares_its_format_and_version() -> None:
    schema = published_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith("/generation-spec-v1.schema.json")
    assert schema["x-shape-format"] == "generation-spec"
    assert schema["x-shape-version"] == 1
    assert schema["properties"]["schema_version"] == {"const": 1}


def test_registry_spec_keys_and_schema_name_the_same_strategies() -> None:
    assert set(REGISTRY) == set(spec_keys.STRATEGY_KEYS) == set(_branches())
    assert set(STRATEGY_REQUIRED_KEYS) == set(REGISTRY)


@pytest.mark.parametrize("strategy", REGISTRY)
def test_each_branch_lists_exactly_the_keys_the_code_reads(strategy: str) -> None:
    then = _branches()[strategy]
    if strategy == "distribution":
        # the keys of a distribution depend on its family; each family has its own branch
        assert set(then["properties"]) >= {"distribution", "params", "min", "max"}
        return
    expected = (
        spec_keys.COMMON
        | spec_keys.STRATEGY_KEYS[strategy]
        | LEGACY_IGNORED_KEYS.get(strategy, set())
    )
    assert set(then["properties"]) == set(expected)
    assert then["additionalProperties"] is False
    assert set(then.get("required", [])) == set(STRATEGY_REQUIRED_KEYS[strategy])
    nested = spec_keys.PARAMS_KEYS.get(strategy)
    if nested is not None:
        assert set(then["properties"]["params"]["properties"]) == set(nested)


@pytest.mark.parametrize("family", sorted(spec_keys.FAMILY_KEYS))
def test_each_distribution_family_lists_exactly_its_keys(family: str) -> None:
    families = _branches()["distribution"]["allOf"]
    found = [f for f in families if f'"const": "{family}"' in json.dumps(f["if"])]
    assert found, family
    allowed = (
        spec_keys.COMMON | spec_keys.STRATEGY_KEYS["distribution"] | spec_keys.FAMILY_KEYS[family]
    )
    assert set(found[0]["then"]["properties"]) == set(allowed)
    inner = allowed - {"params", "distribution", "strategy", "output_type"}
    assert set(found[0]["then"]["properties"]["params"]["properties"]) == set(inner)


def _index_of(schema: dict[str, Any], strategy: str) -> int:
    for i, b in enumerate(schema["$defs"]["generator"]["allOf"]):
        if b["if"]["properties"]["strategy"]["const"] == strategy:
            return i
    raise AssertionError(strategy)


def test_a_new_key_in_spec_keys_reaches_the_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    patched = dict(spec_keys.STRATEGY_KEYS)
    patched["constant"] = patched["constant"] | {"added_for_the_test"}
    monkeypatch.setattr(spec_keys, "STRATEGY_KEYS", patched)
    built = build_schema()
    branch = built["$defs"]["generator"]["allOf"][_index_of(built, "constant")]
    assert "added_for_the_test" in branch["then"]["properties"]


def test_every_shipped_domain_is_accepted() -> None:
    from shape.generation.domains import domain_names, load_domain

    names = domain_names()
    assert names
    for name in names:
        assert _problems(load_domain(name).schema.to_dict()) == [], name


def test_a_ddl_schema_and_the_example_file_are_accepted() -> None:
    from pathlib import Path

    from shape.generation.ddl import from_ddl

    ddl, _ = from_ddl(
        "CREATE TABLE a (id INT PRIMARY KEY, name VARCHAR(40), amount DECIMAL(10,2), "
        "made DATETIME, b INT REFERENCES a(id));"
    )
    assert _problems(ddl.to_dict()) == []
    example = Path(__file__).parents[2] / "examples" / "customer.shape.json"
    doc = json.loads(example.read_text("utf-8"))
    if "tables" in doc and doc.get("schema_version") == 1:
        assert _problems(doc) == []


def test_a_plugin_strategy_and_a_plugin_family_stay_open() -> None:
    assert _problems(_doc({"strategy": "my_plugin", "anything": [1, 2], "goes": {"here": 1}})) == []
    assert (
        _problems(_doc({"strategy": "distribution", "distribution": "plugin_dist", "q": 1})) == []
    )
    assert _problems(_doc({"strategy": "distribution", "low": 0.0, "high": 9.0})) == []
    assert _problems(_doc({})) == []  # an empty generator is a validator warning, not an error


@pytest.mark.parametrize(
    ("generator", "fragment"),
    [
        ({"strategy": "sequence", "strt": 5}, "unexpected key 'strt'"),
        ({"strategy": "constant"}, "missing required key 'value'"),
        ({"strategy": "faker", "provider": 3}, "expected ['string']"),
        ({"strategy": "foreign_key", "ref": "a.b", "distribtion": "zipf"}, "'distribtion'"),
        ({"strategy": "distribution", "distribution": "normal", "sigmaa": 1}, "'sigmaa'"),
        (
            {"strategy": "distribution", "distribution": "normal", "params": {"std_devv": 2}},
            "'std_devv'",
        ),
        ({"strategy": "distribution", "min": 1, "mu": 2}, "'mu'"),  # uniform by default
        ({"strategy": "temporal", "pattern": "uniform", "strat": "2024-01-01"}, "'strat'"),
        ({"strategy": 7}, "strategy"),
        ({"strategy": "constant", "value": 1, "output_type": "nonsense"}, "output_type"),
    ],
)
def test_a_bad_generator_is_rejected_at_its_place(generator: dict[str, Any], fragment: str) -> None:
    found = _problems(_doc(generator))
    assert found, generator
    assert any(fragment in p for p in found), found
    assert all(p.startswith("$.tables.t.columns.v.generator") for p in found), found


def test_the_published_schema_is_never_looser_than_the_loader_schema() -> None:
    """Whatever the loader's schema rejects, the published one rejects too."""
    good = _doc({"strategy": "uuid"})
    bad_docs = [
        {**good, "extra": 1},
        {**good, "schema_version": 2},
        {**good, "model": {"seed": 1}},
        {**good, "model": {"name": "t", "schema_mode": "snowflake"}},
        {**good, "tables": {"t": {"name": "t"}}},
        _doc({"strategy": "uuid"}, null_rate=2),
        _doc({"strategy": "uuid"}, nullable="yes"),
        {**good, "relationships": [{"name": "r", "parent": "a"}]},
        {**good, "relationships": [{"name": "r", "parent": "a", "child": "b", "type": "x"}]},
        {**good, "generation": {"scales": {"s": {"t": -1}}}},
        {**good, "business_rules": [{"name": "r"}]},
        [],
    ]
    for doc in bad_docs:
        assert schema_problems(doc), doc
        assert _problems(doc), doc
    assert schema_problems(good) == [] and _problems(good) == []


def test_extensions_are_allowed_everywhere_and_ignored_by_the_loader() -> None:
    doc = _doc({"strategy": "uuid", "x-editor": {"color": "red"}, "$comment": "why"})
    doc["x-owner"] = "data team"
    doc["$comment"] = "top"
    doc["tables"]["t"]["x-layout"] = [1, 2]
    doc["tables"]["t"]["columns"]["v"]["x-note"] = "n"
    doc["model"]["x-team"] = "a"
    assert _problems(doc) == []
    assert schema_problems(doc) == []
    GenSchema.from_dict(doc).validate_or_raise()
