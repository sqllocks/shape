"""Tier 1: mixture fits, conditional profiles, adversarial score, temporal profiles, periodicity."""

from __future__ import annotations

import numpy as np
import pytest
from fid_helpers import make_table

from shape.fidelity.tier1 import Tier1Profiler


def test_profile_single_has_no_adversarial_result(table):
    p = Tier1Profiler().profile_single(table, "t")
    assert p.table_name == "t" and p.row_count == table.num_rows and p.adversarial is None
    assert p.conditional_profiles and p.temporal_profiles and p.periodicity


def test_conditional_profiles_equal_a_data_frame_groupby(table):
    p = Tier1Profiler().profile_single(table)
    df = table.to_pandas()
    got = {(c.primary_col, c.conditioned_on): c for c in p.conditional_profiles}
    assert ("amount", "segment") in got and ("wave", "segment") in got
    # at most the first 5 low-cardinality text columns condition; booleans are numeric, dates text
    assert {c.conditioned_on for c in p.conditional_profiles} <= {"segment", "email", "day"}
    for (num, cond), prof in got.items():
        assert list(prof.stats_by_value) == [
            str(k)
            for k in sorted(df[cond].dropna().unique())
            if df[df[cond] == k][num].dropna().size >= 5
        ]
        for key, st in prof.stats_by_value.items():
            g = df[df[cond].astype(str) == key][num].dropna()
            assert st["count"] == len(g)
            assert st["mean"] == pytest.approx(g.mean(), rel=1e-12)
            assert st["std"] == pytest.approx(g.std(), rel=1e-12)
            assert st["p25"] == pytest.approx(g.quantile(0.25), rel=1e-12)
            assert st["p75"] == pytest.approx(g.quantile(0.75), rel=1e-12)


def test_temporal_profile_equals_a_data_frame(table):
    p = Tier1Profiler().profile_single(table)
    tp = p.temporal_profiles["at"]
    s = table.to_pandas()["at"].dropna().sort_values()
    gaps = s.diff().dropna().dt.total_seconds()
    assert tp.mean_gap_seconds == pytest.approx(gaps.mean(), rel=1e-12)
    assert tp.std_gap_seconds == pytest.approx(gaps.std(), rel=1e-12)
    assert tp.min_gap_seconds == pytest.approx(gaps.min(), rel=1e-12)
    assert tp.max_gap_seconds == pytest.approx(gaps.max(), rel=1e-12)
    secs = s.astype(np.int64) // 10**9
    assert tp.autocorrelation_lag1 == pytest.approx(secs.autocorr(lag=1), rel=1e-12)
    assert tp.autocorrelation_lag7 == pytest.approx(secs.autocorr(lag=7), rel=1e-12)
    assert tp.gap_distribution in ("exponential", "normal")  # exponential gaps


def test_short_columns_are_left_out():
    import pyarrow as pa

    stamps = pa.array([i * 10**9 for i in range(9)] + [None] * 10, type=pa.timestamp("ns"))
    t = pa.table({"v": pa.array(np.arange(19, dtype=float)), "t": stamps})
    p = Tier1Profiler().profile_single(t)
    assert p.gmm_fits == {} and p.temporal_profiles == {} and p.periodicity == {}


def test_periodicity_finds_the_period(table):
    r = Tier1Profiler().profile_single(table).periodicity["wave"]
    assert r.is_periodic and r.dominant_period == pytest.approx(20.0)
    assert len(r.top_periods) == 5
    assert not Tier1Profiler().profile_single(table).periodicity["bimodal"].is_periodic


def test_periodicity_needs_32_values_and_covers_ten_columns():
    import pyarrow as pa

    t = pa.table({f"c{i}": pa.array(np.random.default_rng(i).normal(size=40)) for i in range(12)})
    assert list(Tier1Profiler().profile_single(t).periodicity) == [f"c{i}" for i in range(10)]
    assert Tier1Profiler().profile_single(t.slice(0, 31)).periodicity == {}


def test_without_scikit_learn_the_rest_runs_and_says_what_is_missing(table, other, no_sklearn):
    p = Tier1Profiler().profile_pair(table, other, "t")
    assert p.gmm_fits == {} and p.adversarial is None
    assert any("mixture fits skipped" in n and "sqllocks-shape[advanced]" in n for n in p.notes)
    assert any("adversarial test skipped" in n for n in p.notes)
    assert p.conditional_profiles and p.temporal_profiles and p.periodicity


def test_without_scipy_the_gap_distribution_is_left_out(table, no_scipy):
    p = Tier1Profiler().profile_single(table)
    assert p.temporal_profiles["at"].gap_distribution is None
    assert any("gap distributions skipped" in n for n in p.notes)
    assert p.temporal_profiles["at"].mean_gap_seconds > 0


class TestWithScikitLearn:
    @pytest.fixture(autouse=True)
    def _sklearn(self):
        pytest.importorskip("sklearn")

    def test_mixture_picks_two_components_for_a_bimodal_column(self, table):
        fits = Tier1Profiler().profile_single(table).gmm_fits
        f = fits["bimodal"]
        assert f.n_components == 2 and sum(f.weights) == pytest.approx(1.0)
        assert sorted(round(m) for m in f.means) == [0, 8]
        assert fits["amount"].n_components >= 1
        assert "flag" not in fits and "id" in fits  # booleans are not numbers

    def test_mixture_is_deterministic(self, table):
        a = Tier1Profiler().profile_single(table).gmm_fits["bimodal"]
        b = Tier1Profiler().profile_single(table).gmm_fits["bimodal"]
        assert a == b

    def test_same_distribution_is_hard_to_tell_apart(self):
        a, b = make_table(5), make_table(6)
        adv = Tier1Profiler().profile_pair(a, b).adversarial
        assert adv is not None and adv.auc_roc < 0.65 and adv.passed
        assert adv.distinguishability_score == round((adv.auc_roc - 0.5) * 200, 2)

    def test_a_shifted_distribution_is_caught(self):
        adv = Tier1Profiler().profile_pair(make_table(5), make_table(6, shift=40.0)).adversarial
        assert adv is not None and adv.auc_roc > 0.95 and not adv.passed
        assert adv.top_features[0][0] in ("amount", "bimodal")
        assert len(adv.top_features) <= 10
        assert adv.n_samples == 1600

    def test_threshold_and_sample_cap(self):
        a, b = make_table(5, n=3000), make_table(6, n=3000)
        adv = (
            Tier1Profiler(adversarial_threshold=0.0, max_rows_adversarial=1000)
            .profile_pair(a, b)
            .adversarial
        )
        assert adv is not None and adv.n_samples == 1000 and not adv.passed

    def test_features_are_the_shared_columns_in_reference_order(self, table, other):
        adv = Tier1Profiler().profile_pair(table, other.drop(["wave"])).adversarial
        assert adv is not None
        assert "wave" not in {f for f, _ in adv.top_features}

    def test_pair_is_deterministic(self):
        a, b = make_table(5), make_table(6, shift=2.0)
        x = Tier1Profiler().profile_pair(a, b).to_dict()
        y = Tier1Profiler().profile_pair(a, b).to_dict()
        assert x == y

    def test_no_common_columns_or_too_few_rows(self, table):
        import pyarrow as pa

        p = Tier1Profiler().profile_pair(table, pa.table({"zzz": [1, 2, 3]}))
        assert p.adversarial is None and any("share no columns" in n for n in p.notes)
        tiny = pa.table({"a": [1.0, 2.0, 3.0]})
        p = Tier1Profiler().profile_pair(tiny, tiny)
        assert p.adversarial is None and any("fewer than 20" in n for n in p.notes)

    def test_to_dict_is_json_ready(self, table, other):
        import json

        d = Tier1Profiler().profile_pair(table, other).to_dict()
        json.dumps(d, allow_nan=False)
        assert d["adversarial"]["distinguishability_score"] is not None


def test_a_missing_timestamp_is_a_very_small_number_the_classifier_can_see():
    pytest.importorskip("sklearn")
    import pyarrow as pa

    n = 400
    rng = np.random.default_rng(0)
    real = pa.table(
        {"x": rng.normal(size=n), "t": pa.array([1 + i for i in range(n)], pa.timestamp("s"))}
    )
    nulls = pa.array([None if i % 2 else 1 + i for i in range(n)], pa.timestamp("s"))
    synth = pa.table({"x": rng.normal(size=n), "t": nulls})
    adv = Tier1Profiler().profile_pair(real, synth).adversarial
    assert adv is not None and adv.auc_roc > 0.7 and adv.top_features[0][0] == "t"
    same = pa.table({"x": rng.normal(size=n), "t": real["t"]})
    fair = Tier1Profiler().profile_pair(real, same).adversarial
    assert fair is not None and fair.auc_roc < 0.6


def test_the_gap_distribution_does_not_hand_scipy_a_named_norm_with_args() -> None:
    """SciPy 1.18 turned ``kstest(x, "norm", args=(loc, scale))`` into a bare ``ndtr(x, loc,
    scale)`` call, which raises. The normal fit is passed as a frozen distribution's cdf."""
    from scipy import stats as real_stats

    from shape.fidelity.tier1 import _gap_distribution

    class Stats:
        norm = staticmethod(real_stats.norm)

        @staticmethod
        def kstest(x, cdf, args=()):  # type: ignore[no-untyped-def]
            if cdf == "norm":
                raise TypeError("ndtr() takes from 1 to 2 positional arguments but 3 were given")
            return real_stats.kstest(x, cdf, args=args)

    gaps = np.random.default_rng(3).normal(10.0, 2.0, size=300)
    assert _gap_distribution(Stats, gaps) == "normal"
