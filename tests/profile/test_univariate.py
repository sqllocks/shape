"""Univariate depth (W3-07, issue #103): model selection, zero inflation, heaping, Benford and the
tail index, as computed by ``shape.profile.univariate`` and carried by ``shape.profile``."""

from __future__ import annotations

import math

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.profile import univariate as U

N = 5_000


def _stats(values: object, *, integer: bool = False) -> dict:
    return U.univariate_stats(np.asarray(values, dtype=np.float64), integer=integer)


def _col(values: object, name: str = "c") -> dict:
    return shape.profile(pa.table({name: pa.array(values)}), univariate=True).to_dict()["columns"][
        name
    ]


# --- 1. model selection --------------------------------------------------------------------------

_FAMILIES = {
    "normal": lambda r: r.normal(50.0, 10.0, N),
    "lognormal": lambda r: r.lognormal(1.0, 0.8, N),
    "exponential": lambda r: r.exponential(3.0, N),
    "uniform": lambda r: r.uniform(10.0, 20.0, N),
    "gamma": lambda r: r.gamma(4.0, 2.0, N),
    "weibull": lambda r: 5.0 * r.weibull(2.5, N),
}


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_bic_selects_the_generating_family(family: str) -> None:
    out = _stats(_FAMILIES[family](np.random.default_rng(7)))
    assert out["distribution_by_bic"] == family
    cand = out["distribution_candidates"]
    assert cand[family]["bic"] == min(c["bic"] for c in cand.values())


def test_each_candidate_reports_parameters_likelihood_aic_bic_and_ks() -> None:
    out = _stats(np.random.default_rng(1).gamma(3.0, 2.0, 2000))
    assert set(out["distribution_candidates"]) == {
        "normal",
        "lognormal",
        "exponential",
        "uniform",
        "gamma",
        "weibull",
    }
    for c in out["distribution_candidates"].values():
        assert set(c) == {"params", "log_likelihood", "aic", "bic", "ks"}
        assert 0.0 <= c["ks"] <= 1.0
    g = out["distribution_candidates"]["gamma"]
    assert g["params"]["shape"] == pytest.approx(3.0, rel=0.15)
    assert g["params"]["scale"] == pytest.approx(2.0, rel=0.15)
    # AIC = 2k - 2 LL, BIC = k ln n - 2 LL, k = 2 for the gamma
    assert g["aic"] == pytest.approx(4 - 2 * g["log_likelihood"], abs=1e-3)
    assert g["bic"] == pytest.approx(2 * math.log(2000) - 2 * g["log_likelihood"], abs=1e-3)


def test_the_normal_likelihood_is_the_closed_form() -> None:
    x = np.random.default_rng(2).normal(0.0, 3.0, 1000)
    out = _stats(x)["distribution_candidates"]["normal"]
    sigma = x.std()
    expected = -0.5 * len(x) * (math.log(2 * math.pi * sigma**2) + 1.0)
    assert out["log_likelihood"] == pytest.approx(expected, abs=1e-3)
    assert out["params"] == {
        "mu": pytest.approx(x.mean(), abs=1e-6),
        "sigma": pytest.approx(sigma, abs=1e-6),
    }


def test_positive_only_families_are_left_out_of_data_with_a_value_at_or_below_zero() -> None:
    x = np.random.default_rng(3).normal(0.0, 1.0, 500)
    out = _stats(x)
    assert set(out["distribution_candidates"]) == {"normal", "uniform"}
    x = np.abs(x) + 1.0
    x[0] = 0.0  # a zero is not positive
    assert set(_stats(x)["distribution_candidates"]) == {"normal", "uniform"}


def test_model_selection_needs_twenty_finite_values() -> None:
    rng = np.random.default_rng(4)
    assert "distribution_candidates" not in _stats(rng.normal(size=19))
    assert "distribution_candidates" in _stats(rng.normal(size=20))
    x = rng.normal(size=40)
    x[:21] = np.nan  # 19 finite values remain
    assert "distribution_candidates" not in _stats(x)


def test_a_constant_column_has_no_candidates() -> None:
    out = _stats(np.full(100, 7.0))
    assert "distribution_candidates" not in out and "distribution_by_bic" not in out


def test_the_existing_selection_fields_are_unchanged_by_the_new_ones() -> None:
    rng = np.random.default_rng(5)
    values = [float(v) for v in rng.gamma(4.0, 2.0, 3000)]
    col = _col(values)
    from shape.profile.fitting import detect_distribution

    old = detect_distribution(np.asarray(values))
    assert col["distribution"] == old["distribution"]
    assert col["distribution_params"] == old["distribution_params"]
    assert col["fit_score"] == old["fit_score"]
    assert "distribution_by_bic" in col


# --- 2. zero inflation ---------------------------------------------------------------------------


def test_a_zero_inflated_poisson_is_flagged_and_a_plain_poisson_is_not() -> None:
    rng = np.random.default_rng(6)
    plain = rng.poisson(3.0, N)
    zip_ = np.where(rng.random(N) < 0.3, 0, rng.poisson(3.0, N))
    a, b = _stats(plain, integer=True), _stats(zip_, integer=True)
    assert a["zero_inflation"]["inflated"] is False
    assert b["zero_inflation"]["inflated"] is True
    assert b["zero_share"] == pytest.approx(float(np.mean(zip_ == 0)), abs=1e-6)
    z = b["zero_inflation"]
    assert z["poisson_expected"] == pytest.approx(math.exp(-zip_.mean()), abs=1e-6)
    assert z["nb_expected"] > z["poisson_expected"]  # the overdispersion absorbs some zeros
    assert z["observed"] > z["nb_expected"]


def test_zero_inflation_needs_a_share_of_at_least_five_percent() -> None:
    # 3% zeros beyond the model: far more than three standard errors at n = 200,000, but below
    # the 0.05 floor
    rng = np.random.default_rng(8)
    n = 200_000
    x = np.where(rng.random(n) < 0.03, 0, rng.poisson(6.0, n))
    out = _stats(x, integer=True)
    assert out["zero_share"] < 0.05 + 0.0
    assert out["zero_inflation"]["inflated"] is False


def test_zero_inflation_boundary_three_standard_errors() -> None:
    # a handful of rows cannot be three standard errors above the model
    x = np.array([0] * 8 + [1, 2, 3, 4] * 3, dtype=float)
    out = _stats(x, integer=True)
    assert out["zero_share"] == pytest.approx(8 / 20)
    assert out["zero_inflation"]["inflated"] is False


def test_zero_inflation_only_for_non_negative_integers() -> None:
    rng = np.random.default_rng(9)
    ints = rng.integers(-3, 4, 500)
    assert "zero_share" not in _stats(ints, integer=True)
    assert "zero_inflation" not in _stats(ints, integer=True)
    floats = np.where(rng.random(500) < 0.4, 0.0, rng.random(500) + 0.5)
    out = _stats(floats)
    assert out["zero_share"] == pytest.approx(float(np.mean(floats == 0)))
    assert "zero_inflation" not in out  # a float column: the share only


def test_an_all_zero_column_is_not_inflated() -> None:
    out = _stats(np.zeros(100), integer=True)
    assert out["zero_share"] == 1.0
    assert out["zero_inflation"]["inflated"] is False


def test_underdispersed_counts_use_the_poisson_for_the_negative_binomial() -> None:
    x = np.tile([2.0, 3.0, 4.0, 3.0], 50)  # variance below the mean
    z = _stats(x, integer=True)["zero_inflation"]
    assert z["nb_expected"] == pytest.approx(z["poisson_expected"])


# --- 3. heaping ----------------------------------------------------------------------------------


def _heaped_ages(rng: np.random.Generator, share: float = 0.6) -> np.ndarray:
    ages = rng.integers(18, 91, N)
    round_ = rng.integers(4, 19, N) * 5
    return np.where(rng.random(N) < share, round_, ages)


def test_ages_heaped_on_multiples_of_five_are_flagged_and_uniform_ages_are_not() -> None:
    rng = np.random.default_rng(10)
    h = _stats(_heaped_ages(rng), integer=True)["heaping"]
    assert h["heaped"] is True and h["unit"] in (5, 10)
    assert h["observed_share"] >= 0.1 and h["ratio"] >= 2.0
    flat = _stats(rng.integers(18, 91, N), integer=True)["heaping"]
    assert flat["heaped"] is False
    assert flat["ratio"] == pytest.approx(1.0, abs=0.25)


def test_the_strongest_unit_is_reported() -> None:
    rng = np.random.default_rng(11)
    x = np.where(rng.random(N) < 0.7, rng.integers(2, 100, N) * 10, rng.integers(20, 1000, N))
    h = _stats(x, integer=True)["heaping"]
    assert h["unit"] == 10 and h["heaped"] is True
    assert set(h) >= {"unit", "observed_share", "expected_share", "ratio", "heaped", "resolution"}


def test_a_column_of_whole_hundreds_is_resolution_not_heaping() -> None:
    rng = np.random.default_rng(12)
    h = _stats(rng.integers(1, 200, N) * 100, integer=True)["heaping"]
    assert h["resolution"] == 100
    assert h["heaped"] is False
    assert h["unit"] in (None, 1000)  # a unit below the resolution is never reported


def _background() -> np.ndarray:
    return np.array([v for v in range(1, 1000) if v % 5], dtype=np.float64)  # on no unit


def test_heaping_boundary_observed_share_of_one_tenth() -> None:
    bg = _background()
    below = np.concatenate([np.full(99, 500.0), np.resize(bg, 901)])  # 9.9% on whole hundreds
    at = np.concatenate([np.full(100, 500.0), np.resize(bg, 900)])  # 10%
    h = _stats(below, integer=True)["heaping"]
    assert h["observed_share"] == pytest.approx(0.099) and h["ratio"] > 2.0
    assert h["heaped"] is False
    h = _stats(at, integer=True)["heaping"]
    assert h["unit"] == 100 and h["observed_share"] == pytest.approx(0.1)
    assert h["heaped"] is True


def test_heaping_boundary_ratio_of_two() -> None:
    bg = _background()
    odd_fives = np.array([v for v in range(5, 1000, 10)], dtype=np.float64)  # 5, 15, ... on no 10
    below = np.concatenate([np.resize(odd_fives, 380), np.resize(bg, 620)])
    above = np.concatenate([np.resize(odd_fives, 420), np.resize(bg, 580)])
    lo, hi = _stats(below, integer=True)["heaping"], _stats(above, integer=True)["heaping"]
    assert lo["unit"] == 5 and lo["ratio"] < 2.0 and lo["heaped"] is False
    assert hi["unit"] == 5 and hi["ratio"] >= 2.0 and hi["heaped"] is True


def test_zeros_do_not_count_as_round_numbers() -> None:
    rng = np.random.default_rng(14)
    x = np.where(rng.random(N) < 0.5, 0, rng.integers(1, 61, N))
    assert _stats(x, integer=True)["heaping"]["heaped"] is False


def test_a_small_scale_is_not_heaped() -> None:
    rng = np.random.default_rng(15)
    stars = rng.choice(
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        N,
        p=[0.02, 0.02, 0.04, 0.04, 0.3, 0.1, 0.1, 0.1, 0.1, 0.18],
    )
    assert _stats(stars, integer=True)["heaping"]["heaped"] is False  # under 20 grid points


def test_decimal_values_on_a_cent_grid_and_continuous_values() -> None:
    rng = np.random.default_rng(16)
    cents = np.round(rng.uniform(10, 5000, N), 2)
    h = _stats(cents)["heaping"]
    assert h["resolution"] == 0.01 and h["heaped"] is False
    rounded = np.where(rng.random(N) < 0.5, np.round(cents / 100) * 100, cents)
    assert _stats(rounded)["heaping"]["heaped"] is True
    assert "heaping" not in _stats(rng.normal(size=N) * np.pi)  # no grid: not computed


def test_integer_valued_floats_count_as_integers() -> None:
    rng = np.random.default_rng(17)
    as_float = _heaped_ages(rng).astype(np.float64)
    assert _stats(as_float)["heaping"] == _stats(as_float, integer=True)["heaping"]


# --- 4. Benford ----------------------------------------------------------------------------------


def test_benford_amounts_are_close_and_uniform_amounts_are_nonconforming() -> None:
    rng = np.random.default_rng(18)
    amounts = 10 ** rng.uniform(0.0, 5.0, N)  # log-uniform: Benford's law exactly
    b = _stats(amounts)["benford"]
    assert b["applicable"] is True and b["conformity"] == "close"
    assert len(b["digits"]) == 9 and sum(b["digits"]) == pytest.approx(1.0, abs=1e-5)
    assert b["digits"][0] == pytest.approx(0.301, abs=0.03)
    u = _stats(rng.uniform(1.0, 100000.0, N))["benford"]
    assert u["conformity"] == "nonconformity" and u["mad"] > 0.015


def test_benford_conformity_classes_follow_nigrini() -> None:
    assert [
        U.benford_class(m) for m in (0.0, 0.00599, 0.006, 0.0119, 0.012, 0.0149, 0.015, 0.1)
    ] == [
        "close",
        "close",
        "acceptable",
        "acceptable",
        "marginal",
        "marginal",
        "nonconformity",
        "nonconformity",
    ]


def test_benford_not_applicable_cases_say_why() -> None:
    rng = np.random.default_rng(19)
    few = _stats(10 ** rng.uniform(0, 5, 99))["benford"]
    assert few == {"applicable": False, "reason": "fewer than 100 values"}
    assert "benford" in _stats(10 ** rng.uniform(0, 5, 100))
    assert _stats(10 ** rng.uniform(0, 5, 100))["benford"]["applicable"] is True
    narrow = _stats(rng.uniform(100.0, 999.0, 500))["benford"]
    assert narrow["applicable"] is False and "orders of magnitude" in narrow["reason"]
    # exactly two orders of magnitude is enough
    two = np.concatenate([[1.0, 100.0], 10 ** rng.uniform(0, 2, 200)])
    assert _stats(two)["benford"]["applicable"] is True
    neg = _stats(np.concatenate([10 ** rng.uniform(0, 5, 500), [-1.0]]))["benford"]
    assert neg["applicable"] is False and "positive" in neg["reason"]
    zero = _stats(np.concatenate([10 ** rng.uniform(0, 5, 500), [0.0]]))["benford"]
    assert zero["applicable"] is False


def test_first_digits_are_exact_at_powers_of_ten() -> None:
    d = U._first_digits(np.array([1.0, 9.99, 10.0, 100.0, 1000.0, 0.001, 0.0999, 5e-7, 2e22]))
    assert list(d) == [1, 9, 1, 1, 1, 1, 9, 5, 2]


# --- 5. tail index -------------------------------------------------------------------------------


def test_a_pareto_tail_is_estimated_within_three_standard_errors_and_flagged_heavy() -> None:
    for seed in range(5):
        x = (np.random.default_rng(seed).pareto(1.5, N) + 1.0) * 2.0
        t = _stats(x)["tail_index"]
        assert t["heavy"] is True
        assert abs(t["alpha"] - 1.5) <= 3 * t["se"]
        assert t["k"] == max(10, round(math.sqrt(N)))
        assert t["se"] == pytest.approx(t["alpha"] / math.sqrt(t["k"]), rel=1e-3)


def test_a_normal_sample_is_not_heavy_tailed() -> None:
    t = _stats(np.random.default_rng(20).normal(100.0, 10.0, N))["tail_index"]
    assert t["heavy"] is False and t["alpha"] > 2.0


def test_tail_index_needs_fifty_positive_values() -> None:
    rng = np.random.default_rng(21)
    assert "tail_index" not in _stats(rng.pareto(1.5, 49) + 1.0)
    t = _stats(rng.pareto(1.5, 50) + 1.0)["tail_index"]
    assert t["k"] == 10  # max(10, round(sqrt(50)) = 7)
    x = np.concatenate([rng.normal(-5.0, 1.0, 200), rng.pareto(1.5, 49) + 1.0])
    assert "tail_index" not in _stats(x)  # only the 49 positive values count


def test_tail_index_on_constant_positive_values_is_left_out() -> None:
    assert "tail_index" not in _stats(np.full(100, 3.0))


# --- 6. bounded cost, determinism, and the profile -----------------------------------------------


def test_the_sample_is_deterministic_and_capped() -> None:
    x = np.random.default_rng(22).random(250_000)
    a, b = U.sample_values(x), U.sample_values(x.copy())
    assert len(a) == U.SAMPLE_CAP == 50_000
    assert np.array_equal(a, b)
    small = x[:5000]
    assert U.sample_values(small) is small or np.array_equal(U.sample_values(small), small)


def test_a_large_input_gives_the_same_answer_twice() -> None:
    x = np.random.default_rng(23).lognormal(2.0, 1.0, 300_000)
    assert _stats(x) == _stats(x.copy())


def test_new_fields_appear_in_the_profile_and_are_absent_where_not_computed() -> None:
    rng = np.random.default_rng(24)
    col = _col([float(v) for v in rng.gamma(4.0, 2.0, 600)])
    assert col["distribution_by_bic"] == "gamma"
    assert {"distribution_candidates", "zero_share", "tail_index"} <= set(col)
    text = _col(["a", "b", "c"] * 50)
    assert not (
        {
            "distribution_candidates",
            "distribution_by_bic",
            "zero_share",
            "zero_inflation",
            "heaping",
            "benford",
            "tail_index",
        }
        & set(text)
    )
    tiny = _col([1.5, 2.5, 3.5])
    assert "distribution_candidates" not in tiny and "benford" not in tiny


def test_integer_columns_get_zero_inflation_in_the_profile() -> None:
    rng = np.random.default_rng(25)
    zip_ = np.where(rng.random(N) < 0.3, 0, rng.poisson(3.0, N))
    col = _col([int(v) for v in zip_])
    assert col["zero_inflation"]["inflated"] is True
    assert col["zero_share"] == pytest.approx(float(np.mean(zip_ == 0)), abs=1e-6)


def test_object_columns_keep_the_statistics() -> None:
    from decimal import Decimal

    rng = np.random.default_rng(26)
    col = _col([Decimal(str(round(float(v), 2))) for v in rng.gamma(4.0, 2.0, 400)])
    assert "distribution_candidates" in col


def test_output_is_json_safe() -> None:
    import json

    rng = np.random.default_rng(27)
    out = _stats(rng.lognormal(1.0, 2.0, 2000))
    assert json.loads(json.dumps(out, allow_nan=False)) == out


def test_extreme_values_do_not_break_the_fits() -> None:
    rng = np.random.default_rng(28)
    for x in (
        np.concatenate([rng.normal(1e12, 1.0, 100)]),
        rng.lognormal(0.0, 6.0, 500),
        np.concatenate([np.full(50, 1e-300), np.full(50, 1.0)]),
        np.array([1.0] * 99 + [1e15]),
    ):
        out = _stats(x)
        assert "distribution_candidates" in out
        for c in out["distribution_candidates"].values():
            assert all(math.isfinite(v) for v in (c["log_likelihood"], c["aic"], c["bic"], c["ks"]))


# --- special functions ---------------------------------------------------------------------------


def test_the_incomplete_gamma_matches_closed_forms() -> None:
    x = np.array([0.0, 0.01, 0.5, 1.0, 2.5, 7.0, 40.0, 200.0])
    assert U._gammainc(1.0, x) == pytest.approx(1 - np.exp(-x), abs=1e-12)
    assert U._gammainc(2.0, x) == pytest.approx(1 - np.exp(-x) * (1 + x), abs=1e-12)
    erf = np.array([math.erf(math.sqrt(v)) for v in x])
    assert U._gammainc(0.5, x) == pytest.approx(erf, abs=1e-12)


def test_digamma_and_trigamma_known_values() -> None:
    euler = 0.5772156649015329
    assert U._digamma(1.0) == pytest.approx(-euler, abs=1e-12)
    assert U._digamma(0.5) == pytest.approx(-euler - 2 * math.log(2), abs=1e-12)
    assert U._trigamma(1.0) == pytest.approx(math.pi**2 / 6, abs=1e-12)
    assert U._trigamma(0.5) == pytest.approx(math.pi**2 / 2, abs=1e-12)


def test_the_gamma_and_weibull_maximum_likelihood_equations_hold() -> None:
    s = 0.37
    k = U._gamma_shape(s)
    assert k is not None and math.log(k) - U._digamma(k) == pytest.approx(s, abs=1e-10)
    logs = np.log(np.random.default_rng(31).weibull(1.7, 4000) * 3.0)
    fit = U._weibull_shape(logs)
    assert fit is not None
    kw, w = fit
    ly = logs - logs.max()
    g = float((w * ly).sum() / w.sum()) - 1.0 / kw - float(ly.mean())
    assert abs(g) < 1e-9 and kw == pytest.approx(1.7, rel=0.05)


# --- agreement with scipy (when it is installed) -----------------------------------------------


def test_the_fits_agree_with_scipy() -> None:
    stats = pytest.importorskip("scipy.stats")
    rng = np.random.default_rng(1)
    gamma_x = np.sort(rng.gamma(3.0, 2.0, 5000))
    weib_x = np.sort(5.0 * rng.weibull(2.5, 5000))
    for x in (gamma_x, weib_x):
        cands, _ = U.distribution_candidates(x)
        p = cands["gamma"]["params"]
        ll = float(stats.gamma.logpdf(x, p["shape"], scale=p["scale"]).sum())
        assert cands["gamma"]["log_likelihood"] == pytest.approx(ll, abs=1e-3)
        ks = stats.kstest(x, "gamma", args=(p["shape"], 0.0, p["scale"])).statistic
        assert cands["gamma"]["ks"] == pytest.approx(ks, abs=1e-6)
        p = cands["weibull"]["params"]
        ll = float(stats.weibull_min.logpdf(x, p["shape"], scale=p["scale"]).sum())
        assert cands["weibull"]["log_likelihood"] == pytest.approx(ll, abs=1e-3)
        ks = stats.kstest(x, "weibull_min", args=(p["shape"], 0.0, p["scale"])).statistic
        assert cands["weibull"]["ks"] == pytest.approx(ks, abs=1e-6)
        p = cands["lognormal"]["params"]
        ll = float(stats.lognorm.logpdf(x, p["sigma"], scale=math.exp(p["mu"])).sum())
        assert cands["lognormal"]["log_likelihood"] == pytest.approx(ll, abs=1e-3)
        p = cands["normal"]["params"]
        ll = float(stats.norm.logpdf(x, p["mu"], p["sigma"]).sum())
        assert cands["normal"]["log_likelihood"] == pytest.approx(ll, abs=1e-3)
        p = cands["exponential"]["params"]
        ll = float(stats.expon.logpdf(x, scale=p["scale"]).sum())
        assert cands["exponential"]["log_likelihood"] == pytest.approx(ll, abs=1e-3)
    p = U.distribution_candidates(gamma_x)[0]["gamma"]["params"]
    shape_hat, _, scale_hat = stats.gamma.fit(gamma_x, floc=0)
    assert p["shape"] == pytest.approx(shape_hat, rel=1e-6)
    assert p["scale"] == pytest.approx(scale_hat, rel=1e-6)


def test_non_finite_values_are_ignored_and_empty_input_is_quiet() -> None:
    rng = np.random.default_rng(40)
    clean = rng.gamma(3.0, 2.0, 1000)
    dirty = np.concatenate([clean, [np.nan, np.inf, -np.inf] * 5])
    assert _stats(dirty) == _stats(clean)
    assert _stats(np.array([])) == {}
    assert _stats(np.array([np.nan] * 50)) == {}
    assert _stats(np.array([np.inf, -np.inf] * 30)) == {}
    # a NaN inside a later step of a column longer than one step is found too
    big = rng.gamma(3.0, 2.0, 2 * U.CHUNK + 10)
    with_nan = big.copy()
    with_nan[-3] = np.nan
    assert _stats(with_nan) == _stats(np.delete(big, len(big) - 3))
