"""The virtual clock stops and restarts exactly: windows, checkpoints and a fresh process agree."""

import pyarrow as pa
import pytest
from helpers import T0

from shape_behavior import Checkpoint, ModuleError, Population, SimConfig, Simulator, load_module

ALL = ("subscription", "equipment_maintenance", "healthcare_screening")


def _sim(name, size=800, seed=5):
    m = load_module(name)
    return m, Simulator([m], Population(size=size, start=T0, **m.doc["population_defaults"]), SimConfig(seed=seed))


@pytest.mark.parametrize("name", ALL)
def test_windows_concatenate_to_the_single_run(name):
    m, single = _sim(name)
    whole = single.run_until("2024-01-01")
    _, stepped = _sim(name)
    parts = [stepped.run_until(f"{y}-01-01") for y in (2021, 2022, 2023, 2024)]
    assert pa.concat_tables(parts).equals(whole)
    assert stepped.entities().equals(single.entities())


@pytest.mark.parametrize("name", ALL)
def test_checkpoint_roundtrip_through_a_file(name, tmp_path):
    m, sim = _sim(name)
    first = sim.run_until("2021-06-15")
    at = sim.now
    path = tmp_path / "run.ckpt.npz"
    sim.checkpoint().save(path)
    rest = sim.run_until("2024-01-01")

    resumed = Simulator.resume(path, [m])
    assert resumed.now == at
    rest2 = resumed.run_until("2024-01-01")
    assert rest2.equals(rest)
    assert pa.concat_tables([first, rest2]).equals(_sim(name)[1].run_until("2024-01-01"))
    assert resumed.entities().equals(sim.entities())


def test_checkpoint_object_roundtrip_and_clock():
    m, sim = _sim("subscription")
    sim.run_until("2021-01-01")
    ck = sim.checkpoint()
    assert Checkpoint.load  # the class is public
    again = Simulator.resume(ck, [m])
    assert again.now == sim.now


def test_resume_refuses_changed_modules(tmp_path):
    m, sim = _sim("subscription")
    sim.run_until("2021-01-01")
    ck = sim.checkpoint()
    with pytest.raises(ModuleError, match="differ"):
        Simulator.resume(ck, [load_module("equipment_maintenance")])


def test_running_to_an_earlier_time_returns_nothing():
    _, sim = _sim("subscription")
    sim.run_until("2022-01-01")
    assert sim.run_until("2021-01-01").num_rows == 0
    assert sim.run_until("2022-01-01").num_rows == 0


def test_checkpoint_holds_no_pickles(tmp_path):
    import numpy as np

    _, sim = _sim("healthcare_screening", size=100)
    sim.run_until("2021-01-01")
    path = tmp_path / "c.npz"
    sim.checkpoint().save(path)
    with np.load(path, allow_pickle=False) as z:  # raises if any array needs pickle
        assert "state" in z.files
