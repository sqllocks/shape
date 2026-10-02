"""One timed run of ``shape stream``, in this process (run it in the Shape venv).

    "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/stream_1to1/shape_worker.py --scale medium \\
        --seed 1042 -o FILE [--max-events N]

The timed region is the product's command entry point (``shape.cli.main.main``), with everything
it loads on first use imported before it (T-19: imports are excluded for both tools; the
baseline's import loads every strategy and writer): the command's modules, the strategy and sink
plugins and pyarrow's lazy pandas import. The last stdout line is ``STREAM_JSON {...}``.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import resource
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
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
    import numpy  # noqa: F401
    import pyarrow

    import shape.cli.emit
    import shape.cli.stream
    import shape.generation.engine
    import shape.streaming.emit
    from shape.cli.main import main as shape_main
    from shape.plugins.host import default_host

    pyarrow.array(["warm"])
    host = default_host()
    host.load_all("shape.strategies")
    host.load_all("shape.sinks")
    import_s = time.perf_counter() - t_imp

    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)  # no output or checkpoint survives between timed runs (1.4)
    Path(f"{out}.checkpoint").unlink(missing_ok=True)
    cmd = [
        "stream",
        DOMAIN,
        "--table",
        TABLE,
        "--scale",
        a.scale,
        "--no-realtime",
        "--sink",
        "file",
        "-o",
        a.output,
        "--seed",
        str(a.seed),
        "--json",
    ]
    if a.max_events is not None:
        cmd += ["--max-events", str(a.max_events)]
    ru0 = resource.getrusage(resource.RUSAGE_SELF)
    t0 = time.perf_counter()
    report = io.StringIO()
    with contextlib.redirect_stdout(report):
        rc = shape_main(cmd)
    total = time.perf_counter() - t0
    ru1 = resource.getrusage(resource.RUSAGE_SELF)
    if rc != 0:
        print(f"shape stream exited {rc}", file=sys.stderr)
        return 1
    run = json.loads(report.getvalue().strip().splitlines()[-1])
    rec = {
        "total_s": total,
        "import_s": import_s,
        "events": run["events"],
        "emit_s": run["elapsed"],
        "cpu_s": (ru1.ru_utime + ru1.ru_stime) - (ru0.ru_utime + ru0.ru_stime),
        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0,
        "bytes": os.path.getsize(a.output),
    }
    print("STREAM_JSON " + json.dumps(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
