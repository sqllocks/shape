"""Coordinated reruns finish only after workers drain; private state is never persisted."""

import json
import threading
import time

import pytest

from shape.errors import ShapeError
from shape.generation.engine import Engine
from shape.generation.output import TargetOptions, write_targets
from shape.generation.schema import Column, GenSchema, Model, Table


def engine():
    tables = {
        name: Table(name, {"id": Column("id", "integer", {"strategy": "sequence"})}, ["id"])
        for name in ("one", "two", "three")
    }
    return Engine(
        GenSchema(Model("tx", "tx"), tables), row_counts={name: 6 for name in tables}, chunk_rows=1
    )


class Connection:
    def __init__(self):
        self.events = []

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")

    def close(self):
        self.events.append("close")


@pytest.mark.parametrize("fail", [False, True])
def test_prepared_transaction_serializes_and_finishes_after_workers(monkeypatch, fail):
    from shape.generation.write_constraints import _PreparedTransaction

    conn = Connection()
    active = 0
    lock = threading.Lock()
    ended = set()

    class Sink:
        def prepare_constraints(self, uri, plans):
            return _PreparedTransaction(conn, owns_connection=True)

        def write(self, uri, table, batches, **options):
            nonlocal active
            assert options["connection"] is conn
            with lock:
                assert active == 0, "shared SQL connection used concurrently"
                active += 1
            try:
                total = 0
                for batch in batches:
                    total += batch.num_rows
                    time.sleep(0.001)
                    if fail and table == "two":
                        raise ShapeError("planted later load failure")
                return total
            finally:
                ended.add(table)
                active -= 1

    sink = Sink()
    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("postgres", sink))
    if fail:
        with pytest.raises(ShapeError, match="planted"):
            write_targets(engine(), ["postgresql://localhost/db"], TargetOptions(), max_workers=3)
        assert conn.events == ["rollback", "close"]
    else:
        result = write_targets(
            engine(), ["postgresql://localhost/db"], TargetOptions(), max_workers=3
        )
        assert json.loads(json.dumps(result)) == {
            "postgresql://localhost/db": {"one": 6, "two": 6, "three": 6}
        }
        assert conn.events == ["commit", "close"]
    assert ended == {"one", "two", "three"} and active == 0


def test_later_prepare_failure_aborts_earlier_private_transaction(monkeypatch):
    from shape.generation.write_constraints import _PreparedTransaction

    conn = Connection()

    class Sink:
        def prepare_constraints(self, uri, plans):
            if uri.endswith("second"):
                raise ShapeError("second prepare failed")
            return _PreparedTransaction(conn, owns_connection=True)

        def write(self, *args, **kwargs):
            raise AssertionError("no writer may start after preparation fails")

    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("postgres", Sink()))
    with pytest.raises(ShapeError, match="second prepare"):
        write_targets(
            engine(),
            ["postgresql://localhost/first", "postgresql://localhost/second"],
            TargetOptions(),
        )
    assert conn.events == ["rollback", "close"]


def test_commit_failure_aborts_remaining_retained_destinations(monkeypatch):
    from shape.generation.write_constraints import _PreparedTransaction

    first = Connection()
    second = Connection()
    failure = RuntimeError("planted commit failure")

    def fail_commit():
        first.events.append("commit")
        raise failure

    first.commit = fail_commit

    class Sink:
        def prepare_constraints(self, uri, plans):
            conn = first if uri.endswith("first") else second
            return _PreparedTransaction(conn, owns_connection=True)

        def write(self, uri, table, batches, **options):
            return sum(batch.num_rows for batch in batches)

    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("postgres", Sink()))
    with pytest.raises(RuntimeError, match="planted commit failure") as error:
        write_targets(
            engine(),
            ["postgresql://localhost/first", "postgresql://localhost/second"],
            TargetOptions(),
            max_workers=1,
        )
    assert error.value is failure
    assert first.events == ["commit", "rollback", "close"]
    assert second.events == ["rollback", "close"]


def test_commit_failure_survives_rollback_cleanup_failure():
    from shape.generation.write_constraints import _PreparedTransaction

    conn = Connection()
    original = RuntimeError("original commit failure")

    def fail_commit():
        conn.events.append("commit")
        raise original

    def fail_rollback():
        conn.events.append("rollback")
        raise RuntimeError("cleanup rollback failure")

    conn.commit = fail_commit
    conn.rollback = fail_rollback
    transaction = _PreparedTransaction(conn, owns_connection=True)
    with pytest.raises(RuntimeError, match="original commit failure") as error:
        transaction.finish(True)
    assert error.value is original
    assert conn.events == ["commit", "rollback", "close"]


def test_retained_destination_fanout_keeps_the_same_table_in_flight(monkeypatch):
    from shape.generation.write_constraints import _PreparedTransaction

    guard = threading.Lock()
    in_flight = {}

    class Sink:
        def prepare_constraints(self, uri, plans):
            return _PreparedTransaction(Connection(), owns_connection=True)

        def write(self, uri, table, batches, **options):
            with guard:
                assert set(in_flight) <= {table}, "retained destinations must share table order"
                in_flight[table] = in_flight.get(table, 0) + 1
            try:
                total = 0
                for batch in batches:
                    time.sleep(0.001)
                    total += batch.num_rows
                return total
            finally:
                with guard:
                    in_flight[table] -= 1
                    if not in_flight[table]:
                        del in_flight[table]

    monkeypatch.setattr("shape.io.targets.sink_for_target", lambda uri: ("postgres", Sink()))
    result = write_targets(
        engine(),
        ["postgresql://localhost/first", "postgresql://localhost/second"],
        TargetOptions(),
        max_workers=3,
    )
    assert all(rows == {"one": 6, "two": 6, "three": 6} for rows in result.values())
    assert not in_flight
