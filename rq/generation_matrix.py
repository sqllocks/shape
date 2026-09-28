import json,random,math,itertools
from datetime import datetime,timedelta
from shape.generation import *
from shape.capture import capture_rows
out={"passed":[],"failed":[],"roadblocks":[]}
def check(name,fn):
 try: fn();out["passed"].append(name)
 except Exception as e:out["failed"].append({"name":name,"error":repr(e)})
# every scalar strategy
check("constant",lambda: (_ for _ in ()).throw(AssertionError()) if list(GenerationPlan((("x",Constant("a")),),1).rows(3))!=[{"x":"a"}]*3 else None)
check("sequence",lambda: (_ for _ in ()).throw(AssertionError()) if [r["x"] for r in GenerationPlan((("x",SequenceStrategy(10,2)),)).rows(3)]!=[10,12,14] else None)
check("choice_weighted",lambda: (_ for _ in ()).throw(AssertionError()) if set(r["x"] for r in GenerationPlan((("x",Choice(("a","b"),(1,0))),),1).rows(100))!={"a"} else None)
check("uniform",lambda: all(0<=r["x"]<=1 for r in GenerationPlan((("x",Uniform(0,1)),),1).rows(100)) or (_ for _ in ()).throw(AssertionError()))
check("normal",lambda: len(list(GenerationPlan((("x",Normal(0,1)),),1).rows(100)))==100 or (_ for _ in ()).throw(AssertionError()))
check("derived_conditional",lambda: [r["z"] for r in GenerationPlan((("x",SequenceStrategy()),("z",Conditional(lambda c:c["x"]%2==0,Constant("E"),Constant("O")))),1).rows(4)]==["O","E","O","E"] or (_ for _ in ()).throw(AssertionError()))
check("foreign_key",lambda: set(r["fk"] for r in GenerationPlan((("fk",ForeignKey((1,2,3))),),1).rows(100))<={1,2,3} or (_ for _ in ()).throw(AssertionError()))
check("empirical",lambda: set(r["x"] for r in GenerationPlan((("x",Empirical(("a","b"),(.5,1.0))),),1).rows(100))<={"a","b"} or (_ for _ in ()).throw(AssertionError()))
check("unique_token",lambda: len({r["x"] for r in GenerationPlan((("x",UniqueToken("U",6)),),1).rows(1000)})==1000 or (_ for _ in ()).throw(AssertionError()))
def corr():
 rows=list(GenerationPlan((("x",Normal(0,1)),("y",CorrelatedNormal("x",0,1,0,1,.8))),7).rows(10000))
 xs=[r["x"] for r in rows];ys=[r["y"] for r in rows]
 mx=sum(xs)/len(xs);my=sum(ys)/len(ys)
 num=sum((r["x"]-mx)*(r["y"]-my) for r in rows);den=(sum((r["x"]-mx)**2 for r in rows)*sum((r["y"]-my)**2 for r in rows))**.5
 assert num/den>.75
check("correlated_normal",corr)
# deterministic random access
def deterministic():
 p=GenerationPlan((("x",Normal(0,1)),("y",Choice(("a","b")))),123)
 assert p.row_at(999)==p.row_at(999)
 assert list(p.rows_at([5,2,9]))==[p.row_at(5),p.row_at(2),p.row_at(9)]
check("deterministic_random_access_stateless",deterministic)
# Stateful strategy: expose random-access inconsistency
def first_parent():
 p=GenerationPlan((("parent",Choice((1,1,2))),("flag",FirstPerParent("parent","first","later"))),4)
 rows=list(p.rows(20));assert sum(r["flag"]=="first" for r in rows)==len(set(r["parent"] for r in rows))
check("first_per_parent_sequential",first_parent)
# relational / SCD
def rel():
 parents=[{"id":i} for i in range(100)]
 children=list(generate_children(parents,ParentChildSpec("id","pid",1,3),5))
 assert all(c["pid"] in range(100) for c in children)
 assert relational_fidelity(parents,children,"id","pid").passed
check("parent_child",rel)
def scd():
 x=list(scd2_versions("a",datetime(2020,1,1),3,timedelta(days=1)));assert x[-1]["is_current"] and x[-1]["valid_to"] is None
check("scd2",scd)
# vector keys
def vk():
 import numpy as np
 k=composite_keys(100000);pairs=set(zip(k["entity_id"].tolist(),k["partition_id"].tolist()));assert len(pairs)==100000
 f=foreign_keys(100000,1000,1);assert f.min()>=0 and f.max()<1000
 p,o=parent_child_keys(1000,5);assert len(p)==5000 and len(o)==5000
check("vector_keys",vk)
# addresses + coherence + deterministic
def addr():
 import numpy as np
 rec=[{"city":"A","state":"OH","county":"X","postal_code":"43000","latitude":40.0,"longitude":-83.0},{"city":"B","state":"PA","county":"Y","postal_code":"15000","latitude":41.0,"longitude":-80.0}]
 a=CompiledAddressAsset.from_records(rec,["One","Two"])
 x=generate_addresses(a,100000,9);y=generate_addresses(a,100000,9)
 assert all((x[k]==y[k]).all() for k in x)
 allowed={(r["city"],r["state"],r["county"],r["postal_code"],r["latitude"],r["longitude"]) for r in rec}
 assert set(zip(x["city"],x["state"],x["county"],x["postal_code"],x["latitude"],x["longitude"]))<=allowed
check("addresses_coherent_deterministic",addr)
# combinations of base stateless strategies in a plan
factories=[
 ("seq",lambda:SequenceStrategy()),("choice",lambda:Choice(("a","b"))),("uniform",lambda:Uniform(0,1)),
 ("normal",lambda:Normal(0,1)),("unique",lambda:UniqueToken("x"))
]
for (an,af),(bn,bf),(cn,cf) in itertools.combinations(factories,3):
 check("combo_"+an+"_"+bn+"_"+cn,lambda af=af,bf=bf,cf=cf: len(list(GenerationPlan((("a",af()),("b",bf()),("c",cf())),11).rows(1000)))==1000 or (_ for _ in ()).throw(AssertionError()))
# expected roadblocks explicitly probed
# start parameter address partition determinism
try:
 import numpy as np
 rec=[{"city":"A","state":"OH","county":"X","postal_code":"43000","latitude":40.0,"longitude":-83.0}]
 a=CompiledAddressAsset.from_records(rec,["One","Two"])
 whole=generate_addresses(a,2000,10,start=0);p1=generate_addresses(a,1000,10,start=0);p2=generate_addresses(a,1000,10,start=1000)
 if all((whole[k][:1000]==p1[k]).all() and (whole[k][1000:]==p2[k]).all() for k in whole):
  out["passed"].append("address_partition_determinism")
 else:out["roadblocks"].append("address `start` is ignored; independently generated partitions do not reproduce a whole batch")
except Exception as e:out["failed"].append({"name":"address_partition_probe","error":repr(e)})
# weighted address assets
try:
 import numpy as np
 rec=[{"city":"A","state":"OH","county":"X","postal_code":"1","latitude":1.0,"longitude":1.0},{"city":"B","state":"OH","county":"X","postal_code":"2","latitude":2.0,"longitude":2.0}]
 a=CompiledAddressAsset.from_records(rec,["S"]);object.__setattr__(a,"weights",np.array([.99,.01]))
 x=generate_addresses(a,10000,1);ratio=(x["city"]=="A").mean()
 if ratio>.9:out["passed"].append("address_weights")
 else:out["roadblocks"].append(f"CompiledAddressAsset.weights exists but generation ignores it (observed A ratio {ratio:.3f})")
except Exception as e:out["failed"].append({"name":"address_weight_probe","error":repr(e)})
print(json.dumps(out))
