import time,tempfile,json,threading,urllib.request
import numpy as np
from shape.hub import ShapeHub,ShapeRef
from shape.etl import ShapeETL
from shape.distributed import DistributedProfiler,partition_rows
from shape.generation.joint import fit_joint_numeric,generate_joint_numeric
from shape.temporal import fit_temporal,generate_temporal
from shape.reference import ReferenceAssetStore
from shape.lineage import LineageGraph
from shape.testing import generate_tests
from shape.scenarios import Scenario,generate_scenario
from shape.observability import Metrics
from shape.webapp import ShapeService,serve
from shape.packages import DomainPackageStore
from shape.packs import DomainDefinition,DomainField
R={}
def bench(name,n,fn,minrate=0):
 t=time.perf_counter();fn();e=time.perf_counter()-t;rate=n/max(e,1e-12);R[name]={"units":n,"seconds":e,"units_per_second":rate,"minimum":minrate,"passed":rate>=minrate};return rate
class T:
 def request(self,*a,**k):return {"shape":{"rows":1}}
h=ShapeHub(T());ref=ShapeRef("o","p","n")
bench("01_hub_protocol",200000,lambda:[h.pull(ref) for _ in range(200000)],100000)
with tempfile.TemporaryDirectory() as d:
 s=DomainPackageStore(d)
 bench("02_domain_package_publish",1000,lambda:[s.publish(DomainDefinition(f"d{i}","1.0.0",(DomainField("id","int"),))) for i in range(1000)],500)
rows=[{"x":i} for i in range(100000)]
bench("03_etl_runtime",100000,lambda:ShapeETL().stage("identity",lambda x:x).run(rows),500000)
parts=partition_rows(rows,8)
bench("04_distributed_profile",100000,lambda:DistributedProfiler(4).profile(parts),500000)
rng=np.random.default_rng(1);x=rng.normal(size=500000);y=.8*x+rng.normal(size=500000)
m=fit_joint_numeric({"x":x,"y":y})
bench("05_joint_generation",500000,lambda:generate_joint_numeric(m,500000,2),500000)
tm=fit_temporal(np.arange(1000000,dtype=float))
bench("06_temporal_generation",1000000,lambda:generate_temporal(tm,1000000,2),1000000)
with tempfile.TemporaryDirectory() as d:
 s=ReferenceAssetStore(d);s.publish("a","1",({"id":i,"v":i%10} for i in range(100000)))
 bench("07_reference_index",100000,lambda:s.index("a","1","id"),100000)
g=LineageGraph()
for i in range(10000):g.add_shape(str(i),{})
for i in range(9999):g.connect(str(i),str(i+1))
bench("08_lineage_blast_radius",10000,lambda:g.downstream("0"),10000)
S={"rows":100000,"columns":{"x":{"kind":"numeric","count":100000,"null_count":0,"distinct_estimate":1000,"mean":50,"variance_population":100,"min":0,"max":100}}}
bench("09_test_generation",100000,lambda:generate_tests(S,{"columns":{"x":{"min":0,"max":100}}},100000,1),500000)
sc=Scenario("stress",{"columns.x.mean":"+10%"})
bench("10_scenario_generation",500000,lambda:generate_scenario(S,sc,500000,2),500000)
metrics=Metrics()
bench("11_metrics",200000,lambda:[metrics.inc("rows",1,source="x") for _ in range(200000)],100000)
svc=ShapeService();svc.put("x",S);server=serve(svc);threading.Thread(target=server.serve_forever,daemon=True).start()
try:
 base=f"http://127.0.0.1:{server.server_port}/v1/shapes/x"
 bench("12_web_api",200,lambda:[urllib.request.urlopen(base).read() for _ in range(200)],100)
finally:server.shutdown();server.server_close()
import resource
R["maxrss_kb"]=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
R["all_passed"]=all(x.get("passed",True) for x in R.values() if isinstance(x,dict))
print(json.dumps(R))
raise SystemExit(0 if R["all_passed"] else 2)
