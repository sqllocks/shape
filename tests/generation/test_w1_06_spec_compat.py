"""W1-06 (#64) deliverable 2: the stability promise for the generation spec within 1.x
(``docs/GENERATION_SPEC.md``) and a compatibility test per strategy.

``spec_compat/<strategy>.json`` is a spec frozen at 1.0 that uses the strategy; it must stay
valid under the published schema, load, validate without errors and generate. ``_keys-1.0.json``
is the key table of 1.0: a key, a strategy or a distribution family may be added but none may
be removed, and nothing already optional may become required.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from shape import schemacheck
from shape.generation import reference, spec_keys
from shape.generation.engine import Engine
from shape.generation.schema import STRATEGY_REQUIRED_KEYS, GenSchema
from shape.generation.spec_schema import published_schema, strategy_names

CORPUS = Path(__file__).parent / "spec_compat"
LOCK = json.loads((CORPUS / "_keys-1.0.json").read_text("utf-8"))

DATASETS: dict[str, list[Any]] = {
    "compat_colors": ["red", "green", "blue"],
    "compat_people": [{"income": 10.0 * i} for i in range(1, 30)],
    "compat_places": [{"city": f"c{i}", "zip": f"{10000 + i}"} for i in range(40)],
    "compat_regions": [
        {"state": f"s{i % 3}", "city": f"c{i % 7}", "zip": f"{20000 + i}"} for i in range(42)
    ],
}


@pytest.fixture(autouse=True)
def _datasets() -> Iterator[None]:
    for name, rows in DATASETS.items():
        reference.register_dataset(name, rows)
    yield
    for name in DATASETS:
        reference.unregister_dataset(name)


def _fixture(strategy: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((CORPUS / f"{strategy}.json").read_text("utf-8"))
    return doc


def _strategies_in(doc: dict[str, Any]) -> set[str]:
    return {
        str(c["generator"].get("strategy"))
        for t in doc["tables"].values()
        for c in t["columns"].values()
    }


def test_every_strategy_has_a_frozen_spec_and_no_stray_files() -> None:
    files = {p.stem for p in CORPUS.glob("*.json") if not p.name.startswith("_")}
    assert files == set(strategy_names())


@pytest.mark.parametrize("strategy", strategy_names())
def test_a_frozen_spec_stays_valid_loads_and_generates(strategy: str) -> None:
    doc = _fixture(strategy)
    assert strategy in _strategies_in(doc)
    assert schemacheck.validate(doc, published_schema()) == []
    schema = GenSchema.from_dict(doc)
    assert [i for i in schema.validate() if i.level == "error"] == []
    first = Engine(schema, seed=11).generate().tables
    again = Engine(GenSchema.from_dict(doc), seed=11).generate().tables
    assert first.keys() == again.keys()
    for name, table in first.items():
        assert table.num_rows > 0
        assert table.equals(again[name]), f"{strategy}: {name} is not reproducible"
    assert GenSchema.from_dict(schema.to_dict()).to_dict() == schema.to_dict()


def test_the_lock_is_the_1_0_table() -> None:
    assert LOCK["format"] == "generation-spec-key-lock" and LOCK["version"] == 1
    assert set(LOCK["strategies"]) == set(strategy_names())


@pytest.mark.parametrize("strategy", sorted(LOCK["strategies"]))
def test_no_key_is_removed_or_made_required(strategy: str) -> None:
    locked = LOCK["strategies"][strategy]
    assert strategy in spec_keys.STRATEGY_KEYS, f"{strategy} was removed"
    assert set(locked["keys"]) <= spec_keys.STRATEGY_KEYS[strategy], "a key was removed"
    assert set(locked["params"]) <= spec_keys.PARAMS_KEYS.get(strategy, frozenset())
    assert STRATEGY_REQUIRED_KEYS[strategy] <= set(locked["required"]), "a key became required"


def test_no_family_or_common_key_is_removed() -> None:
    assert set(LOCK["common"]) <= spec_keys.COMMON
    for family, keys in LOCK["families"].items():
        assert family in spec_keys.FAMILY_KEYS, f"family {family} was removed"
        assert set(keys) <= spec_keys.FAMILY_KEYS[family]


def test_the_version_is_still_one() -> None:
    assert published_schema()["properties"]["schema_version"] == {"const": 1}
    assert published_schema()["x-shape-version"] == 1


def test_the_lock_detects_a_removed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check above has teeth: it fails when a key disappears."""
    patched = dict(spec_keys.STRATEGY_KEYS)
    patched["sequence"] = patched["sequence"] - {"step"}
    monkeypatch.setattr(spec_keys, "STRATEGY_KEYS", patched)
    with pytest.raises(AssertionError, match="a key was removed"):
        test_no_key_is_removed_or_made_required("sequence")
