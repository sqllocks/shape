import json,tempfile,random
from datetime import datetime,timedelta,timezone
from pathlib import Path
from shape.streaming import *
out={"passed":[],"failed":[],"findings":[]}
def ck(n,f):
 try:f();out["passed"].append(n)
 except Exception as e:out["failed"].append({"name":n,"error":repr(e)})
def windows():
 w=TumblingWindow(timedelta(seconds=10),timedelta(seconds=5));base=datetime(2026,1,1,tzinfo=timezone.utc)
 assert w.add(base+timedelta(seconds=20),20)
 assert w.add(base+timedelta(seconds=18),18)
 assert not w.add(base+timedelta(seconds=1),1);assert w.late_dropped==1
 w.add(base+timedelta(seconds=40),40);ready=w.close_ready();assert ready
ck("late_out_of_order_watermark",windows)
def failure():
 with tempfile.TemporaryDirectory() as d:
  s=FileCheckpointStore(Path(d)/"cp.json");seen=[]
  try:replay_with_failures(range(100),seen.append,s,fail_after=37)
  except RuntimeError:pass
  before=len(seen);replay_with_failures(range(100),seen.append,s)
  assert seen==list(range(100)) and before==37
ck("crash_checkpoint_replay_no_loss_no_duplicate",failure)
def corrupt():
 with tempfile.TemporaryDirectory() as d:
  p=Path(d)/"cp";p.write_text("{bad")
  try:FileCheckpointStore(p).load();raise AssertionError("corrupt accepted")
  except Exception:pass
ck("corrupt_checkpoint_rejected",corrupt)
def bounded():
 o=OnlineShape(1000)
 for i in range(100000):o.add({"x":i})
 assert len(o.buffer)==1000 and o.total==100000
ck("bounded_online_memory_semantics",bounded)
def dedupe():
 import numpy as np
 seen=set();a=np.array([1,2,2,3]);k=deduplicate_ids(a,seen);assert a[k].tolist()==[1,2,3]
 b=np.array([2,3,4]);k=deduplicate_ids(b,seen);assert b[k].tolist()==[4]
ck("duplicate_suppression_reference",dedupe)
print(json.dumps(out))
