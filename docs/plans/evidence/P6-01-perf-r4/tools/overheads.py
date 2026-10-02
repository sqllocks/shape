import sys, time, collections
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation.domains import load_domain, domain_names
from shape.generation.engine import Engine
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks")
T=time.perf_counter
N=int(sys.argv[1]) if len(sys.argv)>1 else 10
agg=collections.defaultdict(lambda:[0,0.0])
for d in domain_names():
    e=Engine(load_domain(d).schema, scale="medium", seed=1042)
    for t in e.order:
        tdef=e.schema.tables[t]
        n=min(N,e.row_counts[t])
        if n==0: continue
        e.generate_table(t) if False else None
        # build parents (so strategies needing them work)
    for lvl in e.levels:
        for t in lvl:
            tdef=e.schema.tables[t]; n=min(N,e.row_counts[t])
            # one pass to build `built` per column timing
            for rep in range(6):
                built={}
                for cname in e._columns_of(t):
                    col=tdef.columns[cname]
                    t0=T(); produced=e._column(t,col,0,0,n,built); dt=T()-t0
                    if isinstance(produced, dict):
                        for k,v in produced.items(): built[k]=e._as_array(v,"x",n)
                        built.setdefault(cname, built[next(iter(produced))])
                    else:
                        built[cname]=e._as_array(produced,"x",n)
                    if rep>=3:
                        g=col.generator
                        key=(col.strategy, g.get("provider") or g.get("distribution") or g.get("pattern") or "")
                        agg[key][0]+=1; agg[key][1]+=dt
            e.generate_table(t)
tot=0
print("strategy                           calls  mean us   total ms (per 3 reps)")
for k,(c,s) in sorted(agg.items(), key=lambda kv:-kv[1][1]):
    print(f"{str(k):36s}{c:6d} {1e6*s/c:8.1f} {1e3*s/3:9.2f}")
    tot+=s
print("total ms per pass", 1e3*tot/3)
