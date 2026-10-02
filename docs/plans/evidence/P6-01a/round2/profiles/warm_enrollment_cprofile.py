import cProfile, pstats, sys
import numpy, pyarrow as pa
pa.array(["w"])
from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.plugins.host import default_host
h=default_host(); h.load_all("shape.strategies")
l=load_domain("education"); e=Engine(l.schema,scale="medium",seed=1042)
# make parents available
for t in ("department","course","instructor","student"): e.generate_table(t)
n=e.chunk_rows
for _ in range(3): e.generate_chunk("enrollment",0,n)
pr=cProfile.Profile(); pr.enable()
for _ in range(20): e.generate_chunk("enrollment",0,n)
pr.disable()
pstats.Stats(pr).sort_stats("tottime").print_stats(22)
