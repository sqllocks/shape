import cProfile, pstats, sys, time, os
import numpy, pyarrow as pa
pa.array(["w"])
from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.plugins.host import default_host
h=default_host(); h.load_all("shape.strategies")
from shape.kernel.dispatch import get_kernel; get_kernel()
l=load_domain(sys.argv[1]); e=Engine(l.schema,scale="medium",seed=1042)
pr=cProfile.Profile(); pr.enable()
t=time.perf_counter()
e.generate()
print("single-thread generate s",round(time.perf_counter()-t,3))
pr.disable()
st=pstats.Stats(pr); st.sort_stats("tottime").print_stats(int(sys.argv[2]))
