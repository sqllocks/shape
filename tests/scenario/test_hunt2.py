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
from tests.scenario.conftest import PACK, write

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


# ---- #673: a key whose type no longer matches fails the gate, it does not crash it -----------


def retyped_key(retail):
    import pyarrow as pa
    import pyarrow.compute as pc

    data = generated(retail)
    orders = data.tables["order"]
    i = orders.schema.get_field_index("customer_id")
    bad = orders.set_column(i, "customer_id", pc.cast(orders["customer_id"], pa.string()))
    return replace(data, tables={**data.tables, "order": bad})


def test_673_a_retyped_foreign_key_fails_the_referential_integrity_gate(retail):
    from shape.scenario.runner import _run_gate

    passed, message = _run_gate("referential_integrity", retyped_key(retail))
    assert passed is False
    assert "order.customer_id" in message


def test_673_an_untouched_dataset_still_passes_the_gate(retail):
    from shape.scenario.runner import _run_gate

    assert _run_gate("referential_integrity", generated(retail)) == (True, "")


def test_673_a_narrower_integer_key_is_not_a_failure(retail):
    import pyarrow as pa
    import pyarrow.compute as pc

    from shape.scenario.runner import _run_gate

    data = generated(retail)
    orders = data.tables["order"]
    i = orders.schema.get_field_index("customer_id")
    narrow = orders.set_column(i, "customer_id", pc.cast(orders["customer_id"], pa.int32()))
    data = replace(data, tables={**data.tables, "order": narrow})
    assert _run_gate("referential_integrity", data) == (True, "")


# ---- #667: a run manifest with a field of the wrong type is refused, naming the field -------


def written_manifest(tmp_path, retail) -> Path:
    pack = PackLoader().load(write(tmp_path / "p.yaml", PACK.format(fmt="csv")))
    result = PackRunner().run(pack, retail, "fabric_demo", 42, tmp_path / "out")
    return Path(result.files_written[-1])


@pytest.mark.parametrize(
    "key,value",
    [
        ("seed", "x"),
        ("seed", 1.5),
        ("seed", True),
        ("seed", None),
        ("scale", 5),
        ("scale", None),
        ("domain", 3),
        ("pack_id", None),
        ("reproducibility", []),
        ("reproducibility", "x"),
        ("tables", []),
        ("tables", {"customer": 5}),
        ("chaos", []),
        ("sbom", []),
        ("validation", "x"),
        ("outputs", 1),
        ("timestamps", []),
        ("dataset_id", 5),
        ("run_id", 7),
    ],
)
def test_667_a_field_of_the_wrong_type_is_refused_with_its_name(tmp_path, retail, key, value):
    path = written_manifest(tmp_path, retail)
    doc = json.loads(path.read_text())
    doc[key] = value
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError) as caught:
        ManifestBuilder.from_file(path)
    message = str(caught.value)
    assert key in message and "must be" in message and str(path) in message


def test_667_a_manifest_the_runner_wrote_and_an_old_one_still_load(tmp_path, retail):
    path = written_manifest(tmp_path, retail)
    assert ManifestBuilder.from_file(path).seed == 42
    doc = json.loads(path.read_text())
    for key in ("format", "version", "reproducibility", "dataset_id", "shape_version"):
        doc.pop(key, None)
    path.write_text(json.dumps(doc))
    assert ManifestBuilder.from_file(path).reproducibility == {}
