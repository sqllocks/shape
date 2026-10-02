import sys, time, tempfile, shutil, threading
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation.domains import load_domain, domain_names
from shape.generation.engine import Engine
from shape.generation import output
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks"); domain_names()
from shape.kernel.dispatch import get_kernel; get_kernel()
T=time.perf_counter
ev=[]
orig=Engine._column
def col(self, table, c, chunk, row_start, n_rows, built):
    t0=T(); r=orig(self, table, c, chunk, row_start, n_rows, built); ev.append((t0,T(),threading.get_ident(),table,c.name,c.strategy,c.generator.get("provider") or c.generator.get("distribution") or "",n_rows)); return r
Engine._column=col
loaded=load_domain(sys.argv[1]); e=Engine(loaded.schema, scale="medium", seed=1042)
t00=T()
out=tempfile.mkdtemp(); output.write_engine(e,"parquet",out); shutil.rmtree(out)
tids={}
for t0,t1,tid,table,cn,st,sub,n in sorted(ev):
    i=tids.setdefault(tid,len(tids))
    if (t1-t0)>float(sys.argv[2]) *1e-3:
        print(f"T{i} {1e3*(t0-t00):6.1f} +{1e3*(t1-t0):5.2f} ms  {table}.{cn} [{st} {sub}] n={n}")
