import json,time,random
import numpy as np
from shape.streaming import NumericEvidence,TextEvidence,KeyedState
from shape.profile.text_vectorized import profile_text_array,semantic_detect_array
from shape.connectors import KafkaBatchAdapter,EventHubsBatchAdapter
out={"passed":[],"failed":[]}
def ck(n,f):
 try:f();out["passed"].append(n)
 except Exception as e:out["failed"].append({"name":n,"error":repr(e)})
def evidence():
 a=NumericEvidence();b=NumericEvidence()
 for x in range(5000):a.update(x)
 for x in range(5000,10000):b.update(x)
 a.merge(b);s=a.summary();assert s["count"]==10000 and 4990<s["mean"]<5010 and s["q50"] is not None
 t=TextEvidence()
 for i in range(10000):t.update("cat"+str(i%100))
 assert t.summary()["distinct_estimate"]>90
ck("bounded_mergeable_numeric_text_evidence",evidence)
def keyed():
 s=KeyedState(10,100)
 for i in range(1000):s.put(i,i,float(i))
 assert len(s)<=100
 snap=s.snapshot();r=KeyedState.restore(snap);assert len(r)==len(s)
ck("bounded_keyed_state_ttl_capacity_restore",keyed)
def text():
 a=np.asarray(["user%d@example.com"%i for i in range(100000)])
 p=profile_text_array(a);d=semantic_detect_array(a);assert p["count"]==100000 and any(x[0]=="email" for x in d)
ck("vector_text_semantic",text)
def adapters():
 dec=lambda b:json.loads(b.decode() if isinstance(b,bytes) else b)
 payload=[json.dumps({"id":i,"v":i%10}).encode() for i in range(1000)]
 for A,meth in ((KafkaBatchAdapter,"decode_messages"),(EventHubsBatchAdapter,"decode_events")):
  a=A(dec);c=getattr(a,meth)(payload);assert len(c["id"])==1000 and c["id"][999]==999
ck("connector_decode_contract_equivalence",adapters)
print(json.dumps(out))
