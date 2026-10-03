"""sha256 of every table as written by the product path (write_engine, Parquet) and read back,
all domains, given scales and modes. usage: digest_written.py out.json scale[,scale] workdir"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from shape.generation.domains import domain_names, load_domain
from shape.generation.engine import Engine
from shape.generation.output import write_engine

out, scales, work = sys.argv[1], sys.argv[2].split(","), Path(sys.argv[3])
res = {}
for d in domain_names():
    for mode in ("3nf", "star"):
        try:
            sch = load_domain(d, mode=mode).schema
        except Exception:
            continue
        for sc in scales:
            target = work / f"{d}_{mode}_{sc}"
            shutil.rmtree(target, ignore_errors=True)
            paths = write_engine(Engine(sch, scale=sc, seed=1042), "parquet", target)
            for p in paths:
                t = pq.read_table(p).combine_chunks()
                sink = pa.BufferOutputStream()
                with pa.ipc.new_stream(sink, t.schema) as w:
                    w.write_table(t)
                res[f"{d}/{mode}/{sc}/{Path(p).stem}"] = hashlib.sha256(
                    sink.getvalue().to_pybytes()
                ).hexdigest()
            shutil.rmtree(target, ignore_errors=True)
json.dump(res, open(out, "w"), indent=0, sort_keys=True)
print(len(res), "tables hashed")
