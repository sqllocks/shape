import pytest

from shape.connectors.qualification import (
    ConnectorRecord,
    ExactlyOnceProjector,
    reconnecting_batches,
)


def test_partition_checkpoint_and_duplicates():
    seen = []
    p = ExactlyOnceProjector()
    rec = [
        ConnectorRecord("0", 0, "a", "a"),
        ConnectorRecord("1", 0, "b", "b"),
        ConnectorRecord("0", 1, "a2", "a"),
        ConnectorRecord("0", 2, "c", "c"),
    ]
    r = p.process(rec, seen.append)
    assert seen == ["a", "b", "c"] and r["duplicates"] == 1 and r["offsets"] == {"0": 2, "1": 0}
    # replay is stale and does not duplicate side effects
    r2 = p.process(rec, seen.append)
    assert seen == ["a", "b", "c"] and r2["stale"] == 4


def test_commit_only_after_success():
    p = ExactlyOnceProjector()

    def bad(v):
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        p.process([ConnectorRecord("0", 7, "x", "x")], bad)
    assert p.store.committed("0") == -1


def test_out_of_order_old_offsets_do_not_regress():
    seen = []
    p = ExactlyOnceProjector()
    p.process(
        [
            ConnectorRecord("0", 5, 5, "5"),
            ConnectorRecord("0", 3, 3, "3"),
            ConnectorRecord("0", 6, 6, "6"),
        ],
        seen.append,
    )
    assert seen == [5, 6] and p.store.committed("0") == 6


def test_reconnect_success_and_bound():
    calls = [0]

    def connect():
        calls[0] += 1
        if calls[0] < 3:
            raise OSError("transport")
        return iter([1, 2])

    assert list(reconnecting_batches(connect, 3)) == [1, 2]
    calls[0] = 0
    with pytest.raises(OSError):
        list(reconnecting_batches(connect, 2))
