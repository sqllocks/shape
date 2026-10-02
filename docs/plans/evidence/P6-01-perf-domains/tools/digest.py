"""sha256 of every generated table, all domains, given scales and modes. usage: digest.py out.json scale[,scale]"""

import hashlib
import json
import sys

import pyarrow as pa

from shape.generation.domains import domain_names, load_domain
from shape.generation.engine import Engine

out, scales = sys.argv[1], sys.argv[2].split(",")
res = {}
for d in domain_names():
    for mode in ("3nf", "star"):
        try:
            sch = load_domain(d, mode=mode).schema
        except Exception:
            continue
        for sc in scales:
            r = Engine(sch, scale=sc, seed=1042).generate()
            for name, t in r.tables.items():
                t = t.combine_chunks()
                sink = pa.BufferOutputStream()
                with pa.ipc.new_stream(sink, t.schema) as w:
                    w.write_table(t)
                res[f"{d}/{mode}/{sc}/{name}"] = hashlib.sha256(
                    sink.getvalue().to_pybytes()
                ).hexdigest()
json.dump(res, open(out, "w"), indent=0, sort_keys=True)
print(len(res), "tables hashed")
