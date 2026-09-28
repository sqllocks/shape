import numpy as np,time,json,resource
from shape.streaming import VectorStreamProfiler
N=50000000;B=250000
p=VectorStreamProfiler();t=time.perf_counter()
for start in range(0,N,B):
 n=min(B,N-start)
 cols={"id":np.arange(start,start+n,dtype=np.int64),"value":np.arange(start,start+n,dtype=np.float64)%1000,"category":np.arange(start,start+n,dtype=np.int64)%100}
 p.add_batch(cols)
e=time.perf_counter()-t
print(json.dumps({"rows":p.rows,"batches":p.batches,"seconds":e,"rows_per_second":p.rows/e,"batch_rows":B,"maxrss_kb":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
