import sys, time, cProfile, pstats
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks")
d,table,n=sys.argv[1],sys.argv[2],int(sys.argv[3])
e=Engine(load_domain(d).schema, scale="medium", seed=1042)
for lvl in e.levels:
    if table in lvl: break
    for t in lvl: e.generate_table(t)
for _ in range(50): e.generate_chunk(table,0,n)
T=time.perf_counter; t=T(); R=500
for _ in range(R): e.generate_chunk(table,0,n)
dt=(T()-t)/R
ncol=len(e.schema.tables[table].columns)
print(f"{table} n={n}: {1e6*dt:.0f} us per chunk, {ncol} columns, {1e6*dt/ncol:.0f} us/column")
if len(sys.argv)>4:
    pr=cProfile.Profile(); pr.enable()
    for _ in range(R): e.generate_chunk(table,0,n)
    pr.disable(); pstats.Stats(pr).sort_stats("tottime").print_stats(25)
