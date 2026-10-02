import sys, time, tempfile, shutil, threading, collections
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation.domains import load_domain, domain_names
from shape.generation.engine import Engine
from shape.generation import output
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks"); domain_names()
from shape.kernel.dispatch import get_kernel; get_kernel()
from shape.builtins.sinks import files
T=time.perf_counter
ev=[]
orig=Engine.generate_chunk
def gc(self, table, row_start, n_rows, **kw):
    t0=T(); r=orig(self, table, row_start, n_rows, **kw); ev.append(("gen",threading.get_ident(),t0,T(),f"{table}[{row_start}:{row_start+n_rows}]")); return r
Engine.generate_chunk=gc
class Proxy:
    def __init__(s,w,name): s.w=w; s.name=name
    def write_batch(s,b): t0=T(); s.w.write_batch(b); ev.append(("wb",threading.get_ident(),t0,T(),s.name))
    def finish(s): t0=T(); s.w.finish(); ev.append(("fin",threading.get_ident(),t0,T(),s.name))
    def wait(s): t0=T(); s.w.wait(); ev.append(("wait",threading.get_ident(),t0,T(),s.name))
    def close(s): s.w.close()
oon=files.ParquetSink.open_native
def on(self,target,schema,options):
    t0=T(); w=oon(self,target,schema,options); ev.append(("open",threading.get_ident(),t0,T(),str(target).split("/")[-1]))
    return Proxy(w,str(target).split("/")[-1]) if w is not None else None
files.ParquetSink.open_native=on
d=sys.argv[1]; scale=sys.argv[2] if len(sys.argv)>2 else "medium"
tA=T(); loaded=load_domain(d); tB=T(); e=Engine(loaded.schema, scale=scale, seed=1042); tC=T()
out=tempfile.mkdtemp(); output.write_engine(e,"parquet",out); tD=T(); shutil.rmtree(out)
print(f"load {1e3*(tB-tA):.1f} engine {1e3*(tC-tB):.1f} write_engine {1e3*(tD-tC):.1f} total {1e3*(tD-tA):.1f} ms")
t0=tC
tids={t:i for i,t in enumerate(sorted({e_[1] for e_ in ev}))}
gen_end=max(x[3] for x in ev if x[0]=="gen"); 
print(f"first gen starts at {1e3*(min(x[2] for x in ev if x[0]=='gen')-t0):.1f} ms, last gen ends {1e3*(gen_end-t0):.1f} ms, write_engine returns {1e3*(tD-t0):.1f}")
waits=[x for x in ev if x[0]=="wait"]; print("wait total %.1f ms, from %.1f to %.1f"%(1e3*sum(x[3]-x[2] for x in waits),1e3*(min(x[2] for x in waits)-t0),1e3*(max(x[3] for x in waits)-t0)))
busy=collections.defaultdict(float)
for k,tid,a,b,lab in ev:
    if k=="gen": busy[tids[tid]]+=b-a
print("gen busy per thread ms:", {k:round(1e3*v,1) for k,v in busy.items()})
print("sum gen %.1f ms; sum open %.1f; wb %.1f; fin %.1f"%tuple(1e3*sum(x[3]-x[2] for x in ev if x[0]==k) for k in ("gen","open","wb","fin")))
# ascii timeline per thread
W=100; span=tD-t0
rows=collections.defaultdict(lambda:[" "]*W)
for k,tid,a,b,lab in ev:
    if k!="gen": continue
    i0=int(W*(a-t0)/span); i1=max(i0+1,int(W*(b-t0)/span))
    for i in range(max(0,i0),min(W,i1)): rows[tids[tid]][i]="#"
for tix in sorted(rows): print(f"T{tix} |"+"".join(rows[tix])+"|")
print("    0"+" "*(W-12)+f"{1e3*span:.0f} ms")
print("--- per table: first chunk start, last chunk end (ms from engine start), chunks, rows")
import re
byt=collections.defaultdict(list)
for k,tid,a,b,lab in ev:
    if k=="gen":
        t,rng=lab.split("["); r0,r1=rng[:-1].split(":"); byt[t].append((a,b,int(r1)-int(r0),tids[tid]))
for t,l in sorted(byt.items(), key=lambda kv: min(x[0] for x in kv[1])):
    print(f"{t:22s} {1e3*(min(x[0] for x in l)-t0):6.1f} -> {1e3*(max(x[1] for x in l)-t0):6.1f}  chunks={len(l)} rows={sum(x[2] for x in l)} threads={sorted({x[3] for x in l})}")
