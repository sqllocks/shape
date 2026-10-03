"""The realtime rate holds at 10,000 events/s (P5-01 acceptance).

CI runs 10 minutes (the default). The nightly job sets ``SHAPE_RATE_SOAK_SECONDS=3600`` for the
hour. The events go through the real encoder into a file (``/dev/null``), so the measured rate
includes generation, pacing, encoding and delivery. The run must deliver within 5% of the target
overall and in every full 10-second window, and never fall behind its own schedule by a second.
"""

from __future__ import annotations

import faulthandler
import gc
import os
import tempfile
import threading
import time
from typing import Any

import pytest

from shape.cli.generation import load_target
from shape.generation.engine import Engine
from shape.streaming.emit import EmitConfig, EmitRunner, EventPlan, FileSink

RATE = 10_000
SECONDS = int(os.environ.get("SHAPE_RATE_SOAK_SECONDS", "600"))
WINDOW = 10


class _StallProbe:
    """Evidence for a failed soak, never part of what it asserts: where in the run the process
    stopped, and why. A thread that only sleeps records every time it woke more than 0.25 s late
    (the whole process was not scheduled, or something held the GIL); a ``gc`` callback records
    collections over 0.1 s; ``faulthandler`` (a C thread, which needs no GIL) dumps every thread's
    stack when the probe thread has not run for 3 s, which names the code that held the GIL."""

    def __init__(self) -> None:
        self.gaps: list[tuple[float, float]] = []  # (seconds into the run, seconds late)
        self.collections: list[tuple[float, float, int]] = []  # (at, seconds, generation)
        self._stop = threading.Event()
        self._started = time.perf_counter()
        self._gc_started = 0.0
        self._dump = tempfile.TemporaryFile(mode="w+")
        self._thread = threading.Thread(target=self._loop, name="soak-probe", daemon=True)

    def _on_gc(self, phase: str, info: dict[str, Any]) -> None:
        now = time.perf_counter()
        if phase == "start":
            self._gc_started = now
        elif now - self._gc_started > 0.1:
            self.collections.append(
                (self._gc_started - self._started, now - self._gc_started, info["generation"])
            )

    def _loop(self) -> None:
        last = time.perf_counter()
        while not self._stop.is_set():
            faulthandler.dump_traceback_later(3.0, file=self._dump)
            time.sleep(0.02)
            now = time.perf_counter()
            if now - last > 0.25:
                self.gaps.append((last - self._started, now - last - 0.02))
            last = now

    def __enter__(self) -> _StallProbe:
        gc.callbacks.append(self._on_gc)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()
        faulthandler.cancel_dump_traceback_later()
        gc.callbacks.remove(self._on_gc)

    def describe(self) -> str:
        self._dump.seek(0)
        dumps = self._dump.read()
        return (
            "probe gaps (s into run, s late): "
            f"{[(round(a, 1), round(b, 2)) for a, b in self.gaps]}; "
            f"gc over 0.1 s (at, s, generation): "
            f"{[(round(a, 1), round(b, 2), g) for a, b, g in self.collections]}; "
            f"stack dumps after 3 s without the probe running: {dumps[:6000] or 'none'}"
        )


@pytest.mark.heavy
@pytest.mark.realtime
def test_realtime_rate_holds_at_10000_events_per_second() -> None:
    rows = RATE * (SECONDS + 5)
    engine = Engine(load_target("retail"), scale="small", seed=5, row_counts={"order_line": rows})
    plan = EventPlan(engine, tables=["order_line"])
    sink = FileSink(os.devnull, append=False)
    cfg = EmitConfig(realtime=True, rate=RATE, duration=SECONDS)
    with _StallProbe() as probe:
        report = EmitRunner(plan, sink, cfg).run()
    off = [(i, n) for i, n in enumerate(report.per_second) if abs(n / RATE - 1) > 0.05]
    evidence = (
        f"seconds off the rate (index, events): {off[:40]}; retries {report.retries}, "
        f"max queue depth {report.max_queue_depth}, max lag {report.max_lag:.2f} s; "
        f"{probe.describe()}"
    )
    windows = [
        sum(report.per_second[i : i + WINDOW]) / WINDOW
        for i in range(0, len(report.per_second) - WINDOW + 1, WINDOW)
    ]
    print(  # visible with -s: the numbers the status file records
        f"soak: {report.events} events in {SECONDS}s, rate {report.rate:.1f}/s, "
        f"{WINDOW}s windows min {min(windows):.1f} max {max(windows):.1f}, "
        f"max lag {report.max_lag * 1000:.1f} ms"
    )
    assert report.stopped_by == "duration", evidence
    assert report.events == pytest.approx(RATE * SECONDS, rel=0.05), evidence
    assert abs(report.rate / RATE - 1) < 0.05, (report.rate, evidence)
    full = report.per_second[: SECONDS - SECONDS % WINDOW]
    for i in range(0, len(full), WINDOW):
        window = sum(full[i : i + WINDOW]) / WINDOW
        assert abs(window / RATE - 1) < 0.05, (i, window, evidence)
    assert report.max_lag < 1.0, (report.max_lag, evidence)
