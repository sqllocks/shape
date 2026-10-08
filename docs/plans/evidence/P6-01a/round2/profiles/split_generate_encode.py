import sys, time, os, resource, shutil
import numpy, pyarrow as pa
pa.array(["w"])
import pyarrow.parquet as pq
from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.plugins.host import default_host
h=default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks")
from shape.kernel.dispatch import get_kernel; get_kernel()
def ru(): r=resource.getrusage(resource.RUSAGE_SELF); return r.ru_utime+r.ru_stime
dom=sys.argv[1]
l=load_domain(dom); e=Engine(l.schema, scale="medium", seed=1042)
c0=ru(); t0=time.perf_counter()
res=e.generate()
t1=time.perf_counter(); c1=ru()
print(f"generate only: wall {t1-t0:.3f} cpu {c1-c0:.3f}")
shutil.rmtree("/tmp/o/sp",ignore_errors=True); os.makedirs("/tmp/o/sp")
c2=ru(); t2=time.perf_counter()
for n,t in res.tables.items():
    pq.write_table(t,f"/tmp/o/sp/{n}.parquet",compression="snappy",use_dictionary=True,dictionary_pagesize_limit=128*1024,row_group_size=262144)
t3=time.perf_counter(); c3=ru()
print(f"parquet encode (serial, all tables): wall {t3-t2:.3f} cpu {c3-c2:.3f}")
