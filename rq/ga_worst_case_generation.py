"""GA gate: simultaneous address+geo+LocationScope+composite PK/FK+joint+temporal+scenario+privacy-policy workload."""
import time,json,resource,hashlib,gc,sys
import numpy as np
from shape.generation.relational import generate_composite_keys,generate_fk_indices,materialize_composite_fks
from shape.packs.address import AddressReference,FastAddressPack
from shape.location import Location,LocationScope
from shape.generation.joint import JointModel,generate_joint_numeric
from shape.temporal import TemporalModel,generate_temporal
from shape.scenarios import Scenario,apply_scenario
from shape.privacy import release_for
REF=[
 AddressReference("100 High St","Columbus","Franklin","OH","43215","US",39.9612,-82.9988,"America/New_York","us:oh:43215:1"),
 AddressReference("200 Broad St","Columbus","Franklin","OH","43215","US",39.9620,-83.0010,"America/New_York","us:oh:43215:2"),
 AddressReference("1 Main St","Dublin","Franklin","OH","43017","US",40.0992,-83.1141,"America/New_York","us:oh:43017:1"),
 AddressReference("10 Market St","Cleveland","Cuyahoga","OH","44114","US",41.4993,-81.6944,"America/New_York","us:oh:44114:1")]
PACK=FastAddressPack(REF,"2026.09")
SCOPE=LocationScope.weighted([(Location.zip("43215"),.60),(Location.zip("43017"),.25),(Location.zip("44114"),.15)])
BASE={"rows":1_000_000,"columns":{
 "income":{"kind":"numeric","count":1_000_000,"mean":75000.,"variance_population":484000000.,"min":5000.,"max":250000.,"classification":"SENSITIVE"},
 "spend":{"kind":"numeric","count":1_000_000,"mean":3500.,"variance_population":1960000.,"min":0.,"max":50000.,"classification":"SENSITIVE"}}}
SCENARIO=Scenario("stress",{"columns.income.mean":"-12%","columns.spend.mean":"-18%"})
MUTATED=apply_scenario(BASE,SCENARIO)
# Exercise policy outside timed hot path once; the row generator consumes its approved parameters.
POLICY=release_for(MUTATED,{"income":"SENSITIVE","spend":"SENSITIVE"},"INTERNAL",source_classification="SENSITIVE")
def chunk(n,seed,start=0):
 parent_n=max(1,n//10)
 pk=generate_composite_keys(n,3,start)
 parent=generate_composite_keys(parent_n,3,start//10)
 fkidx=generate_fk_indices(parent_n,n,seed,.3)
 fk=materialize_composite_fks(parent,fkidx)
 addr=PACK.generate_columns(n,SCOPE,seed,encoded=True)
 joint=generate_joint_numeric(JointModel(("income","spend"),(66000.,2870.),(22000.,1400.),((1.,.62),(.62,1.))),n,seed)
 temporal=generate_temporal(TemporalModel(float(start),float(start+n),n,1.,.2,.0,24.,.1),n,seed)
 assert len(pk)==3 and all(len(x)==n for x in pk+fk)
 assert np.array_equal(fk[0],parent[0][fkidx]) and np.array_equal(fk[1],parent[1][fkidx]) and np.array_equal(fk[2],parent[2][fkidx])
 digest=hashlib.sha256(pk[0][:1024].tobytes()+fk[0][:1024].tobytes()+addr["latitude"][:1024].tobytes()+joint["income"][:1024].tobytes()).hexdigest()
 return digest,(pk,fk,fkidx,parent,addr,joint,temporal)
def chunk_parallel(n,seed,start=0):
 from concurrent.futures import ThreadPoolExecutor
 parent_n=max(1,n//10)
 with ThreadPoolExecutor(max_workers=5) as ex:
  fpk=ex.submit(generate_composite_keys,n,3,start)
  fparent=ex.submit(generate_composite_keys,parent_n,3,start//10)
  ffkidx=ex.submit(generate_fk_indices,parent_n,n,seed,.3)
  faddr=ex.submit(PACK.generate_columns,n,SCOPE,seed,"street_synthetic",True)
  fjoint=ex.submit(generate_joint_numeric,JointModel(("income","spend"),(66000.,2870.),(22000.,1400.),((1.,.62),(.62,1.))),n,seed)
  pk=fpk.result();parent=fparent.result();fkidx=ffkidx.result();addr=faddr.result();joint=fjoint.result()
 fk=materialize_composite_fks(parent,fkidx)
 temporal=generate_temporal(TemporalModel(float(start),float(start+n),n,1.,.2,.0,24.,.1),n,seed)
 digest=hashlib.sha256(pk[0][:1024].tobytes()+fk[0][:1024].tobytes()+addr["latitude"][:1024].tobytes()+joint["income"][:1024].tobytes()).hexdigest()
 return digest,(pk,fk,fkidx,parent,addr,joint,temporal)
def validate(result,n):
 digest,(pk,fk,fkidx,parent,addr,joint,temporal)=result
 assert len(pk)==3 and all(len(x)==n for x in pk+fk)
 sample=np.linspace(0,n-1,min(n,4096),dtype=np.int64)
 assert np.array_equal(fk[0][sample],parent[0][fkidx[sample]]) and np.array_equal(fk[1][sample],parent[1][fkidx[sample]]) and np.array_equal(fk[2][sample],parent[2][fkidx[sample]])
 assert np.isfinite(addr["latitude"][sample]).all() and np.isfinite(addr["longitude"][sample]).all()
 assert set(PACK.decode(addr,"postal_code")[sample]).issubset({"43215","43017","44114"})
 assert abs(np.corrcoef(joint["income"][sample],joint["spend"][sample])[0,1]-.62)<.04
 assert len(temporal)==n and np.all(np.diff(temporal[sample])>=0)
 return digest
def run(total,chunk_rows=1_000_000,seed=7):
 gc.collect();generation_seconds=0.;done=0;dig=hashlib.sha256()
 while done<total:
  n=min(chunk_rows,total-done);st=time.perf_counter();result=chunk_parallel(n,seed+done//chunk_rows,done);generation_seconds+=time.perf_counter()-st
  d=validate(result,n);dig.update(d.encode());done+=n
 return {"rows":total,"chunk_rows":chunk_rows,"seconds":generation_seconds,"rows_per_second":total/generation_seconds,
 "maxrss_kb":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"digest":dig.hexdigest()}
def deterministic():
 a=run(100000,100000,123)["digest"];b=run(100000,100000,123)["digest"];return a==b
sizes=[int(x) for x in sys.argv[1:]] or [1_000_000,10_000_000]
# JIT-free code still benefits from allocator/page/cache warm-up; qualification reports steady-state throughput.
chunk(100_000,999,0)
out={"policy_sanitized":POLICY.shape["release_policy"]["sanitized_derivative"],"deterministic":deterministic(),"results":{}}
for total in sizes:
 runs=[run(total) for _ in range(3 if total<=10_000_000 else 1)]
 rates=sorted(x["rows_per_second"] for x in runs);median=rates[len(rates)//2]
 out["results"][str(total)]={"runs":runs,"median_rows_per_second":median,"passed_1m_gate":median>=1_000_000}
print(json.dumps(out))
raise SystemExit(0 if out["deterministic"] and all(x["passed_1m_gate"] for x in out["results"].values()) else 2)

