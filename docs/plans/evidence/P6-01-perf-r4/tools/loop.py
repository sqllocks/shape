import sys, time, tempfile, shutil
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation.domains import load_domain, domain_names
from shape.generation.engine import Engine
from shape.generation.output import write_engine
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks"); domain_names()
from shape.kernel.dispatch import get_kernel; get_kernel()
d=sys.argv[1]; n=int(sys.argv[2]); floor=sys.argv[3]=="floor"
s=load_domain(d).schema
for i in range(n):
    rc={t:1 for t in s.tables} if floor else None
    e=Engine(s, scale="medium", seed=1042, row_counts=rc)
    out=tempfile.mkdtemp(); write_engine(e,"parquet",out); shutil.rmtree(out)
