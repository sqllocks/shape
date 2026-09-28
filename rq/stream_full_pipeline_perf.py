import numpy as np,time,json,resource,tempfile
from pathlib import Path
from shape.capture import capture_columns
from shape.drift import compare
from shape.streaming import VectorStreamProfiler,FileCheckpointStore,StreamCheckpoint
N=10000000;B=250000
refcols={"id":np.arange(B,dtype=np.int64),"fk":np.arange(B,dtype=np.int64)%1000,
"value":np.arange(B,dtype=np.float64)%1000,"category":np.arange(B,dtype=np.int64)%100,
"latitude":39+(np.arange(B)%100)/1000.,"longitude":-83-(np.arange(B)%100)/1000.}
ref=capture_columns(refcols);rows=0;alerts=0;quality_fail=0
with tempfile.TemporaryDirectory() as d:
 cp=FileCheckpointStore(Path(d)/"cp");p=VectorStreamProfiler();t=time.perf_counter()
 for start in range(0,N,B):
  n=min(B,N-start);x=np.arange(start,start+n,dtype=np.int64);offset=500 if start>=N//2 else 0
  cols={"id":x,"fk":x%1000,"value":(x.astype(np.float64)%1000)+offset,"category":x%100,
        "latitude":39+(x%100)/1000.,"longitude":-83-(x%100)/1000.}
  shape=p.add_batch(cols);dft=compare(ref,shape);alerts+=sum(z.score>.1 for z in dft)
  # structural quality checks, vectorized
  quality_fail+=int(not ((cols["fk"]>=0).all() and (cols["fk"]<1000).all() and np.isfinite(cols["latitude"]).all() and np.isfinite(cols["longitude"]).all()))
  rows+=n;cp.save(StreamCheckpoint(rows,str(start)))
 e=time.perf_counter()-t
 print(json.dumps({"rows":rows,"columns":6,"seconds":e,"rows_per_second":rows/e,"drift_alert_metrics":alerts,
 "quality_fail_batches":quality_fail,"checkpoint_sequence":cp.load().sequence,"batch_rows":B,"maxrss_kb":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
