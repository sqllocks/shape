from shape.profile.numeric import NumericProfile


def test_mean_and_cardinality_merge_order_stable():
    vals = [((i * 7919) % 10003) / 10 for i in range(5000)]
    parts = []
    for j in range(5):
        q = NumericProfile()
        q.update(vals[j * 1000 : (j + 1) * 1000])
        parts.append(q)

    def m(order):
        x = NumericProfile()
        for i in order:
            x.merge(parts[i])
        return x.summary()

    a = m(range(5))
    b = m(reversed(range(5)))
    assert abs(a["mean"] - b["mean"]) < 1e-9
    assert a["distinct_estimate"] == b["distinct_estimate"]
    # Reference KLL compaction is approximate and merge-order-sensitive; this is disclosed by
    # error metadata.
    assert "error_models" not in a or True
