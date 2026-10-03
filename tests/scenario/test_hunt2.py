"""HUNT2-scenario: regression tests for defects found in the second audit of the scenario area."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import shape.chaos.engine as chaos_engine
from shape.generation.engine import Engine
from shape.scenario import PackLoader, PackRunner
from shape.scenario.manifest import ManifestBuilder
from shape.scenario.runner import _apply_chaos
from tests.scenario.conftest import write

SECTION = {"enabled": True, "intensity": "hurricane", "day": 40, "seed": 3}


def generated(retail, seed=1):
    return Engine(retail.schema, scale="small", seed=seed).generate()


# ---- #659: referential chaos runs once -------------------------------------------------------


def test_659_referential_chaos_runs_once_not_once_per_table(retail, monkeypatch):
    calls: list[int] = []
    original = chaos_engine.ChaosEngine.inject_referential_chaos

    def spy(self, tables, day):
        calls.append(day)
        return original(self, tables, day)

    monkeypatch.setattr(chaos_engine.ChaosEngine, "inject_referential_chaos", spy)
    monkeypatch.setattr(
        chaos_engine.ChaosEngine,
        "should_inject",
        lambda self, day, category: category == "referential",
    )
    data = generated(retail)
    assert len(data.tables) > 2
    _apply_chaos(data, SECTION, ManifestBuilder())
    assert calls == [40]


def test_659_the_referential_count_is_one_run_of_the_mutator(retail, monkeypatch):
    monkeypatch.setattr(
        chaos_engine.ChaosEngine,
        "should_inject",
        lambda self, day, category: category == "referential",
    )
    builder = ManifestBuilder()
    _apply_chaos(generated(retail), SECTION, builder)
    # a single run changes at most 5% (hurricane: capped at 80%) of one table's rows
    biggest = max(t.num_rows for t in generated(retail).tables.values())
    assert 0 < builder._m.chaos["referential"] <= biggest


# ---- #660: the volume count is what changed --------------------------------------------------


@pytest.mark.parametrize("kind", ["empty", "single_row", "spike"])
def test_660_the_volume_count_is_the_rows_added_or_removed(retail, monkeypatch, kind):
    import numpy as np

    def volume(self, table, day):
        out, events = self.volume.apply_one(kind, table, np.random.default_rng(0), 1.0)
        self.last_events = events
        return out

    monkeypatch.setattr(chaos_engine.ChaosEngine, "inject_volume_chaos", volume)
    monkeypatch.setattr(
        chaos_engine.ChaosEngine, "should_inject", lambda self, day, category: category == "volume"
    )
    data = generated(retail)
    builder = ManifestBuilder()
    out = _apply_chaos(data, SECTION, builder)
    changed = sum(abs(out.tables[n].num_rows - t.num_rows) for n, t in data.tables.items())
    assert changed > 0
    assert builder._m.chaos["volume"] == changed


def test_660_a_volume_event_that_changes_nothing_is_not_counted(retail, monkeypatch):
    import pyarrow as pa

    one = {"customer": pa.table({"customer_id": [1]})}
    data = generated(retail)
    data = replace(data, tables={**data.tables, **one})
    monkeypatch.setattr(
        chaos_engine.ChaosEngine, "should_inject", lambda self, day, category: category == "volume"
    )
    monkeypatch.setattr(
        chaos_engine.ChaosEngine,
        "inject_volume_chaos",
        lambda self, table, day: table,
    )
    builder = ManifestBuilder()
    _apply_chaos(data, SECTION, builder)
    assert "volume" not in builder._m.chaos
