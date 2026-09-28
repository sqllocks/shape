import numpy as np,time,json
from shape.profile.text_vectorized import profile_text_semantic
N=1000000;a=np.asarray(["user%d@example.com"%(i%100000) for i in range(N)])
profile_text_semantic(a)
rates=[];last=None
for _ in range(3):
 t=time.perf_counter();last=profile_text_semantic(a);e=time.perf_counter()-t;rates.append(N/e)
print(json.dumps({"rows":N,"runs_rps":rates,"median_rows_per_second":sorted(rates)[len(rates)//2],"semantic":last[1],"distinct":last[0]["distinct_estimate"]}))
