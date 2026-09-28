import json,random,time
from shape.connectors.qualification import *
N=1_000_000
# 8 partitions, at-least-once replay, duplicates and deliberately stale offsets.
records=[]
for i in range(N):
 p=str(i%8);off=i//8
 records.append(ConnectorRecord(p,off,i,str(i)))
# add replay tail + duplicate IDs at newer offsets
records.extend(records[-10000:])
for j in range(10000):
 i=j;records.append(ConnectorRecord(str(i%8),N//8+j,i,str(i)))
seen=[];q=ExactlyOnceProjector();t=time.perf_counter()
r=q.process(records,seen.append);elapsed=time.perf_counter()-t
assert len(seen)==N
assert len(set(seen))==N
assert all(q.store.committed(str(p))>=N//8 for p in range(8))
# reconnect semantics
attempt=[0]
def connect():
 attempt[0]+=1
 if attempt[0]<4:raise OSError("broker unavailable")
 return iter([1,2,3])
assert list(reconnecting_batches(connect,5))==[1,2,3]
print(json.dumps({"input_deliveries":len(records),"unique_projected":len(seen),"seconds":elapsed,"deliveries_per_second":len(records)/elapsed,"duplicates_or_stale":r["duplicates"]+r["stale"],"partitions":8,"reconnect_attempts":attempt[0]}))
