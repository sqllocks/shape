"""ISS-gen #9: a generator spec with a key its strategy does not read is reported by
``GenSchema.validate`` (it used to be accepted silently, and a typo fell back to a default)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from shape.builtins.distributions.families import FAMILIES
from shape.generation import spec_keys
from shape.generation.schema import GenSchema


def _schema(strategy: str, generator: dict[str, Any], **column: Any) -> GenSchema:
    doc = {
        "schema_version": 1,
        "model": {"name": "t", "seed": 1},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": ["id"],
                "columns": {
                    "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}},
                    "price": {
                        "name": "price",
                        "type": "float",
                        "generator": {"strategy": strategy, **generator},
                        **column,
                    },
                },
            }
        },
        "relationships": [],
        "generation": {"scale": "s", "scales": {"s": {"t": 3}}},
    }
    return GenSchema.from_dict(doc)


def _ignored(schema: GenSchema) -> list[str]:
    return [i.message for i in schema.validate() if "ignores" in i.message]


def test_the_issue_repro_is_reported() -> None:
    schema = _schema(
        "distribution",
        {"distribution": "log_normal", "mean": 4.0, "sigma": 0.6, "scale": 2, "sigmaa_typo": 9},
    )
    messages = _ignored(schema)
    assert any("'scale'" in m and "column property" in m for m in messages)
    assert any("'sigmaa_typo'" in m for m in messages)
    typo = _schema("distribution", {"distribution": "log_normal", "mean": 4.0, "sigmaa": 0.6})
    assert any("did you mean 'sigma'" in m for m in _ignored(typo))


def test_the_scale_on_the_column_is_fine() -> None:
    schema = _schema(
        "distribution", {"distribution": "log_normal", "mean": 4.0, "sigma": 0.6}, scale=2
    )
    assert _ignored(schema) == []


def test_a_misspelled_nested_param_is_reported() -> None:
    schema = _schema(
        "distribution", {"distribution": "normal", "params": {"mean": 1, "std_devv": 2}}
    )
    assert any(
        "'params.std_devv'" in m and "did you mean 'params.std_dev'" in m for m in _ignored(schema)
    )
    ok = _schema("distribution", {"distribution": "normal", "params": {"mean": 1, "std_dev": 2}})
    assert _ignored(ok) == []


@pytest.mark.parametrize(
    ("strategy", "generator", "word"),
    [
        ("temporal", {"pattern": "uniform", "strat": "2024-01-01"}, "start"),
        ("native", {"provider": "email", "domain": "realistic"}, "domains"),
        ("sequence", {"strt": 5}, "start"),
        ("foreign_key", {"ref": "t.id", "distribtion": "zipf"}, "distribution"),
        ("weighted_enum", {"values": {"a": 1}, "weight": 3}, None),
    ],
)
def test_other_strategies_report_unknown_keys(
    strategy: str, generator: dict[str, Any], word: str | None
) -> None:
    messages = _ignored(_schema(strategy, generator))
    assert len(messages) == 1
    if word:
        assert f"did you mean '{word}'" in messages[0]


def test_a_plugin_strategy_or_family_is_not_checked() -> None:
    assert spec_keys.unknown_keys("my_plugin_strategy", {"anything": 1}) == []
    assert spec_keys.unknown_keys("distribution", {"distribution": "plugin_dist", "x": 1}) == []


def test_the_shipped_domain_and_a_ddl_schema_have_no_ignored_keys() -> None:
    pytest.importorskip("shape_domains")
    from shape.generation.ddl import from_ddl
    from shape.generation.domains import load_domain

    assert _ignored(load_domain("retail").schema) == []
    ddl, _ = from_ddl(
        "CREATE TABLE a (id INT PRIMARY KEY, name VARCHAR(40), amount DECIMAL(10,2), "
        "made DATETIME);"
    )
    assert _ignored(ddl) == []


def test_family_keys_match_the_families() -> None:
    for name, family in FAMILIES.items():
        if name in spec_keys.FAMILY_KEYS:
            assert set(family.defaults) <= spec_keys.FAMILY_KEYS[name], name
    assert set(spec_keys.FAMILY_KEYS) <= set(FAMILIES)
    assert {n for n in FAMILIES if not FAMILIES[n].defaults} == {
        "histogram",
        "mixture",
        "truncated",
    }


def _string_keys_read(path: Path) -> set[str]:
    """String constants a strategy reads from its spec: ``spec.get("k")``, ``spec["k"]``,
    ``require(spec, "k", ...)``, and the same on ``params`` / ``gen``."""
    tree = ast.parse(path.read_text("utf-8"))
    names = {"spec", "params", "gen", "p", "window"}
    keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in names
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "require"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
        ):
            keys.add(str(node.args[1].value))
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in names
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return keys


def test_no_strategy_reads_a_key_the_table_does_not_know() -> None:
    root = Path(spec_keys.__file__).parents[1] / "builtins" / "strategies"
    known = set(spec_keys.COMMON)
    for keys in (*spec_keys.STRATEGY_KEYS.values(), *spec_keys.PARAMS_KEYS.values()):
        known |= keys
    for keys in spec_keys.FAMILY_KEYS.values():
        known |= keys
    # keys of nested specs the strategies read from inner mappings, not from the generator
    nested = {"fixed", "profiles", "month", "day_of_week", "hour_of_day", "peaks", "std_dev"}
    for path in sorted(root.glob("*.py")):
        unknown = _string_keys_read(path) - known - nested
        assert not unknown, f"{path.name} reads {sorted(unknown)} but spec_keys does not list them"


def test_a_spec_key_file_is_json_round_trippable() -> None:
    assert json.dumps(sorted(spec_keys.COLUMN_PROPERTIES))


def test_a_ddl_text_column_bounds_the_provider_through_args() -> None:
    """Found by the check above: ``from-ddl`` wrote ``max_nb_chars`` at the top of a ``faker``
    generator, where the strategy ignored it; it is the provider's keyword, so it is an ``arg``."""
    pytest.importorskip("faker")
    from shape.generation.ddl import from_ddl
    from shape.generation.engine import Engine

    schema, _ = from_ddl("CREATE TABLE a (id INT PRIMARY KEY, note VARCHAR(40));", smart=False)
    gen = schema.tables["a"].columns["note"].generator
    assert gen["args"] == {"max_nb_chars": 40} and "max_nb_chars" not in gen
    pytest.importorskip("faker")

    note = Engine(schema, seed=1).generate().tables["a"]["note"].to_pylist()
    assert max(len(v) for v in note if v) <= 40
