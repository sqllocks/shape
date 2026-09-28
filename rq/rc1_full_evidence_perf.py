import numpy as np,time,json,tempfile,resource
from pathlib import Path
from shape.streaming import FullEvidenceEngine,FileCheckpointStore,StreamCheckpoint
N=5000000;B=250000
x=np.arange(B,dtype=np.int64);v=(x%1000).astype(np.float64);lat=39+(x%100)/1000.;lon=-83-(x%100)/1000.;fk=x%1000
text=np.asarray(["segment%d"%(i%10000) for i in range(B)]);cols={"id":x,"value":v,"fk":fk,"latitude":lat,"longitude":lon}
rows=0;engine=FullEvidenceEngine(3)
with tempfile.TemporaryDirectory() as d:
 cp=FileCheckpointStore(Path(d)/"cp");t=time.perf_counter()
 for batch in range(N//B):
  evidence=engine.process(cols,text,parent_count=1000,dependency=("fk","id"))
  rows+=B;cp.save(StreamCheckpoint(rows,str(batch)))
 e=time.perf_counter()-t;checkpoint=cp.load().sequence
engine.close()
print(json.dumps({"rows":rows,"seconds":e,"rows_per_second":rows/e,"fixture_generation_excluded":True,
 "text_distinct":evidence["text"]["distinct_estimate"],"orphans":evidence["relational"].orphans,
 "geo_cells":len(evidence["geo"].cells),"dependency_mi":evidence["dependency"].summary()["mutual_information"],
 "out_of_order":evidence["temporal"].out_of_order,"checkpoint":checkpoint,
 "maxrss_kb":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
