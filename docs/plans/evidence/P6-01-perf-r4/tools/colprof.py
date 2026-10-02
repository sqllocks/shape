import sys, time, cProfile, pstats
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation.domains import load_domain
from shape.generation.engine import Engine, order_columns
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks")
d,table,colname,n=sys.argv[1],sys.argv[2],sys.argv[3],int(sys.argv[4])
e=Engine(load_domain(d).schema, scale="medium", seed=1042)
for lvl in e.levels:
    if table in lvl: break
    for t in lvl: e.generate_table(t)
tdef=e.schema.tables[table]
built={}
for cname in order_columns(tdef):
    col=tdef.columns[cname]
    if cname==colname: break
    produced=e._column(table,col,0,0,n,built)
    if isinstance(produced, dict):
        for k,v in produced.items(): built[k]=e._as_array(v,"x",n)
        built.setdefault(cname, built[next(iter(produced))])
    else: built[cname]=e._as_array(produced,"x",n)
col=tdef.columns[colname]
for _ in range(50): e._column(table,col,0,0,n,built)
T=time.perf_counter; t=T()
R=2000
for _ in range(R): e._column(table,col,0,0,n,built)
print(f"{1e6*(T()-t)/R:.1f} us per call at n={n}")
pr=cProfile.Profile(); pr.enable()
for _ in range(R): e._column(table,col,0,0,n,built)
pr.disable()
pstats.Stats(pr).sort_stats("tottime").print_stats(18)
