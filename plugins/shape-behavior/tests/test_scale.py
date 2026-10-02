"""100,000 entities over five years: the run completes and stays exact (no timing is asserted)."""

import pyarrow as pa
import pytest
from helpers import T0

from shape_behavior import Population, SimConfig, Simulator, load_module

pytestmark = pytest.mark.heavy


def _sim(m, size=100_000, first_id=0, seed=7):
    return Simulator(
        [m],
        Population(size=size, start=T0, first_id=first_id, **m.doc["population_defaults"]),
        SimConfig(seed=seed),
    )


@pytest.mark.parametrize("name", ["subscription", "equipment_maintenance", "healthcare_screening"])
def test_hundred_thousand_entities_five_years_windows_equal_one_run(name):
    m = load_module(name)
    whole = _sim(m).run_until("2025-01-01")
    assert whole.num_rows > 100_000
    sim = _sim(m)
    parts = [sim.run_until(f"{y}-01-01") for y in range(2021, 2026)]
    assert pa.concat_tables(parts).equals(whole)


def test_sharding_the_population_by_id_range_gives_the_same_events():
    m = load_module("subscription")
    whole = _sim(m, 100_000).run_until("2025-01-01")
    halves = [_sim(m, 50_000, first_id=i).run_until("2025-01-01") for i in (0, 50_000)]
    merged = pa.concat_tables(halves).sort_by(
        [("time", "ascending"), ("entity_id", "ascending"), ("seq", "ascending")]
    )
    assert merged.equals(whole)
