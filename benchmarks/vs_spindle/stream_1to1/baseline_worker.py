"""One timed run of the baseline's stream command, in this process (run it in the baseline venv).

    "$SPINDLE_PY" benchmarks/vs_spindle/stream_1to1/baseline_worker.py --scale medium \\
        --seed 42 -o FILE [--max-events N]

It does what the command does after its options are parsed (resolve the domain, build the
stream configuration and the file sink, generate, convert to events, emit), with the library
imports done before the timed region (T-19). The last stdout line is ``STREAM_JSON {...}``.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import SPINDLE_ROOT  # noqa: E402

sys.path.insert(0, str(HERE))
from stream_common import DOMAIN, TABLE  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", default="medium")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--max-events", type=int)
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)

    t_imp = time.perf_counter()
    sys.path.insert(0, str(SPINDLE_ROOT))
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    import pyarrow  # noqa: F401
    from sqllocks_spindle.cli import _resolve_domain
    from sqllocks_spindle.streaming import (
        AnomalyRegistry,
        FileSink,
        SpindleStreamer,
        StreamConfig,
    )

    import_s = time.perf_counter() - t_imp

    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    ru0 = resource.getrusage(resource.RUSAGE_SELF)
    t0 = time.perf_counter()
    domain = _resolve_domain(DOMAIN, "3nf")
    config = StreamConfig(max_events=a.max_events, realtime=False)
    sink = FileSink(a.output, mode="w")
    result = SpindleStreamer(
        domain=domain,
        sink=sink,
        config=config,
        anomaly_registry=AnomalyRegistry(),
        scale=a.scale,
        seed=a.seed,
    ).stream(TABLE)
    sink.close()
    total = time.perf_counter() - t0
    ru1 = resource.getrusage(resource.RUSAGE_SELF)
    rec = {
        "total_s": total,
        "import_s": import_s,
        "events": result.events_sent,
        "emit_s": result.elapsed_seconds,
        "cpu_s": (ru1.ru_utime + ru1.ru_stime) - (ru0.ru_utime + ru0.ru_stime),
        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0,
        "bytes": os.path.getsize(a.output),
    }
    print("STREAM_JSON " + json.dumps(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
