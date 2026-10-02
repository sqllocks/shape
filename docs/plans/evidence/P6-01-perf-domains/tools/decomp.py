"""Per domain (fresh process each): wall of the product path, when generation ends, encode CPU (serial), generate CPU (1 thread)."""

import json
import subprocess
import sys

doms = "capital_markets education financial healthcare hr insurance iot manufacturing marketing pulse real_estate supply_chain telecom retail".split()
code = r"""
import sys, time, tempfile, threading, json, os
import numpy, pyarrow, pyarrow.parquet as pq
pyarrow.array(["w"])
from shape.generation.domains import load_domain
from shape.generation import engine as E, output as O
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks")
from shape.kernel.dispatch import get_kernel; get_kernel()
T=time.perf_counter; ev=[]
orig=E.Engine.generate_chunk
def gc(self,*a,**k):
    t=T(); r=orig(self,*a,**k); ev.append(("gen",t,T())); return r
E.Engine.generate_chunk=gc
ow=O._LazySink.write
def w(self,*a,**k):
    t=T(); r=ow(self,*a,**k); ev.append(("write",t,T())); return r
O._LazySink.write=w
d, scale = sys.argv[1], sys.argv[2]
t0=T(); e=E.Engine(load_domain(d).schema, scale=scale, seed=1042)
O.write_engine(e,"parquet",tempfile.mkdtemp()); t1=T()
gen_end=max(x[2] for x in ev if x[0]=="gen"); first_write=min(x[1] for x in ev if x[0]=="write"); last_write=max(x[2] for x in ev if x[0]=="write")
wcpu=sum(x[2]-x[1] for x in ev if x[0]=="write")
print(json.dumps({"wall":t1-t0,"gen_end":gen_end-t0,"first_write":first_write-t0,"write_sum":wcpu}))
"""
rows = []
for d in doms:
    rs = [
        json.loads(
            subprocess.run(
                [sys.executable, "-c", code, d, sys.argv[1]], capture_output=True, text=True
            )
            .stdout.strip()
            .splitlines()[-1]
        )
        for _ in range(3)
    ]
    rs.sort(key=lambda r: r["wall"])
    r = rs[1]
    print(
        f"{d:16s} wall {1e3 * r['wall']:6.0f} ms | generation ends {1e3 * r['gen_end']:6.0f} | tail after gen {1e3 * (r['wall'] - r['gen_end']):5.0f} ({100 * (r['wall'] - r['gen_end']) / r['wall']:3.0f}%) | sum of write calls {1e3 * r['write_sum']:6.0f}"
    )
