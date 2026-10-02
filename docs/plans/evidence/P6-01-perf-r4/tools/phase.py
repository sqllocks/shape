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
d=sys.argv[1]; floor = len(sys.argv)>2
import shape_domains._packaged as P
from shape.generation.schema import GenSchema
t0=T()
plugin=h.get("shape.domains", d)
t1=T()
doc=P.schema_document(d,"3nf"); t2=T()
refs={x:P.reference_table(d,x) for x in plugin.datasets}
for dd,(dom,src) in plugin.borrowed.items(): P.reference_table(dom,src)
t3=T()
defn=plugin.definition(); t4=T()
from shape.generation.reference import register_dataset
for k,v in defn.reference_data.items(): register_dataset(k,v)
t5=T()
from shape.generation.schema import schema_problems
schema_problems(doc); t6=T()
s=GenSchema.from_dict(doc); t7=T()
rc={t:1 for t in s.tables} if floor else None
e=Engine(s, scale="medium", seed=1042, row_counts=rc); t8=T()
out=tempfile.mkdtemp(); write_engine(e,"parquet",out); t9=T()
shutil.rmtree(out)
print(json.dumps(dict(plugin=t1-t0,json=t2-t1,refs=t3-t2,defn_rest=t4-t3,register=t5-t4,check=t6-t5,from_dict_total=t7-t6,engine=t8-t7,write=t9-t8,total=t9-t0)))
