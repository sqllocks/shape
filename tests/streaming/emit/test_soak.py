"""The realtime rate holds at 10,000 events/s (P5-01 acceptance).

CI runs 10 minutes (the default). The nightly job sets ``SHAPE_RATE_SOAK_SECONDS=3600`` for the
hour. The events go through the real encoder into a file (``/dev/null``), so the measured rate
includes generation, pacing, encoding and delivery. The run must deliver within 5% of the target
overall and in every full 10-second window, and never fall behind its own schedule by a second.
"""

from __future__ import annotations

import os

import pytest

from shape.cli.generation import load_target
from shape.generation.engine import Engine
from shape.streaming.emit import EmitConfig, EmitRunner, EventPlan, FileSink

RATE = 10_000
SECONDS = int(os.environ.get("SHAPE_RATE_SOAK_SECONDS", "600"))
WINDOW = 10


@pytest.mark.heavy
@pytest.mark.realtime
def test_realtime_rate_holds_at_10000_events_per_second() -> None:
    rows = RATE * (SECONDS + 5)
    engine = Engine(load_target("retail"), scale="small", seed=5, row_counts={"order_line": rows})
    plan = EventPlan(engine, tables=["order_line"])
    sink = FileSink(os.devnull, append=False)
    cfg = EmitConfig(realtime=True, rate=RATE, duration=SECONDS)
    report = EmitRunner(plan, sink, cfg).run()
    windows = [
        sum(report.per_second[i : i + WINDOW]) / WINDOW
        for i in range(0, len(report.per_second) - WINDOW + 1, WINDOW)
    ]
    print(  # visible with -s: the numbers the status file records
        f"soak: {report.events} events in {SECONDS}s, rate {report.rate:.1f}/s, "
        f"{WINDOW}s windows min {min(windows):.1f} max {max(windows):.1f}, "
        f"max lag {report.max_lag * 1000:.1f} ms"
    )
    assert report.stopped_by == "duration"
    assert report.events == pytest.approx(RATE * SECONDS, rel=0.05)
    assert abs(report.rate / RATE - 1) < 0.05, report.rate
    full = report.per_second[: SECONDS - SECONDS % WINDOW]
    for i in range(0, len(full), WINDOW):
        window = sum(full[i : i + WINDOW]) / WINDOW
        assert abs(window / RATE - 1) < 0.05, (i, window)
    assert report.max_lag < 1.0, report.max_lag
