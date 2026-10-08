import sys, time, threading
import numpy, pyarrow as pa
pa.array(["w"])
from shape.generation.domains import load_domain
from shape.generation import engine as E
from shape.generation.output import write_engine
from shape.plugins.host import default_host
h=default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks")
from shape.kernel.dispatch import get_kernel; get_kernel()
ev=[]
og=E.Engine.generate_chunk
def gc(self, table, row_start, n_rows, *, chunk=None):
    s=time.perf_counter(); r=og(self, table, row_start, n_rows, chunk=chunk); ev.append((s-T0, time.perf_counter()-T0, f"{table}[{row_start}:{row_start+n_rows}]", threading.current_thread().name[-8:])); return r
E.Engine.generate_chunk=gc
ol=E.Engine._generate_level
lv=[]
def gl(self,names,*a,**k):
    s=time.perf_counter()-T0; r=ol(self,names,*a,**k); lv.append((s,time.perf_counter()-T0,names)); return r
E.Engine._generate_level=gl
l=load_domain(sys.argv[1]); e=E.Engine(l.schema,scale="medium",seed=1042)
T0=time.perf_counter()
write_engine(e,"parquet",sys.argv[2])
print("total",round(time.perf_counter()-T0,3))
for s,t,n in lv: print(f"LEVEL {s:.3f}-{t:.3f} {n}")
for x in sorted(ev): print(f"{x[0]:.3f}-{x[1]:.3f} {(x[1]-x[0])*1000:5.1f}ms {x[2]} {x[3]}")
