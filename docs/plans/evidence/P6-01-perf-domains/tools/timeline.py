import sys
import tempfile
import threading
import time

domain, scale = sys.argv[1], sys.argv[2]
import pyarrow

pyarrow.array(["w"])
from shape.generation import engine as E
from shape.generation import output as O
from shape.generation.domains import load_domain
from shape.plugins.host import default_host

h = default_host()
h.load_all("shape.strategies")
h.load_all("shape.sinks")
from shape.kernel.dispatch import get_kernel

get_kernel()
T = time.perf_counter
ev = []


def wrap(obj, name, label):
    orig = getattr(obj, name)

    def f(*a, **k):
        t = T()
        r = orig(*a, **k)
        ev.append((t, T(), threading.current_thread().name, label(a, k)))
        return r

    setattr(obj, name, f)


wrap(E.Engine, "generate_chunk", lambda a, k: f"gen {a[1]}[{a[2]}+{a[3]}]")
wrap(E, "apply_compute_phase", lambda a, k: "compute_phase")
wrap(E, "fix_rule", lambda a, k: "fix_rule")
wrap(E, "validate_rules", lambda a, k: "validate_rules")
wrap(O._LazySink, "write", lambda a, k: f"write {a[1]}")
t0 = T()
_l = load_domain(domain)
import os

eng = E.Engine(
    _l.schema,
    scale=scale,
    seed=1042,
    row_counts=({t: 1 for t in _l.schema.tables} if os.environ.get("FLOOR") else None),
)
O.write_engine(eng, "parquet", tempfile.mkdtemp())
t1 = T()
print(f"total {1e3 * (t1 - t0):.1f} ms")
for s, e_, th, l in sorted(ev):
    print(
        f"{1e3 * (s - t0):7.1f} -> {1e3 * (e_ - t0):7.1f}  ({1e3 * (e_ - s):6.1f})  {th[-12:]:>12}  {l}"
    )
