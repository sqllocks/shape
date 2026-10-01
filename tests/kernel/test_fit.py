"""P1-08: distribution fitting in Rust against the numpy reference (Spindle-equivalent)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from shape.kernel import dispatch, reference
from shape.kernel.reference.fit import sample_for_fitting
from shape.profile.fitting import detect_distribution

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def native():
    return dispatch._import_native()


def _compare(native, values, rel=1e-6):
    values = np.ascontiguousarray(np.asarray(values, dtype=np.float64))
    sample = sample_for_fitting(values) if len(values) > 2000 else values
    got = native.fit_distribution(pa.array(sample), pa.array(values))
    want = reference.fit_distribution(sample, values)
    assert got["distribution"] == want["distribution"], (got, want)
    if want["distribution_params"] is None:
        assert got["distribution_params"] is None
    else:
        assert set(got["distribution_params"]) == set(want["distribution_params"])
        for k, v in want["distribution_params"].items():
            assert got["distribution_params"][k] == pytest.approx(v, rel=rel, abs=1e-9), (
                k,
                got,
                want,
            )
    if want["fit_score"] is None:
        assert got["fit_score"] is None
    else:
        assert got["fit_score"] == pytest.approx(want["fit_score"], abs=1e-12)
    return got


def _families(rng, n):
    return {
        "normal": rng.normal(50, 7, n),
        "normal_int": np.round(rng.normal(1000, 50, n)),
        "uniform": rng.uniform(-3, 9, n),
        "exponential": rng.exponential(12.0, n),
        "lognormal": rng.lognormal(3.0, 0.6, n),
        "lognormal_shifted": rng.lognormal(1.0, 0.8, n) - 40.0,
        "heavy_right": rng.pareto(3.0, n) + 1,
        "ints": rng.integers(0, 100, n).astype(float),
        "bimodal": np.concatenate([rng.normal(0, 1, n // 2), rng.normal(8, 1, n - n // 2)]),
        "negative_normal": rng.normal(-500, 20, n),
        "tiny_scale": rng.normal(0, 1e-6, n),
        "big_values": rng.normal(1e9, 1e7, n),
    }


@pytest.mark.parametrize("n", [20, 35, 60, 140, 141, 500, 2000, 6000])
def test_rust_equals_numpy_on_every_family_and_size(native, n):
    rng = np.random.default_rng(n)
    for values in _families(rng, n).values():
        _compare(native, values)


def test_degenerate_inputs(native):
    _compare(native, np.full(50, 3.0))  # constant: no candidate fits
    _compare(native, np.arange(30, dtype=float))  # exact uniform grid
    _compare(native, np.array([1.0, 2.0] * 15))  # two values
    _compare(native, np.concatenate([np.zeros(25), np.ones(25)]))
    assert native.fit_distribution(pa.array([1.0] * 19))["distribution"] is None  # fewer than 20
    bad = np.r_[np.random.default_rng(1).normal(size=40), np.inf]  # non-finite: nothing fits
    assert _compare(native, bad)["distribution"] is None


def test_each_candidate_can_win(native):
    rng = np.random.default_rng(99)
    winners = {}
    for name, values in _families(rng, 1500).items():
        winners[name] = _compare(native, values)["distribution"]
    assert {"normal", "uniform", "exponential", "lognormal"} <= set(winners.values()), winners


def test_public_wrapper_samples_like_spindle():
    rng = np.random.default_rng(3)
    v = rng.normal(10, 2, 20_000)
    out = detect_distribution(v)
    sample = sample_for_fitting(v)
    want = reference.fit_distribution(sample, v)
    assert out["distribution"] == want["distribution"]
    assert out["distribution_params"] == pytest.approx(want["distribution_params"], rel=1e-6)
    assert out["fit_score"] == pytest.approx(want["fit_score"], abs=1e-12)
    assert detect_distribution(v[:5]) == {
        "distribution": None,
        "distribution_params": None,
        "fit_score": None,
    }
    assert len(sample) == 2000 and np.array_equal(sample, sample_for_fitting(v))


def _dataset_module():
    path = ROOT / "benchmarks" / "vs_spindle" / "profile_1to1" / "datasets.py"
    spec = importlib.util.spec_from_file_location("vs_spindle_datasets_fit", path)
    assert spec and spec.loader
    sys.path.insert(0, str(path.parents[1]))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vs_spindle_datasets_fit"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_numeric_columns_of_the_t22_datasets(native, tmp_path):
    """The columns Spindle's benchmark fits: D2, and the wide D4 (its lognormal fits take the
    Nelder-Mead fallback 66 times out of 175)."""
    ds = _dataset_module()
    ds.OUT = tmp_path
    d2 = ds._d2_table(20_000)
    checked = 0
    for name in d2.column_names:
        col = d2[name]
        if not (pa.types.is_floating(col.type) or pa.types.is_integer(col.type)):
            continue
        vals = col.drop_null().to_numpy(zero_copy_only=False).astype(np.float64)
        vals = vals[~np.isnan(vals)]
        _compare(native, vals)
        checked += 1
    ds.d4()
    d4 = pa.ipc  # noqa: F841 (keep pyarrow imported for readers below)
    import pyarrow.parquet as pq

    table = pq.read_table(tmp_path / "d4.parquet")
    for name in table.column_names:
        col = table[name]
        if not (pa.types.is_floating(col.type) or pa.types.is_integer(col.type)):
            continue
        vals = col.drop_null().to_numpy(zero_copy_only=False).astype(np.float64)[:6000]
        vals = vals[~np.isnan(vals)]
        if len(vals) >= 20:
            _compare(native, vals)
            checked += 1
    assert checked > 100


def test_fit_in_a_forked_child_after_the_parent_used_rayon(native):
    """rayon's threads do not survive fork(); a forked child must not wait on the dead pool."""
    import multiprocessing as mp

    if "fork" not in mp.get_all_start_methods():
        pytest.skip("no fork on this platform")
    values = np.random.default_rng(9).lognormal(1.0, 0.5, 100_000)
    parent = native.fit_distribution(pa.array(values[:2000]), pa.array(values))  # spins up rayon
    ctx = mp.get_context("fork")
    with ctx.Pool(1) as pool:
        child = pool.apply_async(
            native.fit_distribution, (pa.array(values[:2000]), pa.array(values))
        ).get(timeout=60)
    assert child == parent
