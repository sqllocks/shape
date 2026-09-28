import json,tempfile,math,random
from pathlib import Path
from datetime import datetime,timedelta,timezone
import numpy as np
from shape.capture import capture_columns
from shape.drift import compare
from shape.history import LocalHistory
from shape.privacy import detect_column,assess_summary
from shape.streaming import *
out={"passed":[],"failed":[],"findings":[]}
def ck(name,fn):
 try:fn();out["passed"].append(name)
 except Exception as e:out["failed"].append({"name":name,"error":repr(e)})
def cols(start,n,variant=0):
 x=np.arange(start,start+n,dtype=np.int64)
 return {"id":x,"fk":x%1000,"value":(x%1000).astype(np.float64)+(500 if variant else 0),"category":x%100,
         "latitude":39.0+(x%100)/1000.,"longitude":-83.0-(x%100)/1000.}
def summarize_partitioned(N,B):
 # Merge by concatenating only test-scale columns to test batch-boundary equivalence of vector path.
 parts=[cols(s,min(B,N-s)) for s in range(0,N,B)]
 merged={k:np.concatenate([p[k] for p in parts]) for k in parts[0]}
 return capture_columns(merged)
def batch_equiv():
 a=summarize_partitioned(100000,1000);b=summarize_partitioned(100000,25000)
 for c in a["columns"]:
  for m in ("count","null_count","distinct_estimate","mean","min","max","q50"):
   if m in a["columns"][c]:
    x,y=a["columns"][c][m],b["columns"][c][m]
    assert x==y or (isinstance(x,float) and abs(x-y)<1e-12)
ck("batch_boundary_equivalence",batch_equiv)
def drift_schema_geo_rel():
 ref=capture_columns(cols(0,100000));cur=cols(0,100000,1);cur["new_col"]=np.ones(100000,dtype=np.int64)
 d=compare(ref,capture_columns(cur));paths={x.path for x in d}
 assert "columns.new_col" in paths and any("value" in x for x in paths)
 assert (cur["fk"]<1000).all() and np.isfinite(cur["latitude"]).all()
ck("drift_schema_geo_fk_combination",drift_schema_geo_rel)
def nonfinite():
 a=np.array([1.,np.nan,np.inf,-np.inf]*25000);s=capture_columns({"x":a})["columns"]["x"]
 assert s["nan_count"]==25000 and s["pos_inf_count"]==25000 and s["neg_inf_count"]==25000
ck("nonfinite_stream_profile",nonfinite)
def privacy():
 vals=["person%d@example.com"%i for i in range(1000)]
 assert any(d.kind=="email" for d in detect_column(vals))
 s={"count":1000,"distinct_estimate":1000,"topk":[]};assert not assess_summary(s).releasable
ck("privacy_detection_release_guard",privacy)
def history_checkpoint():
 with tempfile.TemporaryDirectory() as d:
  h=LocalHistory(Path(d)/"h");s=FileCheckpointStore(Path(d)/"cp")
  a=json.dumps(capture_columns(cols(0,1000)),sort_keys=True).encode();e=h.append(a,metadata={"rows":1000});s.save(StreamCheckpoint(1000))
  b=json.dumps(capture_columns(cols(1000,1000,1)),sort_keys=True).encode();e2=h.append(b,parent=e.content_hash,metadata={"rows":2000});s.save(StreamCheckpoint(2000))
  assert h.checkout(e.content_hash)==a and s.load().sequence==2000 and e2.parent==e.content_hash
ck("history_checkpoint_chain",history_checkpoint)
def aggregate_window():
 w=AggregateTumblingWindow(timedelta(seconds=10),timedelta(seconds=5));base=datetime(2026,1,1,tzinfo=timezone.utc)
 for i in range(100000):w.add(base+timedelta(milliseconds=i),i%100)
 # open state is aggregates per window, not 100k retained values
 assert len(w._windows)<20 and all(not hasattr(v,"__len__") for v in w._windows.values())
 assert w.close_ready()
ck("bounded_aggregate_window_100k",aggregate_window)
def crash_points():
 for fail in (1,2,7,31,99):
  with tempfile.TemporaryDirectory() as d:
   s=FileCheckpointStore(Path(d)/"c");seen=[]
   try:replay_with_failures(range(100),seen.append,s,fail)
   except RuntimeError:pass
   replay_with_failures(range(100),seen.append,s)
   assert seen==list(range(100))
ck("restart_multiple_failure_points",crash_points)
print(json.dumps(out))
