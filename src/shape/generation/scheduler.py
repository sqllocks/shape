"""Dependency-driven scheduling of the chunks of a schema's tables.

A table starts as soon as the tables it depends on are complete, not when the whole level before it
is, and the chunks that are ready are taken longest-path-first (the table whose children have the
most work behind them goes first). The calling thread runs the chunks itself while the queued work
is small, because a pool of threads costs more than it gains on a table of a few thousand rows
(each thread's first calls are cold, and the GIL is shared); worker threads are added when the
queue holds enough work to pay for them. Every callback runs on the calling thread, in the order
chunks and tables arrive.

Nothing here touches a value: a chunk is a function of its row range and of the tables it depends
on, so the tables are the same for any order, chunk size or number of threads."""

from __future__ import annotations

import heapq
import itertools
import queue
import threading
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Cells (rows x columns) of queued work that a worker thread is added for, from two threads on. A
# cell costs about 30 to 100 ns on the machines measured, so this is a few milliseconds of work.
# Measured with the same runs interleaved: 12,000 to 100,000 are within noise of each other, and
# 400,000 or more is slower (retail +9%, education +8%, marketing +25% at 1.6 million).
SPAWN_CELLS = 50_000


@dataclass(frozen=True, slots=True)
class Chunk:
    """Rows ``start .. start + rows - 1`` of ``table``; ``cells`` is rows x columns (its cost)."""

    table: str
    index: int
    start: int
    rows: int
    cells: int


def run_tables(
    order: Sequence[str],
    deps: Mapping[str, set[str]],
    chunks: Mapping[str, Sequence[Chunk]],
    run: Callable[[Chunk], Any],
    on_chunk: Callable[[Chunk, Any], None],
    on_table: Callable[[str], None],
    max_workers: int,
    spawn_cells: int = SPAWN_CELLS,
) -> None:
    """Run every chunk of ``chunks`` (table to its chunks; an empty list for a table that is
    already built) respecting ``deps`` (table to the tables that must be complete first;
    ``order`` is any topological order of them). ``on_chunk(chunk, result)`` gets each result as
    it arrives, in any order; ``on_table(name)`` once the table's chunks have all arrived and
    every table it depends on is complete. The first exception raised by ``run`` or a callback
    stops the workers (after the chunks they are on) and is raised."""
    known = set(order)
    waiting = {t: len(deps.get(t, set()) & known - {t}) for t in order}
    children: dict[str, list[str]] = defaultdict(list)
    for t in order:
        for d in deps.get(t, set()) & known - {t}:
            children[d].append(t)
    cells = {t: sum(c.cells for c in chunks.get(t, ())) for t in order}
    path: dict[str, int] = {}
    for t in reversed(order):
        path[t] = cells[t] + max((path.get(c, 0) for c in children[t]), default=0)

    heap: list[tuple[int, int, Chunk]] = []
    ticket = itertools.count()
    cond = threading.Condition()
    results: queue.SimpleQueue[tuple[Chunk, Any, BaseException | None]] = queue.SimpleQueue()
    workers: list[threading.Thread] = []
    stopping = False
    queued = 0  # cells in ``heap``
    in_flight = 0  # chunks queued or running
    remaining = {t: len(chunks.get(t, ())) for t in order}
    complete = 0

    def push(table: str) -> None:
        nonlocal queued, in_flight
        with cond:
            for chunk in chunks[table]:
                heapq.heappush(heap, (-path[table], next(ticket), chunk))
                queued += chunk.cells
                in_flight += 1
            cond.notify_all()

    def start(table: str) -> None:
        if chunks.get(table):
            push(table)
        else:
            finish(table)

    def finish(table: str) -> None:
        nonlocal complete
        complete += 1
        on_table(table)
        for child in children[table]:
            waiting[child] -= 1
            if waiting[child] == 0:
                start(child)

    def work() -> None:
        nonlocal queued
        while True:
            with cond:
                while not heap and not stopping:
                    cond.wait()
                if stopping:
                    return
                _, _, chunk = heapq.heappop(heap)
                queued -= chunk.cells
            try:
                out = run(chunk)
            except BaseException as exc:  # handed to the calling thread
                results.put((chunk, None, exc))
            else:
                results.put((chunk, out, None))

    def take() -> Chunk:
        nonlocal queued
        with cond:
            _, _, chunk = heapq.heappop(heap)
            queued -= chunk.cells
            return chunk

    def deliver(chunk: Chunk, out: Any) -> None:
        nonlocal in_flight
        in_flight -= 1
        on_chunk(chunk, out)
        remaining[chunk.table] -= 1
        if remaining[chunk.table] == 0:
            finish(chunk.table)

    try:
        for t in [t for t in order if waiting[t] == 0]:  # a snapshot: starting one frees others
            start(t)
        while complete < len(order):
            # One worker would only move the work off this thread: threads come in at two or more.
            want = min(max_workers, queued // spawn_cells)
            if want >= 2:
                while len(workers) < want:
                    thread = threading.Thread(
                        target=work, name=f"shape-gen_{len(workers)}", daemon=True
                    )
                    thread.start()
                    workers.append(thread)
            if workers:
                if in_flight == 0:
                    raise RuntimeError("table scheduler stalled: a dependency cycle?")
                chunk, out, error = results.get()
                if error is not None:
                    raise error
                deliver(chunk, out)
            else:
                if not heap:
                    raise RuntimeError("table scheduler stalled: a dependency cycle?")
                chunk = take()
                deliver(chunk, run(chunk))
    finally:
        with cond:
            stopping = True
            cond.notify_all()
        for thread in workers:
            thread.join()
