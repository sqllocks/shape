import sys, time, collections
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks")
T=time.perf_counter
d=sys.argv[1]
e=Engine(load_domain(d).schema, scale="medium", seed=1042)
res=[]; tot=collections.defaultdict(float)
for lvl in e.levels:
    for t in lvl:
        tdef=e.schema.tables[t]; n=min(e.row_counts[t], 65536)
        best={}
        for rep in range(5):
            built={}
            for cname in e._columns_of(t):
                col=tdef.columns[cname]
                t0=T(); produced=e._column(t,col,0,0,n,built); dt=T()-t0
                if isinstance(produced, dict):
                    for k,v in produced.items(): built[k]=e._as_array(v,"x",n)
                    built.setdefault(cname, built[next(iter(produced))])
                else: built[cname]=e._as_array(produced,"x",n)
                best[cname]=min(best.get(cname,1e9),dt)
        e.generate_table(t)
        nchunks=-(-e.row_counts[t]//n)
        for cname,dt in best.items():
            col=tdef.columns[cname]; g=col.generator
            res.append((dt*nchunks,t,cname,col.strategy,g.get("provider") or g.get("distribution") or "",n))
            tot[col.strategy]+=dt*nchunks
res.sort(reverse=True)
s=sum(r[0] for r in res)
print(f"{d}: sum of column times (warm, 1 thread) {1e3*s:.1f} ms")
for r in res[:14]: print(f"{1e3*r[0]:6.2f} ms {r[1]}.{r[2]} [{r[3]} {r[4]}] n={r[5]}")
print({k:round(1e3*v,1) for k,v in sorted(tot.items(), key=lambda kv:-kv[1])})
