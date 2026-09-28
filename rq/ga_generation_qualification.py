import time,json,resource,tempfile,hashlib
import numpy as np
from shape.generation.relational import generate_composite_keys,generate_fk_indices,materialize_composite_fks
from shape.packs.address import AddressReference,FastAddressPack
from shape.location import Location,LocationScope
from shape.generation.joint import JointModel,generate_joint_numeric
from shape.temporal import TemporalModel,generate_temporal
from shape.scenarios import Scenario,apply_scenario
REF=[
 AddressReference("100 High St","Columbus","Franklin","OH","43215","US",39.9612,-82.9988,"America/New_York","us:oh:43215:1"),
 AddressReference("200 Broad St","Columbus","Franklin","OH","43215","US",39.9620,-83.0010,"America/New_York","us:oh:43215:2"),
 AddressReference("1 Main St","Dublin","Franklin","OH","43017","US",40.0992,-83.1141,"America/New_York","us:oh:43017:1"),
 AddressReference("10 Market St","Cleveland","Cuyahoga","OH","44114","US",41.4993,-81.6944,"America/New_York","us:oh:44114:1")]
PACK=FastAddressPack(REF,"2026.09")
SCOPE=LocationScope.weighted([(Location.zip("43215"),.6),(Location.zip("43017"),.25),(Location.zip("44114"),.15)])
def generate(n,seed=7):
 t0=time.perf_counter()
 pk=generate_composite_keys(n,2)
 fkidx=generate_fk_indices(max(1,n//10),n,seed,.3)
 parent=generate_composite_keys(max(1,n//10),2)
 fk=materialize_composite_fks(parent,fkidx)
 addr=PACK.generate_columns(n,SCOPE,seed,encoded=True)
 joint=generate_joint_numeric(JointModel(("income","spend"),(75000.,3500.),(22000.,1400.),((1.,.62),(.62,1.))),n,seed)
 temporal=generate_temporal(TemporalModel(0.,float(n),n,1.,.2,.0,24.,.1),n,seed)
 elapsed=time.perf_counter()-t0
 # Correctness probes across the entire vectorized result.
 assert len(pk[0])==n and len(fk[0])==n and len(addr["latitude"])==n and len(joint["income"])==n and len(temporal)==n
 assert np.all(fkidx>=0) and np.all(fkidx<len(parent[0]))
 assert np.array_equal(fk[0],parent[0][fkidx]) and np.array_equal(fk[1],parent[1][fkidx])
 assert set(PACK.decode(addr,"postal_code")[:min(n,100000)]).issubset({"43215","43017","44114"})
 assert np.isfinite(addr["latitude"]).all() and np.isfinite(addr["longitude"]).all()
 assert abs(np.corrcoef(joint["income"],joint["spend"])[0,1]-.62)<.02
 return {"rows":n,"seconds":elapsed,"rows_per_second":n/elapsed,"maxrss_kb":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
 "digest":hashlib.sha256(pk[0][:1000].tobytes()+fk[0][:1000].tobytes()+addr["latitude"][:1000].tobytes()).hexdigest()}
def deterministic(n=100000):
 a=generate(n,123);b=generate(n,123);return a["digest"]==b["digest"]
out={"deterministic":deterministic()}
for n in (1_000_000,10_000_000):
 runs=[generate(n) for _ in range(3)]
 rates=sorted(x["rows_per_second"] for x in runs)
 out[str(n)]={"runs":runs,"median_rows_per_second":rates[1],"passed_1m_gate":rates[1]>=1_000_000}
print(json.dumps(out))
