"""P1-16: the lognormal likelihood in Rust is bitwise-equal to the numpy reference.

The reference twin (``shape.profile.reference.numerics``) evaluates every likelihood with numpy's
``log`` and ``sum`` (a pairwise sum over a fixed block tree). The kernel evaluates the same passes
chunk by chunk, in parallel for large columns, and recombines the leaf sums in the reference's
order, so every value has to equal the reference bit for bit: not within a tolerance.
"""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from shape.kernel import dispatch, reference
from shape.kernel.reference.fit import sample_for_fitting
from shape.profile.reference.numerics import _lognorm_nnlf

# at or above the kernel's parallel threshold (2**18 rows): the pool runs the passes
PARALLEL_ROWS = 300_000


@pytest.fixture(scope="module")
def native():
    return dispatch._import_native()


def _bits(x):
    return np.float64(x).tobytes()


def _same(a, b):
    return _bits(a) == _bits(b) or (np.isnan(a) and np.isnan(b))


def _reference_probe(data, loc):
    """``(shape, scale, dL/dloc, loglik)`` as the reference's ``_lognorm_fit`` evaluates them."""
    with np.errstate(all="ignore"):

        def shape_scale(loc):
            logs = np.log(data - loc)
            scale = np.exp(logs.mean())
            d = logs - np.log(scale)
            return np.sqrt(np.mean(np.square(d))), scale

        shape, scale = shape_scale(loc)
        shifted = data - loc
        t = np.log(shifted / scale)
        dl = np.sum((1 + t / shape**2) / shifted)
        ll = -_lognorm_nnlf((shape, loc, scale), data)
    return shape, scale, dl, ll


def _datasets(rng, n):
    return {
        "lognormal": rng.lognormal(3.0, 0.6, n),
        "normal": np.round(rng.normal(250, 40, n), 3),
        "shifted": np.round(rng.lognormal(1.0, 0.8, n), 2) - 3.0,
        "exponential": rng.exponential(5.0, n),
        "standard_normal": np.round(rng.normal(0, 1, n), 3),
    }


def _locs(data):
    mn = float(data.min())
    # just below the minimum, well below, far below, and above it (outside the support)
    return [mn - 1e-9 * (1 + abs(mn)), mn - 0.7, mn - 20.0, mn - 1e4, mn, mn + 0.5]


@pytest.mark.parametrize("n", [20, 127, 129, 1000, 4097, 70_001, PARALLEL_ROWS])
def test_likelihood_passes_equal_the_numpy_reference_bit_for_bit(native, n):
    rng = np.random.default_rng(n)
    for name, data in _datasets(rng, n).items():
        data = np.ascontiguousarray(data)
        for loc in _locs(data):
            got = native.lognorm_probe(pa.array(data), loc)
            want = _reference_probe(data, loc)
            assert all(_same(g, w) for g, w in zip(got, want, strict=True)), (
                name,
                n,
                loc,
                got,
                want,
            )


def _families(rng, n):
    return {
        "normal": rng.normal(50, 7, n),
        "uniform": rng.uniform(-3, 9, n),
        "exponential": rng.exponential(12.0, n),
        "lognormal": rng.lognormal(3.0, 0.6, n),
        "lognormal_shifted": rng.lognormal(1.0, 0.8, n) - 40.0,
        "heavy_right": rng.pareto(3.0, n) + 1,
        "ints": rng.integers(0, 100, n).astype(float),
        "bimodal": np.concatenate([rng.normal(0, 1, n // 2), rng.normal(8, 1, n - n // 2)]),
        "rounded_lognormal": np.round(rng.lognormal(1, 0.5, n), 4),
        "rounded_normal": np.round(rng.normal(0, 1, n), 3),
    }


@pytest.mark.parametrize("n", [20, 60, 141, 500, 2000, 30_000])
def test_fits_equal_the_numpy_reference_bit_for_bit(native, n):
    """Distribution, every parameter and the fit score: the root finder and the Nelder-Mead
    fallback follow the same path, so the final parameters carry the same bits."""
    rng = np.random.default_rng(1000 + n)
    for name, values in _families(rng, n).items():
        values = np.ascontiguousarray(values)
        sample = sample_for_fitting(values) if len(values) > 2000 else values
        got = native.fit_distribution(pa.array(sample), pa.array(values))
        want = reference.fit_distribution(sample, values)
        assert got["distribution"] == want["distribution"], (name, n)
        assert got["fit_score"] == want["fit_score"], (name, n)
        if want["distribution_params"] is None:
            assert got["distribution_params"] is None
            continue
        assert set(got["distribution_params"]) == set(want["distribution_params"])
        for k, v in want["distribution_params"].items():
            assert _same(got["distribution_params"][k], v), (name, n, k, got, want)


def test_a_full_column_refit_on_the_pool_equals_the_reference(native):
    """The full-column refit of a lognormal column above the parallel threshold."""
    rng = np.random.default_rng(7)
    values = np.round(rng.lognormal(3.5, 0.9, PARALLEL_ROWS), 2)
    sample = sample_for_fitting(values)
    got = native.fit_distribution(pa.array(sample), pa.array(values))
    want = reference.fit_distribution(sample, values)
    assert got["distribution"] == want["distribution"] == "lognormal"
    assert got["fit_score"] == want["fit_score"]
    assert all(
        _same(got["distribution_params"][k], v) for k, v in want["distribution_params"].items()
    )
