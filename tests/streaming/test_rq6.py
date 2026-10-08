import tempfile
from pathlib import Path

import pytest

from shape.streaming import FileCheckpointStore, OnlineShape, deduplicate_ids, replay_with_failures


def test_online_bounded():
    o = OnlineShape(10)
    for i in range(100):
        o.add({"x": i})
    assert len(o.buffer) == 10 and o.total == 100


def test_recovery_exact_reference():
    with tempfile.TemporaryDirectory() as d:
        s = FileCheckpointStore(Path(d) / "c")
        seen = []
        with pytest.raises(RuntimeError):
            replay_with_failures(range(20), seen.append, s, 7)
        replay_with_failures(range(20), seen.append, s)
        assert seen == list(range(20))


def test_dedupe_across_batches():
    np = pytest.importorskip("numpy")
    seen = set()
    a = np.array([1, 1, 2])
    assert a[deduplicate_ids(a, seen)].tolist() == [1, 2]
    b = np.array([2, 3])
    assert b[deduplicate_ids(b, seen)].tolist() == [3]
