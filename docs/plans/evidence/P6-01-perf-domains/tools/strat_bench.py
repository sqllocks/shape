import time

from shape.generation.domains import load_domain
from shape.generation.engine import Engine

T = time.perf_counter
# Find a representative column per strategy in telecom's usage_record and time it through the engine, warm.
import sys

d, table = sys.argv[1], sys.argv[2]
e = Engine(load_domain(d).schema, scale="medium", seed=1042)
for lvl in e.levels:
    if table in lvl:
        break
    for t in lvl:
        e.generate_table(t)
n = int(sys.argv[3]) if len(sys.argv) > 3 else 200_000
n = min(n, e.row_counts[table])
tdef = e.schema.tables[table]
from shape.generation.engine import order_columns


def run_cols():
    built = {}
    res = {}
    for cname in order_columns(tdef):
        col = tdef.columns[cname]
        t = T()
        produced = e._column(table, col, 0, 0, n, built)
        dt = T() - t
        built[cname] = e._as_array(produced, "x", n) if not isinstance(produced, dict) else None
        res[cname] = dt
    return res


run_cols()
best = {}
for _ in range(5):
    for k, v in run_cols().items():
        best[k] = min(best.get(k, 1e9), v)
for k, v in sorted(best.items(), key=lambda kv: -kv[1]):
    print(
        f"{1e9 * v / n:6.1f} ns/row  {table}.{k} [{tdef.columns[k].strategy}] {tdef.columns[k].generator.get('distribution') or tdef.columns[k].generator.get('pattern') or tdef.columns[k].generator.get('provider') or ''}"
    )
