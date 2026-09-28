from shape.history.dag import HistoryDAG


def test_dag():
    h = HistoryDAG()
    a = h.commit({"v": 1})
    b = h.commit({"v": 2}, (a,))
    c = h.commit({"v": 3}, (a,))
    m = h.commit({"v": 4}, (b, c))
    h.tag("latest", m)
    assert h.resolve("latest") == m and {a, b, c}.issubset(h.ancestors("latest"))
