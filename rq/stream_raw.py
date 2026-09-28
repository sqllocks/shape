import numpy as np,time,json
N=100000000;B=250000;rows=0;t=time.perf_counter()
for start in range(0,N,B):
 n=min(B,N-start);batch={"id":np.arange(start,start+n,dtype=np.int64),"v":np.arange(start,start+n,dtype=np.float64)};rows+=n
e=time.perf_counter()-t
print(json.dumps({"rows":rows,"seconds":e,"rows_per_second":rows/e}))
