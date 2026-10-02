"""Interleaved fresh-process A/B: before (a checkout of the start commit with its own built kernel,
given as BEFORE_SRC=<checkout>/src) against this tree, all domains.

    BEFORE_SRC=/path/to/checkout/src python ab_domains.py medium 9 hr,pulse OUT.json

Each fresh process loads the domain, builds the engine and runs write_engine to a temporary
directory (removed afterwards); the order of the two trees alternates run by run.
"""

import json
import os
import statistics
import subprocess
import sys

BEFORE_SRC = os.environ["BEFORE_SRC"]
code = r"""
import sys, time, tempfile, os
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.generation.output import write_engine
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks")
from shape.kernel.dispatch import get_kernel; get_kernel()
d=sys.argv[1]
t0=time.perf_counter(); e=Engine(load_domain(d).schema, scale=sys.argv[2], seed=1042)
out=tempfile.mkdtemp(); write_engine(e,"parquet",out)
el=time.perf_counter()-t0
import shutil; shutil.rmtree(out)
print(el)
"""
doms = (
    sys.argv[3].split(",")
    if len(sys.argv) > 3
    else "hr real_estate pulse supply_chain insurance capital_markets education financial healthcare iot manufacturing marketing telecom retail".split()
)
scale = sys.argv[1]
runs = int(sys.argv[2])
out = {}
for d in doms:
    res = {"before": [], "now": []}
    for r in range(runs):
        order = (("before", {"PYTHONPATH": BEFORE_SRC}), ("now", {}))
        for k, env in order if r % 2 == 0 else order[::-1]:
            o = subprocess.run(
                [sys.executable, "-c", code, d, scale],
                capture_output=True,
                text=True,
                env={**os.environ, **env},
            )
            res[k].append(float(o.stdout.strip().splitlines()[-1]))
    b, n = statistics.median(res["before"]), statistics.median(res["now"])
    out[d] = res
    print(
        f"{d:16s} before {1e3 * b:7.1f} ms (min {1e3 * min(res['before']):6.1f})  now {1e3 * n:7.1f} ms (min {1e3 * min(res['now']):6.1f})  {100 * (n - b) / b:+5.1f}%  cpu-model unchanged",
        flush=True,
    )
json.dump(out, open(sys.argv[4] if len(sys.argv) > 4 else "ab_domains.json", "w"))
