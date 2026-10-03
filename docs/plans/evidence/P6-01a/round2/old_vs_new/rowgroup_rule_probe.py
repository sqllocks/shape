import sys, time, os, shutil
import numpy, pyarrow as pa
pa.array(["w"])
from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.generation.output import write_engine
from shape.plugins.host import default_host
h=default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks")
from shape.kernel.dispatch import get_kernel; get_kernel()
dom, mode, out = sys.argv[1], sys.argv[2], sys.argv[3]
shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
t0=time.perf_counter()
l=load_domain(dom); e=Engine(l.schema, scale="medium", seed=1042)
if mode=="fixed": write_engine(e,"parquet",out)
else:
    # per-table groups: at least `mode` groups, between 32768 and 262144 rows
    import shape.generation.output as O
    k=int(mode); orig=O._options
    def opts(fmt, schema, table, options):
        m=orig(fmt, schema, table, options)
        total=e.row_counts[table]
        m.setdefault("row_group_rows", min(262144, max(32768, -(-total//k))))
        return m
    O._options=opts
    write_engine(e,"parquet",out)
t=time.perf_counter()-t0
sz=sum(os.path.getsize(os.path.join(out,f)) for f in os.listdir(out))
print(round(t,4), sz)
