import sys, time, tempfile, shutil, statistics, os
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation import engine as eng
from shape.generation.domains import load_domain, domain_names
from shape.generation.output import write_engine
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks"); domain_names()
from shape.kernel.dispatch import get_kernel; get_kernel()
T=time.perf_counter
d=sys.argv[1]
schema=load_domain(d).schema
orig=eng._PARALLEL_CHUNK_ROWS
def run(low, mode):
    eng._PARALLEL_CHUNK_ROWS=(low, orig[1])
    e=eng.Engine(schema, scale="medium", seed=1042)
    out=tempfile.mkdtemp(); t=T()
    if mode=="gen": e.reserved_cores=1; e.generate()
    else: write_engine(e,"parquet",out)
    dt=T()-t; shutil.rmtree(out)
    n=sum(len(c) for c in e._plan_chunks(list(e.order), 3).values())
    return dt,n
for mode in ("gen","write"):
    for low in (32768, 16384, 8192, 4096):
        rs=[run(low,mode) for _ in range(14)][3:]
        print(f"{d} {mode:5s} low={low:6d} chunks={rs[0][1]:3d} median {1e3*statistics.median(r[0] for r in rs):6.1f} ms min {1e3*min(r[0] for r in rs):6.1f}")
