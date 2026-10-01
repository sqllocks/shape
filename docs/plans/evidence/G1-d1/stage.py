# one fresh process: stage split of shape.profile(d1.csv), imports excluded like bench.py
import os, sys, time
from pathlib import Path
import shape, shape.api, shape.profile.reference.profile, sys as _s
P = _s.modules["shape.profile.reference.profile"]
from shape.kernel.dispatch import get_kernel
get_kernel()
from shape.profile.reference import sources, table, column
import numpy as np
if os.environ.get("SWI"): sys.setswitchinterval(float(os.environ["SWI"]))
path = str(Path(os.environ["BENCH_DATA_DIR"]) / "profile" / (sys.argv[1] if len(sys.argv)>1 else "d1.csv"))
T = {}
def wrap(mod, name, label):
    f = getattr(mod, name)
    def g(*a, **k):
        t=time.perf_counter(); r=f(*a, **k); T[label]=T.get(label,0)+time.perf_counter()-t; return r
    setattr(mod, name, g)
wrap(P, "load_columns", "load")
wrap(P, "_profile_cols_table", "profile_tbl")
wrap(table, "_profile_cols", "cols_pool")
wrap(P, "table_to_dict", "to_dict")
wrap(table, "_correlation", "corr")
cp = column._profile_column
percol = {}
def pc_(c, *a, **k):
    t=time.perf_counter(); r=cp(c,*a,**k); percol[c.name]=time.perf_counter()-t; return r
table._profile_column = pc_
import resource as _r
_a=_r.getrusage(_r.RUSAGE_SELF)
t0=time.perf_counter(); p = shape.profile(path); tot=time.perf_counter()-t0
_b=_r.getrusage(_r.RUSAGE_SELF); print("timed region user=%.3f sys=%.3f minflt=%d"%(_b.ru_utime-_a.ru_utime,_b.ru_stime-_a.ru_stime,_b.ru_minflt-_a.ru_minflt))
print(f"total {tot:.3f}", {k: round(v,3) for k,v in T.items()}, {k: round(v,3) for k,v in percol.items()})
