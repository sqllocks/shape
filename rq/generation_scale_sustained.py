import numpy as np,time,json,resource
from shape.generation import *
records=[{"city":f"City{i%100}","state":"OH","county":f"County{i%20}","postal_code":f"{43000+i%900:05d}","latitude":39+(i%100)/100,"longitude":-84+(i%100)/100} for i in range(1000)]
asset=CompiledAddressAsset.from_records(records,np.asarray([f"Street {i}" for i in range(5000)],dtype="U20"),"bench-v1")
N=10000000;B=250000
for mode in ("address","combined"):
 total=0;t=time.perf_counter()
 for start in range(0,N,B):
  n=min(B,N-start)
  if mode=="address":a=generate_addresses(asset,n,seed=start)
  else:k=composite_keys(n,start);fk=foreign_keys(n,1000000,seed=start);a=generate_addresses(asset,n,seed=start+1)
  total+=n
 elapsed=time.perf_counter()-t
 print(json.dumps({"mode":mode,"rows":total,"seconds":elapsed,"rows_per_second":total/elapsed,"batch_rows":B,"maxrss_kb":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
