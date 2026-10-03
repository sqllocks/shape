"""AUD-io: regression tests for the connectors (issues filed by the io audit)."""

from __future__ import annotations

import sqlite3

import pytest

from shape.connectors import DBAPISource, EventHubsBatchAdapter, KafkaBatchAdapter


def _conn():
    c = sqlite3.connect(":memory:")
    c.execute("create table a(id, v)")
    c.execute("create table b(id, w)")
    c.execute("insert into a values (1, 'x')")
    c.execute("insert into b values (2, 'y')")
    return c


def test_494_duplicate_result_column_names_are_refused_not_collapsed():
    with pytest.raises(ValueError, match=r"'id'.*alias"):
        list(DBAPISource(_conn(), "select a.id, b.id from a, b").rows())
    rows = list(DBAPISource(_conn(), "select a.id, b.id as b_id from a, b").rows())
    assert rows == [[{"id": 1, "b_id": 2}]]


def test_494_a_statement_without_a_result_set_says_so():
    with pytest.raises(ValueError, match="returns no rows"):
        list(DBAPISource(_conn(), "update a set v = 'z'").rows())


@pytest.mark.parametrize("adapter", ["kafka", "eventhubs"])
def test_495_decode_keeps_later_keys_and_every_value(adapter):
    def decode(rows):
        if adapter == "kafka":
            return KafkaBatchAdapter(lambda m: m).decode_messages(rows)
        return EventHubsBatchAdapter(lambda m: m).decode_events(rows)

    out = decode([{"a": 1}, {"a": 2, "b": 3}])
    assert list(out) == ["a", "b"]
    assert out["a"].tolist() == [1, 2] and out["b"].tolist() == [None, 3]
    mixed = decode([{"a": 1}, {"a": "x"}, {"a": True}])["a"]
    assert mixed.tolist() == [1, "x", True]
    assert [type(v) for v in mixed.tolist()] == [int, str, bool]
    # one kind of value keeps its numpy type
    assert decode([{"a": 1.5}, {"a": 2}])["a"].dtype.kind == "f"
    assert decode([{"a": "x"}, {"a": "y"}])["a"].dtype.kind == "U"


def _down(start):
    raise ConnectionError("down")
    yield  # pragma: no cover


def test_562_reconnects_back_off_doubling_and_capped():
    from shape.connectors.qualification import reconnecting_batches

    slept: list[float] = []
    with pytest.raises(ConnectionError):
        list(reconnecting_batches(_down, 10, sleep=slept.append))
    # a pause after every failure but the last, 0.5 s doubling, capped at 30 s
    assert slept == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]

    slept.clear()
    with pytest.raises(ConnectionError):
        list(reconnecting_batches(_down, 4, backoff=0.1, sleep=slept.append))
    assert slept == pytest.approx([0.1, 0.2, 0.4])

    slept.clear()
    with pytest.raises(ConnectionError):
        list(reconnecting_batches(_down, 1, sleep=slept.append))
    assert slept == []  # one attempt: nothing to wait for


def test_562_backoff_zero_keeps_immediate_retries_and_progress_resets_the_pause():
    from shape.connectors.qualification import reconnecting_batches

    slept: list[float] = []
    with pytest.raises(ConnectionError):
        list(reconnecting_batches(_down, 5, backoff=0, sleep=slept.append))
    assert slept == []

    def flaky(start):
        first = 0 if start is None else start + 1
        if first >= 3:
            raise ConnectionError("down for good")
        yield first, first
        raise ConnectionError("drop")

    slept.clear()
    with pytest.raises(ConnectionError, match="for good"):
        list(reconnecting_batches(flaky, 3, sleep=slept.append))
    # each drop after progress waits the first pause again; the last drop and the first
    # refusal are 2 failures in a row (0.5, 1.0), and the third stops it without a wait
    assert slept == [0.5, 0.5, 0.5, 1.0]

    with pytest.raises(ValueError, match="backoff"):
        list(reconnecting_batches(_down, 3, backoff=-1, sleep=slept.append))
    with pytest.raises(TypeError):
        reconnecting_batches(_down, 3, 0.5)  # type: ignore[misc]
