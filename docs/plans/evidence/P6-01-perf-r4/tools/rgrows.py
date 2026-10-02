# like generate.py's shape runner, with the minimum parallel chunk size taken from CHUNK_LOW
import os, sys, time, tempfile, shutil, json
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation import engine as eng
from shape.builtins.sinks import files as _files
_files.ROW_GROUP_ROWS=int(os.environ.get("RG_ROWS","262144"))
low=int(os.environ.get("CHUNK_LOW","32768"))
eng._PARALLEL_CHUNK_ROWS=(low, eng._PARALLEL_CHUNK_ROWS[1])
from shape.generation.domains import load_domain, domain_names
from shape.generation.output import write_engine
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks"); domain_names()
from shape.kernel.dispatch import get_kernel; get_kernel()
T=time.perf_counter
t0=T(); loaded=load_domain(sys.argv[1]); e=eng.Engine(loaded.schema, scale="medium", seed=1042)
out=tempfile.mkdtemp(); write_engine(e,"parquet",out); t1=T(); shutil.rmtree(out)
print("GEN_JSON "+json.dumps({"total_s":t1-t0}))
