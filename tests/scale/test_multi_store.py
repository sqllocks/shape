"""P6-13: MultiStoreWriter."""

from __future__ import annotations

import threading
import time

import pyarrow as pa
import pytest

from shape.io.multi_store import MultiStoreError, MultiStoreWriter, sink_writer
from shape.scale.sinks.memory import MemorySink

TABLES = {"a": pa.table({"x": [1, 2, 3]}), "b": pa.table({"y": ["p"]})}


class W:
    def __init__(self, name=None, out=None, boom=False, delay=0.0):
        if name:
            self.name = name
        self.out, self.boom, self.delay = out, boom, delay
        self.threads: set[int] = set()
        self.seen: dict | None = None

    def write_all(self, tables, **kwargs):
        self.threads.add(threading.get_ident())
        time.sleep(self.delay)
        if self.boom:
            raise OSError("disk gone")
        self.seen = {"tables": list(tables), "kwargs": kwargs}
        return self.out if self.out is not None else {"ok": True}


def test_every_writer_gets_every_table_and_the_keyword_arguments_in_parallel_threads():
    a, b = W("a", delay=0.05), W("b", delay=0.05)
    result = MultiStoreWriter([a, b]).write_all(TABLES, mode="append")
    assert result.success and set(result.results) == {"a", "b"}
    assert a.seen == b.seen == {"tables": ["a", "b"], "kwargs": {"mode": "append"}}
    assert threading.get_ident() not in a.threads | b.threads and a.threads != b.threads


def test_two_writers_of_one_class_keep_separate_results():
    one, two = W(out="first"), W(out="second")
    result = MultiStoreWriter([one, two]).write_all(TABLES)
    assert result.results == {"W": "first", "W#2": "second"}  # not one overwriting the other


def test_a_failing_writer_does_not_stop_the_others():
    ok, bad = W("ok"), W("bad", boom=True)
    result = MultiStoreWriter([bad, ok]).write_all(TABLES)
    assert not result.success and set(result.errors) == {"bad"} and set(result.results) == {"ok"}
    assert isinstance(result.errors["bad"], OSError)
    assert "errors=1" in repr(result)


def test_raise_on_error_names_every_failure_after_all_finished():
    ok = W("ok")
    writer = MultiStoreWriter([W("b1", boom=True), W("b2", boom=True), ok], raise_on_error=True)
    with pytest.raises(MultiStoreError) as err:
        writer.write_all(TABLES)
    assert set(err.value.errors) == {"b1", "b2"} and ok.seen is not None


def test_a_writer_result_that_reports_failure_is_a_failure():
    class Reports:
        success = False
        errors = ["table x failed", "table y failed"]

    result = MultiStoreWriter([W("w", out=Reports())]).write_all(TABLES)
    assert "2 error(s)" in str(result.errors["w"]) and "table x failed" in str(result.errors["w"])


def test_construction_checks():
    with pytest.raises(ValueError, match="at least one"):
        MultiStoreWriter([])
    with pytest.raises(TypeError, match="write_all"):
        MultiStoreWriter([object()])  # type: ignore[list-item]
    assert "MultiStoreWriter([a, b])" == repr(MultiStoreWriter([W("a"), W("b")]))


def test_a_scale_sink_can_be_driven_as_a_writer():
    mem = MemorySink()
    result = MultiStoreWriter([sink_writer(mem)]).write_all(
        {**TABLES, "empty": pa.table({"z": pa.array([], pa.int64())})}
    )
    assert result.success and result.results["memory"] == {"a": 3, "b": 1, "empty": 0}
    assert mem.result()["a"].equals(TABLES["a"]) and mem.result()["empty"].num_rows == 0
