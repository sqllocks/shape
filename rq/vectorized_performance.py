import numpy as np,time,json,resource
from shape.capture import capture_columns
for n in (100000,1000000):
 cols={"id":np.arange(n,dtype=np.int64),"x":np.arange(n,dtype=np.float64)%1000,"nullable":np.arange(n,dtype=np.int64)%7}
 t=time.perf_counter();s=capture_columns(cols);e=time.perf_counter()-t
 print(json.dumps({"rows":n,"columns":len(cols),"seconds":e,"rows_per_second":n/e,"scalar_values_per_second":n*len(cols)/e,"maxrss_kb":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
