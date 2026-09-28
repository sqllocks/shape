from shape.streaming import KeyedState, NumericEvidence


def test_numeric_evidence_merge():
    a, b = NumericEvidence(), NumericEvidence()
    for x in range(100):
        a.update(x)
    for x in range(100, 200):
        b.update(x)
    a.merge(b)
    assert a.count == 200 and 99 < a.mean < 100


def test_keyed_state_bounded_expiry_restore():
    s = KeyedState(10, 5)
    for i in range(20):
        s.put(i, i, float(i))
    assert len(s) <= 5
    r = KeyedState.restore(s.snapshot())
    assert len(r) == len(s)
