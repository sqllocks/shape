"""P6-01-perf: the dependency-driven table scheduler (``shape.generation.scheduler``).

It decides when chunks run, never what they hold: the tests are about order (a table after the
tables it needs, and not later than that), where chunks run (the calling thread while the work is
small), and what happens when one fails."""

from __future__ import annotations

import threading
import time

import pytest

from shape.generation.scheduler import Chunk, run_tables


def _chunks(table: str, n: int, cells: int = 10) -> list[Chunk]:
    return [Chunk(table, i, i * 10, 10, cells) for i in range(n)]


class Log:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ran: list[tuple[str, int]] = []
        self.threads: set[str] = set()
        self.delivered: list[tuple[str, int]] = []
        self.done: list[str] = []

    def run(self, chunk: Chunk) -> str:
        with self.lock:
            self.ran.append((chunk.table, chunk.index))
            self.threads.add(threading.current_thread().name)
        return f"{chunk.table}{chunk.index}"

    def on_chunk(self, chunk: Chunk, out: str) -> None:
        assert out == f"{chunk.table}{chunk.index}"
        self.delivered.append((chunk.table, chunk.index))

    def on_table(self, name: str) -> None:
        self.done.append(name)


def _go(order, deps, chunks, log, workers, **kw):
    run_tables(order, deps, chunks, log.run, log.on_chunk, log.on_table, workers, **kw)


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_a_table_runs_after_its_parents_and_is_done_once(workers):
    order = ["a", "b", "c", "d"]
    deps = {"b": {"a"}, "c": {"a"}, "d": {"b", "c"}}
    chunks = {t: _chunks(t, 3) for t in order}
    log = Log()
    _go(order, deps, chunks, log, workers, spawn_cells=1)
    assert sorted(log.done) == order
    assert log.done.index("a") < log.done.index("b") < log.done.index("d")
    assert log.done.index("a") < log.done.index("c") < log.done.index("d")
    first = {}
    last = {}
    for i, (t, _) in enumerate(log.ran):
        first.setdefault(t, i)
        last[t] = i
    # a chunk of a table starts only after every chunk of its parents has finished (delivered)
    delivered_at = {t: max(i for i, (x, _) in enumerate(log.delivered) if x == t) for t in order}
    for child, parents in deps.items():
        for parent in parents:
            assert delivered_at[parent] < log.delivered.index((child, 0))
    assert len(log.delivered) == 12


def test_a_table_starts_when_its_own_parents_are_done_not_when_the_level_is():
    """``slow`` has no children and is held until ``child`` has run; with level barriers ``child``
    would wait for ``slow`` and the test would time out."""
    release = threading.Event()
    ran = threading.Event()
    log = Log()

    def run(chunk: Chunk) -> str:
        if chunk.table == "slow":
            assert release.wait(30), "child did not start while slow was running"
        if chunk.table == "child":
            ran.set()
            release.set()
        return log.run(chunk)

    order = ["slow", "root", "child"]
    deps = {"child": {"root"}}
    chunks = {t: _chunks(t, 1) for t in order}
    run_tables(order, deps, chunks, run, log.on_chunk, log.on_table, 3, spawn_cells=1)
    assert ran.is_set()
    assert sorted(log.done) == sorted(order)


def test_small_work_runs_on_the_calling_thread():
    log = Log()
    chunks = {"a": _chunks("a", 4, cells=10), "b": _chunks("b", 2, cells=10)}
    _go(["a", "b"], {"b": {"a"}}, chunks, log, 4)  # 40 cells queued, far below SPAWN_CELLS
    assert log.threads == {threading.current_thread().name}


def test_enough_queued_work_brings_in_worker_threads():
    log = Log()
    chunks = {"a": _chunks("a", 8, cells=1_000)}  # 8,000 cells queued: four workers at 1,000 each
    barrier = threading.Barrier(2, timeout=30)

    def run(chunk: Chunk) -> str:
        if chunk.index < 2:
            barrier.wait()  # two chunks are running at the same time, so two threads
        return log.run(chunk)

    run_tables(["a"], {}, chunks, run, log.on_chunk, log.on_table, 4, spawn_cells=1_000)
    assert len(log.delivered) == 8
    assert threading.current_thread().name not in log.threads


def test_the_chunk_on_the_longest_path_goes_first():
    log = Log()
    # "long" has a heavy child; "short" has none: with one worker "long" must be taken first
    order = ["short", "long", "heavy"]
    deps = {"heavy": {"long"}}
    chunks = {"short": _chunks("short", 1, 10), "long": _chunks("long", 1, 10)}
    chunks["heavy"] = _chunks("heavy", 1, 10_000)
    _go(order, deps, chunks, log, 1)
    assert log.ran[0] == ("long", 0)


def test_a_built_table_has_no_chunks_and_still_releases_its_children():
    log = Log()
    chunks = {"built": [], "child": _chunks("child", 2)}
    _go(["built", "child"], {"child": {"built"}}, chunks, log, 2)
    assert log.done == ["built", "child"]
    assert log.ran == [("child", 0), ("child", 1)]


def test_chunks_of_a_table_may_arrive_in_any_order_and_the_table_ends_after_all():
    log = Log()

    def run(chunk: Chunk) -> str:
        if chunk.index == 0:
            time.sleep(0.05)  # the first chunk finishes last
        return log.run(chunk)

    chunks = {"a": _chunks("a", 4, cells=1_000)}
    run_tables(["a"], {}, chunks, run, log.on_chunk, log.on_table, 4, spawn_cells=1_000)
    assert log.delivered[-1] == ("a", 0)  # the slow first chunk arrives last
    assert log.done == ["a"]
    assert len(log.delivered) == 4


@pytest.mark.parametrize("workers", [1, 4])
def test_a_failing_chunk_raises_and_stops_the_workers(workers):
    log = Log()

    def run(chunk: Chunk) -> str:
        if (chunk.table, chunk.index) == ("a", 2):
            raise RuntimeError("generator failed")
        return log.run(chunk)

    chunks = {"a": _chunks("a", 6, cells=1_000), "b": _chunks("b", 2, cells=1_000)}
    before = threading.active_count()
    with pytest.raises(RuntimeError, match="generator failed"):
        run_tables(["a", "b"], {"b": {"a"}}, chunks, run, log.on_chunk, log.on_table, workers, 1)
    assert "b" not in log.done
    assert threading.active_count() == before  # every worker was joined


def test_a_failing_callback_raises_too():
    log = Log()

    def on_table(name: str) -> None:
        raise ValueError("writer failed")

    with pytest.raises(ValueError, match="writer failed"):
        run_tables(["a"], {}, {"a": _chunks("a", 3)}, log.run, log.on_chunk, on_table, 2, 1)


def test_a_dependency_cycle_is_reported_not_waited_for():
    log = Log()
    chunks = {"a": _chunks("a", 1), "b": _chunks("b", 1)}
    with pytest.raises(RuntimeError, match="stalled"):
        _go(["a", "b"], {"a": {"b"}, "b": {"a"}}, chunks, log, 1)


def test_dependencies_on_unknown_tables_and_self_references_are_ignored():
    log = Log()
    chunks = {"a": _chunks("a", 1), "b": _chunks("b", 1)}
    _go(["a", "b"], {"a": {"a", "nowhere"}, "b": {"a"}}, chunks, log, 1)
    assert log.done == ["a", "b"]
