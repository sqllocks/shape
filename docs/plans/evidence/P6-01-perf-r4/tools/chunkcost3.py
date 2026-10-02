import sys, time, tempfile, shutil, statistics, os
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation import engine as eng
from shape.generation.domains import load_domain, domain_names
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks"); domain_names()
from shape.kernel.dispatch import get_kernel; get_kernel()
T=time.perf_counter
d=sys.argv[1]
schema=load_domain(d).schema
def run(cr):
    e=eng.Engine(schema, scale="medium", seed=1042, chunk_rows=cr)
    e.reserved_cores=0
    t=T(); e.generate(); dt=T()-t
    nch=sum(-(-e.row_counts[tn]//cr) for tn in e.order)
    return dt,nch
for cr in (65536, 16384, 8192, 4096, 2048):
    rs=[run(cr) for _ in range(10)][2:]
    print(f"{d} threads=1 chunk_rows={cr:6d} chunks={rs[0][1]:3d} median {1e3*statistics.median(r[0] for r in rs):6.1f} ms")
