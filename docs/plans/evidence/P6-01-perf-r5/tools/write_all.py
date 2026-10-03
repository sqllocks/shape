"""Write every domain (3nf, star; given scales) through write_engine as Parquet into OUT/<d>_<mode>_<scale>/."""
import sys
from pathlib import Path
from shape.generation.domains import domain_names, load_domain
from shape.generation.engine import Engine
from shape.generation.output import write_engine
out, scales = Path(sys.argv[1]), sys.argv[2].split(",")
n = 0
for d in domain_names():
    for mode in ("3nf", "star"):
        try:
            sch = load_domain(d, mode=mode).schema
        except Exception:
            continue
        for sc in scales:
            n += len(write_engine(Engine(sch, scale=sc, seed=1042), "parquet", out / f"{d}_{mode}_{sc}"))
print(n, "files")
