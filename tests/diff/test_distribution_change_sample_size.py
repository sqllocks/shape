"""W1-08 (#66): ``distribution_change`` respects sample size.

The fitted family's name flips between two samples of one distribution. It is reported only when
the two samples also differ by more than two samples of that size do by chance (the KS critical
value at alpha = 0.001, as ``distribution_shift`` uses), so same-distribution pairs stay quiet."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

import shape


def family_changes(a, b):
    return [c for c in shape.diff(a, b).changes if c["kind"] == "distribution_change"]


def prof(values):
    return shape.profile(pa.table({"v": values}))


def relabel(profile, family):
    (table,) = profile.tables.values()
    table["columns"]["v"]["distribution"] = family


@pytest.mark.parametrize("n", [100, 500, 2000, 10000])
def test_samples_of_one_normal_distribution_do_not_trigger_it(n):
    fired = 0
    for seed in range(40):
        rng = np.random.default_rng(seed)
        fired += bool(family_changes(prof(rng.normal(50, 10, n)), prof(rng.normal(50, 10, n))))
    assert fired == 0


def test_a_real_family_change_is_still_reported():
    rng = np.random.default_rng(5)
    ch = family_changes(prof(rng.normal(50, 5, 3000)), prof(rng.uniform(0, 100, 3000)))
    assert ch and (ch[0]["baseline"], ch[0]["current"]) == ("normal", "uniform")
    assert ch[0]["severity"] == "low" and ch[0]["score"] == 0.2


def test_a_real_family_change_is_still_reported_at_small_samples():
    rng = np.random.default_rng(7)
    ch = family_changes(prof(rng.normal(50, 5, 300)), prof(rng.uniform(0, 100, 300)))
    assert ch


def test_a_label_flip_with_an_unmoved_distribution_is_quiet():
    # two profiles with identical quantiles but different fitted names: no evidence of a change
    rng = np.random.default_rng(1)
    values = rng.normal(50, 10, 2000)
    a, b = prof(values), prof(values)
    relabel(b, "log_normal")
    assert family_changes(a, b) == []


def strip_quantiles(profile):
    (table,) = profile.tables.values()
    col = table["columns"]["v"]
    col["quantiles"] = None
    col["min_value"] = col["max_value"] = None


def test_without_quantiles_only_samples_of_min_rows_or_more_name_a_family():
    for n, expect in ((12, 0), (300, 1)):
        rng = np.random.default_rng(2)
        a, b = prof(rng.normal(50, 5, n)), prof(rng.uniform(0, 100, n))
        for p in (a, b):
            strip_quantiles(p)
        relabel(a, "normal")
        relabel(b, "log_normal")
        assert len(family_changes(a, b)) == expect
