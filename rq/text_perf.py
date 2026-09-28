import numpy as np,time,json
from shape.profile.text_vectorized import profile_text_array,semantic_detect_array
N=1000000;a=np.asarray(["user%d@example.com"%(i%100000) for i in range(N)])
t=time.perf_counter();p=profile_text_array(a);s=semantic_detect_array(a);e=time.perf_counter()-t
print(json.dumps({"rows":N,"seconds":e,"rows_per_second":N/e,"semantic":s,"distinct":p["distinct_estimate"]}))
