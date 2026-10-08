"""Own time of Python frames vs C/native calls in one cold, single-threaded generate() of a domain
at medium (cProfile). pstats keys with filename '~' are built-in/C functions (the Rust kernel, numpy
methods); the rest are Python frames (strategy glue, arrowkit, pyarrow's Python wrappers)."""
import cProfile, pstats, sys, time
import numpy, pyarrow as pa
pa.array(["w"])
from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies")
from shape.kernel.dispatch import get_kernel; get_kernel()
e = Engine(load_domain(sys.argv[1]).schema, scale="medium", seed=1042)
pr = cProfile.Profile(); pr.enable(); t = time.perf_counter(); e.generate(); wall = time.perf_counter() - t; pr.disable()
st = pstats.Stats(pr)
py = sum(v[2] for k, v in st.stats.items() if k[0] != "~")
c = sum(v[2] for k, v in st.stats.items() if k[0] == "~")
k = sum(v[2] for k, v in st.stats.items() if "_kernel" in k[2])
print(f"{sys.argv[1]:16s} wall {wall:.3f}s  python-frame own time {py:.3f}s ({100*py/(py+c):.0f}%)  C/native own time {c:.3f}s ({100*c/(py+c):.0f}%), of which Rust kernel {k:.3f}s")
