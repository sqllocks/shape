import numpy as np,random,json,math
from shape.capture import capture_columns
from shape.drift import compare
out={"trials":0,"failures":[]}
for seed in range(100):
 rng=random.Random(seed);N=rng.randint(1000,10000);B=rng.choice([1,7,31,128,997,4096])
 x=np.arange(N,dtype=np.int64);full=capture_columns({"id":x,"v":(x%97).astype(np.float64),"fk":x%23})
 # reconstruct identical logical stream from arbitrary boundaries
 chunks=[];s=0
 while s<N:
  n=min(rng.randint(1,B),N-s);chunks.append(np.arange(s,s+n,dtype=np.int64));s+=n
 y=np.concatenate(chunks);got=capture_columns({"id":y,"v":(y%97).astype(np.float64),"fk":y%23})
 try:
  assert y.tolist()==x.tolist()
  for c in full["columns"]:
   for m in ("count","distinct_estimate","mean","min","max","q50"):
    a,b=full["columns"][c].get(m),got["columns"][c].get(m)
    assert a==b or (isinstance(a,float) and abs(a-b)<1e-12)
 except Exception as e:out["failures"].append({"seed":seed,"batch":B,"error":repr(e)})
 out["trials"]+=1
print(json.dumps(out))
raise SystemExit(1 if out["failures"] else 0)
