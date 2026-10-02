import sys, time, os, shutil
import numpy, pyarrow as pa
pa.array(["w"])
from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.generation.output import write_engine
from shape.plugins.host import default_host
h=default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks")
from shape.kernel.dispatch import get_kernel; get_kernel()
dom, rg, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
t0=time.perf_counter()
l=load_domain(dom); e=Engine(l.schema, scale="medium", seed=1042)
write_engine(e,"parquet",out,row_group_rows=rg)
t=time.perf_counter()-t0
sz=sum(os.path.getsize(os.path.join(out,f)) for f in os.listdir(out))
print(round(t,4), sz)
