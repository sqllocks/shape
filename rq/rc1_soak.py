import json,time,resource
from shape.streaming import PartitionedKeyedState,NumericEvidence,TextEvidence
N=2000000;s=PartitionedKeyedState(32,10000,500);num=NumericEvidence();txt=TextEvidence();t=time.perf_counter()
for i in range(N):
 s.put(i%50000,i,float(i))
 num.update(i%1000)
 if i%10==0:txt.update("cat"+str(i%1000))
elapsed=time.perf_counter()-t
print(json.dumps({"events":N,"seconds":elapsed,"events_per_second":N/elapsed,"keys":len(s),
"max_allowed_keys":16000,"bounded":len(s)<=16000,"numeric_count":num.count,"text_count":txt.count,
"maxrss_kb":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
