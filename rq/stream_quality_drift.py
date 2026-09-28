import numpy as np,time,json,resource
from shape.capture import capture_columns
from shape.drift import compare
N=10000000;B=250000
ref=capture_columns({"id":np.arange(B,dtype=np.int64),"value":np.arange(B,dtype=np.float64)%1000,"category":np.arange(B,dtype=np.int64)%100})
rows=0;alerts=0;t=time.perf_counter()
for start in range(0,N,B):
 n=min(B,N-start);offset=500 if start>=N//2 else 0
 cur=capture_columns({"id":np.arange(start,start+n,dtype=np.int64),"value":(np.arange(start,start+n,dtype=np.float64)%1000)+offset,"category":np.arange(start,start+n,dtype=np.int64)%100})
 d=compare(ref,cur);alerts+=sum(x.score>.1 for x in d);rows+=n
e=time.perf_counter()-t
print(json.dumps({"rows":rows,"seconds":e,"rows_per_second":rows/e,"drift_alert_metrics":alerts,"maxrss_kb":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
