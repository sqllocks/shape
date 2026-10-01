"""P4-02: the Shape engine plans the baseline's own schemas as the baseline does.

``benchmarks/vs_spindle/fixtures/plan.json`` holds, for each of the 14 domains in both modes, the
baseline's table order, dependency levels, column order and row counts at every scale preset
(``plan_fixtures.py``, which has ``--check`` to prove the fixtures still equal the baseline's
output). Nothing here needs the baseline's venv.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_spindle"
FIXTURES = BENCH / "fixtures"
sys.path.insert(0, str(BENCH))

_spec = importlib.util.spec_from_file_location(
    "vs_spindle_schema_import", BENCH / "schema_import.py"
)
assert _spec and _spec.loader
schema_import = importlib.util.module_from_spec(_spec)
sys.modules["vs_spindle_schema_import"] = schema_import
_spec.loader.exec_module(schema_import)

from shape.generation.engine import (  # noqa: E402
    Engine,
    calculate_row_counts,
    dependency_levels,
    order_columns,
    resolve_order,
)

PLAN = json.loads((FIXTURES / "plan.json").read_text("utf-8"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def test_the_plan_covers_every_dump():
    assert set(PLAN) == {p.stem for p in (FIXTURES / "schemas").glob("*.json")}
    assert len(PLAN) == 28


@pytest.mark.parametrize("key", sorted(PLAN))
def test_plan_equals_the_baselines(key):
    schema = schema_import.import_dump(_load(FIXTURES / "schemas" / f"{key}.json"))
    expected = PLAN[key]
    assert resolve_order(schema) == expected["order"]
    assert dependency_levels(schema) == expected["levels"]
    # The baseline lists a key column that has another strategy twice (it generates it twice and
    # the second write replaces the first); its frame holds the column once, at the first place.
    once = {t: list(dict.fromkeys(cols)) for t, cols in expected["columns"].items()}
    assert {t: order_columns(td) for t, td in schema.tables.items()} == once
    assert set(expected["row_counts"]) == set(schema.generation.scales)
    for scale, counts in expected["row_counts"].items():
        schema.generation.scale = scale
        assert calculate_row_counts(schema) == counts, (key, scale)


@pytest.mark.parametrize("mode", ["3nf", "star"])
def test_retail_row_counts_at_small_medium_large_xlarge(mode):
    schema = schema_import.import_dump(_load(FIXTURES / "schemas" / f"retail_{mode}.json"))
    for scale in ("small", "medium", "large", "xlarge"):
        engine = Engine(schema, scale=scale)
        assert engine.row_counts == PLAN[f"retail_{mode}"]["row_counts"][scale]
    small = Engine(schema, scale="small").row_counts
    assert small["customer"] == 1000 and small["order"] == 5000
    assert small["order_line"] == 12500 and small["return"] == 850 and small["promotion"] == 200
    assert Engine(schema, scale="xlarge").row_counts["order"] == 100_000_000


def test_dry_run_plans_retail_without_strategies():
    schema = schema_import.import_dump(_load(FIXTURES / "schemas" / "retail_3nf.json"))
    d = Engine(schema, scale="medium", strategies={}).dry_run()
    assert d.order == [t for level in PLAN["retail_3nf"]["levels"] for t in level]
    assert d.total_rows == sum(PLAN["retail_3nf"]["row_counts"]["medium"].values())
    assert not d.ok and any("faker" in m for m in d.missing_strategies)
