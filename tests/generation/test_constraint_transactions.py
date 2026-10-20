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
