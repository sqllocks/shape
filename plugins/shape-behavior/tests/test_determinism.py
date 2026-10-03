"""Same seed, same events: however the run is chunked, split or reordered."""

import numpy as np
from helpers import T0, module, start
from shape_behavior import Population, SimConfig, Simulator, load_module


def _example_pop(m, size=1500, **kw):
    return Population(size=size, start=T0, **{**m.doc["population_defaults"], **kw})


def test_same_seed_same_events_and_other_seed_differs():
    m = load_module("subscription")
    a = Simulator([m], _example_pop(m), SimConfig(seed=11)).run_until("2023-01-01")
    b = Simulator([m], _example_pop(m), SimConfig(seed=11)).run_until("2023-01-01")
    c = Simulator([m], _example_pop(m), SimConfig(seed=12)).run_until("2023-01-01")
    assert a.num_rows > 1000
    assert a.equals(b)
    assert not a.equals(c)


def test_entities_are_identical_too():
    m = load_module("healthcare_screening")
    a = Simulator([m], _example_pop(m), SimConfig(seed=2))
    b = Simulator([m], _example_pop(m), SimConfig(seed=2))
    a.run_until("2024-01-01")
    b.run_until("2024-01-01")
    assert a.entities().equals(b.entities())


def test_splitting_the_population_into_id_ranges_changes_nothing():
    m = load_module("equipment_maintenance")
    whole = Simulator([m], _example_pop(m, 1200), SimConfig(seed=4)).run_until("2023-01-01")
    parts = [
        Simulator([m], _example_pop(m, 400, first_id=i * 400), SimConfig(seed=4)).run_until(
            "2023-01-01"
        )
        for i in range(3)
    ]
    import pyarrow as pa

    joined = pa.concat_tables(parts).sort_by(
        [("time", "ascending"), ("entity_id", "ascending"), ("seq", "ascending")]
    )
    assert whole.equals(joined)


def test_modules_do_not_disturb_each_others_draws():
    # a module's events for an entity do not depend on another module running beside it
    solo = load_module("subscription")
    other = module(
        {
            "s": start("w"),
            "w": {
                "type": "delay",
                "delay": {"kind": "exact", "value": 1, "unit": "days"},
                "transition": {"direct": "w"},
            },
        },
        name="ticker",
    )
    pop = _example_pop(solo, 800)
    a = Simulator([solo], pop, SimConfig(seed=9)).run_until("2022-01-01")
    b = Simulator([solo, other], pop, SimConfig(seed=9)).run_until("2022-01-01")
    only = b.filter(
        __import__("pyarrow.compute", fromlist=["x"]).equal(b["module"], "subscription")
    )
    assert a.drop_columns(["seq"]).equals(only.drop_columns(["seq"]))


def test_event_ids_are_unique_and_sequential_per_entity():
    m = load_module("equipment_maintenance")
    ev = Simulator([m], _example_pop(m, 500), SimConfig(seed=1)).run_until("2023-01-01")
    pairs = set(zip(ev["entity_id"].to_pylist(), ev["seq"].to_pylist(), strict=True))
    assert len(pairs) == ev.num_rows
    ent = np.array(ev["entity_id"].to_pylist())
    seq = np.array(ev["seq"].to_pylist())
    t = np.array(ev["time"].to_numpy())
    for e in (0, 7, 123):
        sel = ent == e
        order = np.argsort(seq[sel])
        assert list(seq[sel][order]) == list(range(sel.sum()))
        assert np.all(np.diff(t[sel][order].astype("int64")) >= 0)


def test_table_is_sorted():
    m = load_module("subscription")
    ev = Simulator([m], _example_pop(m, 600), SimConfig(seed=3)).run_until("2022-01-01")
    key = list(
        zip(ev["time"].to_pylist(), ev["entity_id"].to_pylist(), ev["seq"].to_pylist(), strict=True)
    )
    assert key == sorted(key)
