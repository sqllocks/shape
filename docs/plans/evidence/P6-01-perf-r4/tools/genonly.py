import sys, time, tempfile, shutil, json
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation.domains import load_domain, domain_names
from shape.generation.engine import Engine
from shape.generation.output import write_engine
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks"); domain_names()
from shape.kernel.dispatch import get_kernel; get_kernel()
T=time.perf_counter
d,mode=sys.argv[1],sys.argv[2]
t0=T(); loaded=load_domain(d); e=Engine(loaded.schema, scale="medium", seed=1042)
if mode=="gen":
    e.reserved_cores=1
    r=e.generate()
else:
    out=tempfile.mkdtemp(); write_engine(e,"parquet",out); shutil.rmtree(out)
print(json.dumps(T()-t0))
