from shape.streaming import (
    CovarianceEvidence,
    FullEvidenceEngine,
    GeoGridEvidence,
    HashedDependencyEvidence,
    MissingnessPairEvidence,
    PartitionedKeyedState,
    RelationalEvidence,
)


def test_platinum_bounded_evidence_merge():
    a, b = MissingnessPairEvidence(), MissingnessPairEvidence()
    for i in range(100):
        a.update(None if i % 5 == 0 else i, None if i % 7 == 0 else i)
    for i in range(100, 200):
        b.update(None if i % 5 == 0 else i, None if i % 7 == 0 else i)
    a.merge(b)
    assert sum(a.summary().values()) == 200
    c = CovarianceEvidence()
    for i in range(10000):
        c.update(i, i * i)
    assert c.summary()["n"] == 10000
    d = HashedDependencyEvidence(32)
    for i in range(10000):
        d.update(i % 10, (i % 10) ** 2)
    assert d.summary()["mutual_information"] > 1
    g = GeoGridEvidence(max_cells=100)
    for i in range(10000):
        g.update(39 + (i % 1000) / 100, -83)
    assert len(g.cells) <= 100
    r = RelationalEvidence()
    for i in range(100):
        r.update(i, i % 10 != 0)
    assert r.summary()["orphans"] == 10


def test_partitioned_keyed_state_stable_restore():
    s = PartitionedKeyedState(8, 100, 100)
    parts = [s.partition_for(i) for i in range(100)]
    for i in range(100):
        s.put(i, {"v": i}, float(i))
    r = PartitionedKeyedState.restore(s.snapshot())
    assert [r.partition_for(i) for i in range(100)] == parts
    assert r.get(99, 99)["v"] == 99


def test_full_evidence_engine():
    import numpy as np

    e = FullEvidenceEngine(3)
    x = np.arange(10000, dtype=np.int64)
    try:
        r = e.process(
            {
                "id": x,
                "value": (x % 100).astype(float),
                "fk": x % 10,
                "latitude": 39 + x % 10 / 1000.0,
                "longitude": -83 - x % 10 / 1000.0,
            },
            np.asarray(["a"] * 10000),
            parent_count=10,
            dependency=("fk", "id"),
        )
    finally:
        e.close()
    assert (
        r["profile"]["rows"] == 10000
        and r["relational"].orphans == 0
        and r["dependency"].n == 10000
    )
