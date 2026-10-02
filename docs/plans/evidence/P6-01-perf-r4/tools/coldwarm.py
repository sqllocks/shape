import sys, time, tempfile, shutil, threading, collections
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
    t0=T(); r=orig(self, table, c, chunk, row_start, n_rows, built); ev.append((table,c.name,c.strategy,c.generator.get("provider") or c.generator.get("distribution") or "",row_start,n_rows,T()-t0)); return r
Engine._column=col
d=sys.argv[1]
runs=[]
for it in range(3):
    ev.clear()
    e=Engine(load_domain(d).schema, scale="medium", seed=1042)
    out=tempfile.mkdtemp(); t=T(); output.write_engine(e,"parquet",out); wall=T()-t; shutil.rmtree(out)
    runs.append((wall,list(ev)))
print("wall per iteration ms:", [round(1e3*r[0],1) for r in runs])
cold={ (a,b,c,d_,e_):f for a,b,c,d_,e_,n,f in runs[0][1]}; warm={ (a,b,c,d_,e_):f for a,b,c,d_,e_,n,f in runs[2][1]}
agg=collections.defaultdict(lambda:[0,0.0,0.0])
for k,v in cold.items():
    s=(k[2],k[3]); agg[s][0]+=1; agg[s][1]+=v; agg[s][2]+=warm.get(k,0)
print("per strategy: cols, cold ms, warm ms, excess")
for s,(n,c,w) in sorted(agg.items(), key=lambda kv: -(kv[1][1]-kv[1][2])):
    print(f"{str(s):38s} {n:3d} {1e3*c:7.1f} {1e3*w:7.1f} {1e3*(c-w):7.1f}")
print("total cold %.1f warm %.1f"%(1e3*sum(cold.values()),1e3*sum(warm.values())))
