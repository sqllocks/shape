import sys, time, tempfile, shutil, resource, json, os
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation.domains import load_domain, domain_names
from shape.generation.engine import Engine
from shape.generation.output import write_engine
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks"); domain_names()
from shape.kernel.dispatch import get_kernel; get_kernel()
T=time.perf_counter
d=sys.argv[1]
r0=resource.getrusage(resource.RUSAGE_SELF); t0=T()
loaded=load_domain(d); e=Engine(loaded.schema, scale="medium", seed=1042)
out=tempfile.mkdtemp(); write_engine(e,"parquet",out); shutil.rmtree(out)
t1=T(); r1=resource.getrusage(resource.RUSAGE_SELF)
print(json.dumps(dict(wall=1e3*(t1-t0), user=1e3*(r1.ru_utime-r0.ru_utime), sys=1e3*(r1.ru_stime-r0.ru_stime), minflt=r1.ru_minflt-r0.ru_minflt, vol=r1.ru_nvcsw-r0.ru_nvcsw, invol=r1.ru_nivcsw-r0.ru_nivcsw)))
