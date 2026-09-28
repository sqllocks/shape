from shape.profile import KLL, HyperLogLog, NumericProfile, SpaceSaving


def test_numeric_counts_and_moments():
    p = NumericProfile().update([1, 2, 3, None, float("nan"), float("inf"), float("-inf")])
    s = p.summary()
    assert (
        s["count"] == 7 and s["finite_count"] == 3 and s["null_count"] == 1 and s["nan_count"] == 1
    )
    assert s["mean"] == 2


def test_merge_moments_equivalent():
    a = NumericProfile().update(range(50))
    b = NumericProfile().update(range(50, 100))
    a.merge(b)
    c = NumericProfile().update(range(100))
    assert abs(a.mean - c.mean) < 1e-12 and abs(a.m2 - c.m2) < 1e-9


def test_hll_reasonable():
    h = HyperLogLog(p=10)
    [h.update(i) for i in range(10000)]
    assert abs(h.estimate() - 10000) / 10000 < 0.12


def test_topk():
    s = SpaceSaving(4)
    [s.update(x) for x in ["a"] * 20 + ["b"] * 10 + ["c"] * 3]
    assert s.top(1)[0][0] == "a"


def test_kll_quantile():
    k = KLL(50)
    [k.update(i) for i in range(1000)]
    assert 400 < k.quantile(0.5) < 600
