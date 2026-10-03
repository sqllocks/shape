"""Evidence for the realtime pacing tests on a CI runner (CI-FIX item 5).

Prints, as JSON lines, what the host does to a process that only sleeps (no shape code), what a
1 ms ticker thread sees while a realtime emit run is under way, how long ``time.sleep(0.05)``
really takes in a loop (the duration test's sink), and the busiest processes at the time. A stall
that shows up in the shape-free probe is the host's, not the runtime's. Not a test: it asserts
nothing and is run by ``.github/workflows/pacing-diagnostic.yml``.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from typing import Any


def _say(kind: str, **fields: Any) -> None:
    print(json.dumps({"probe": kind, **fields}, default=str), flush=True)


def _busiest() -> list[str]:
    if sys.platform == "win32":
        return []
    out = subprocess.run(["ps", "-Ao", "pcpu,comm"], capture_output=True, text=True).stdout
    rows = sorted(out.splitlines()[1:], key=lambda r: -float(r.split(None, 1)[0] or 0))
    return [r.strip() for r in rows[:8]]


def _bare_sleep(seconds: float) -> dict[str, Any]:
    """Absolute 10 ms deadlines for ``seconds``: the worst overshoot and every one above 20 ms."""
    ev = threading.Event()
    t0 = time.perf_counter()
    n = 0
    worst = 0.0
    over = []
    while time.perf_counter() - t0 < seconds:
        n += 1
        due = t0 + n * 0.01
        ev.wait(max(0.0, due - time.perf_counter()))
        late = time.perf_counter() - due
        worst = max(worst, late)
        if late > 0.02:
            over.append(round(late * 1000, 1))
    return {"worst_ms": round(worst * 1000, 1), "over_20ms": over}


def _sleep_loop() -> dict[str, Any]:
    took = []
    for _ in range(10):
        t = time.perf_counter()
        time.sleep(0.05)
        took.append(round((time.perf_counter() - t) * 1000, 1))
    return {"sleep_50ms_took_ms": took}


def _emit_with_ticker() -> dict[str, Any]:
    from shape.cli.generation import load_target
    from shape.generation.engine import Engine
    from shape.streaming.emit import EmitConfig, EmitRunner, EventPlan, MemorySink

    gaps: list[float] = []
    stop = threading.Event()

    def tick() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            time.sleep(0.001)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    plan = EventPlan(Engine(load_target("retail"), scale="small", seed=11), tables=["order_line"])
    ticker = threading.Thread(target=tick)
    ticker.start()
    report = EmitRunner(
        plan, MemorySink(), EmitConfig(realtime=True, rate=2000, max_events=12000)
    ).run()
    stop.set()
    ticker.join()
    return {
        "max_lag_ms": round(report.max_lag * 1000, 1),
        "per_second": report.per_second,
        "ticker_gaps_over_20ms": [round(g * 1000, 1) for g in gaps if g > 0.02],
        "ticker_worst_ms": round(max(gaps) * 1000, 1),
    }


def main() -> int:
    _say(
        "host",
        platform=platform.platform(),
        machine=platform.machine(),
        python=sys.version.split()[0],
        cpus=os.cpu_count(),
        load=os.getloadavg() if hasattr(os, "getloadavg") else None,
        busiest=_busiest(),
    )
    for round_no in range(3):
        _say("bare-sleep", round=round_no, **_bare_sleep(6.0))
        _say("sleep-loop", round=round_no, **_sleep_loop())
        _say("emit-with-ticker", round=round_no, **_emit_with_ticker())
        _say("busiest", round=round_no, busiest=_busiest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
