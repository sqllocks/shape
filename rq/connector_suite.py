import json,numpy as np
from shape.connectors import KafkaBatchAdapter,EventHubsBatchAdapter
from shape.capture import capture_columns
from shape.drift import compare
N=100000
rows=[json.dumps({"id":i,"value":i%1000,"fk":i%100}).encode() for i in range(N)]
dec=lambda b:json.loads(b.decode() if isinstance(b,bytes) else b)
direct={"id":np.arange(N,dtype=np.int64),"value":np.arange(N,dtype=np.int64)%1000,"fk":np.arange(N,dtype=np.int64)%100}
ref=capture_columns(direct)
out={}
for name,a,m in [("kafka",KafkaBatchAdapter(dec),"decode_messages"),("eventhubs",EventHubsBatchAdapter(dec),"decode_events")]:
 cols=getattr(a,m)(rows);s=capture_columns(cols);d=compare(ref,s)
 out[name]={"rows":s["rows"],"max_drift":max((x.score for x in d),default=0),"live_sdk_available":a.live_available()}
print(json.dumps(out))
