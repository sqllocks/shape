import sys, time, threading, collections
import numpy, pyarrow as pa
pa.array(["w"])
from shape.generation.domains import load_domain
from shape.generation import engine as E
from shape.generation.output import write_engine
from shape.plugins.host import default_host
h=default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks")
from shape.kernel.dispatch import get_kernel; get_kernel()
ev=[]
oc=E.Engine._column
def col(self, table, c, chunk, row_start, n_rows, built):
    s=time.perf_counter(); r=oc(self, table, c, chunk, row_start, n_rows, built); ev.append((time.perf_counter()-s, table, c.name, c.strategy, n_rows)); return r
E.Engine._column=col
l=load_domain(sys.argv[1]); e=E.Engine(l.schema,scale="medium",seed=1042)
T0=time.perf_counter()
write_engine(e,"parquet",sys.argv[2])
wall=time.perf_counter()-T0
print("wall",round(wall,3),"sum column time",round(sum(x[0] for x in ev),3))
bys=collections.defaultdict(lambda:[0,0.0,0]); 
for d,t,c,s,n in ev: b=bys[s]; b[0]+=1; b[1]+=d; b[2]+=n
print("by strategy (calls, ms, rows, ns/row):")
for s,(k,d,n) in sorted(bys.items(), key=lambda kv:-kv[1][1]): print(f"  {s:18s} {k:4d} {d*1000:7.1f} {n:8d} {d/n*1e9:7.0f}")
byc=collections.defaultdict(float)
for d,t,c,s,n in ev: byc[f"{t}.{c} [{s}]"]+=d
print("top columns (ms):")
for k,v in sorted(byc.items(), key=lambda kv:-kv[1])[:12]: print(f"  {v*1000:6.1f} {k}")
