"""#323: distribution fitting gives the same bits in every process, threaded or not.

The native kernel evaluates ``exp``/``log`` through numpy's own loops (hooks installed on the
first fit in a process). Columns are profiled on several threads, so the first fits of a process
can start together. Every one of them has to use the same ``exp``/``log`` as a fit run alone:
results may not depend on which thread got there first. Each run is a fresh process, because the
hooks are installed once per process; a tiny GIL switch interval makes the threads interleave
inside the first call, where they would otherwise rarely meet.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

RUNS = 8

_FIT = r"""
import json, sys, threading
import numpy as np
sys.setswitchinterval(1e-6)
from shape.profile.fitting import detect_distribution
rng = np.random.default_rng(7)
n = 3000
cols = [rng.normal(10, 2, n), rng.normal(0, 1, n), rng.lognormal(0, 1, n), rng.uniform(-5, 5, n),
        rng.exponential(3, n), rng.normal(-100, 30, n), rng.lognormal(2, 0.5, n),
        rng.lognormal(1, 0.25, n) - 40.0]
out = [None] * len(cols)
def run(i):
    out[i] = detect_distribution(cols[i])
if sys.argv[1] == "serial":
    for i in range(len(cols)):
        run(i)
else:
    gate = threading.Barrier(len(cols))
    def go(i):
        gate.wait()
        run(i)
    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(cols))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
print(json.dumps(out, sort_keys=True))
"""

_PROFILE = r"""
import json, sys
import numpy as np, pandas as pd
sys.setswitchinterval(1e-6)
import shape
rng = np.random.default_rng(7)
n = 3000
df = pd.DataFrame({
    "fn": rng.normal(10, 2, n), "fz": rng.normal(0, 1, n), "fl": rng.lognormal(0, 1, n),
    "fu": rng.uniform(-5, 5, n), "fe": rng.exponential(3, n), "fn2": rng.normal(-100, 30, n),
    "fl2": rng.lognormal(2, 0.5, n),
})
print(json.dumps(shape.profile(df).to_dict(), sort_keys=True, default=str))
"""


def _run(script: str, *args: str, kernel: str = "rust", loops: str | None = None) -> str:
    env = {**os.environ, "SHAPE_KERNEL": kernel}
    env.pop("PROFILE_THREADS", None)
    env.pop("SHAPE_THREADS", None)
    env.pop("SHAPE_NUMPY_LOOPS", None)
    if loops is not None:
        env["SHAPE_NUMPY_LOOPS"] = loops
    done = subprocess.run(
        [sys.executable, "-c", script, *args], capture_output=True, text=True, env=env, timeout=300
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.mark.parametrize("loops", [None, "python"], ids=["numpy-loops", "numpy-through-python"])
def test_first_fits_racing_on_threads_equal_serial_fits(loops):
    serial = _run(_FIT, "serial", loops=loops)
    threaded = [_run(_FIT, "threads", loops=loops) for _ in range(RUNS)]
    differing = sum(t != serial for t in threaded)
    assert differing == 0, f"{differing} of {RUNS} fresh threaded processes differ from serial"


@pytest.mark.parametrize("kernel", ["rust", "python"])
def test_a_threaded_profile_is_the_same_in_every_fresh_process(kernel):
    outs = [_run(_PROFILE, kernel=kernel) for _ in range(RUNS)]
    first = json.loads(outs[0])
    assert len(first["columns"]) == 7
    assert len(set(outs)) == 1, f"{len(set(outs))} different profiles in {RUNS} fresh processes"
