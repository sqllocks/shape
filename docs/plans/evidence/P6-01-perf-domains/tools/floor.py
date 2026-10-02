import statistics
import subprocess
import sys

code = r"""
import sys, time, tempfile, os, shutil
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.generation.output import write_engine
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks")
from shape.kernel.dispatch import get_kernel; get_kernel()
d=sys.argv[1]; T=time.perf_counter
t0=T(); loaded=load_domain(d); t1=T()
rc={t:1 for t in loaded.schema.tables} if sys.argv[2]=="floor" else None
e=Engine(loaded.schema, scale="medium", seed=1042, row_counts=rc); t2=T()
out=tempfile.mkdtemp(); write_engine(e,"parquet",out); t3=T()
shutil.rmtree(out)
print(t1-t0, t2-t1, t3-t2)
"""
for d in sys.argv[1:]:
    for mode in ("floor", "medium"):
        rs = [
            list(
                map(
                    float,
                    subprocess.run(
                        [sys.executable, "-c", code, d, mode], capture_output=True, text=True
                    ).stdout.split(),
                )
            )
            for _ in range(9)
        ]
        med = [statistics.median(c) for c in zip(*rs)]
        print(
            f"{d:14s} {mode:7s} load_domain {1e3 * med[0]:5.1f}  Engine() {1e3 * med[1]:4.1f}  write_engine(generate+write) {1e3 * med[2]:6.1f}  total {1e3 * sum(med):6.1f} ms"
        )
