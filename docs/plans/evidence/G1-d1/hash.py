import hashlib, sys, os, json
from pathlib import Path
import shape
D = Path(os.environ["BENCH_DATA_DIR"]) / "profile"
out = {}
for ds in ["d1.csv","d1.parquet","d2.csv","d2.parquet","d4.csv","d4.parquet","mt","d3.csv","d3.parquet"]:
    if ds == "mt":
        p = shape.profile({f.stem: str(f) for f in sorted((D/"mt").glob("*.csv"))})
    else:
        p = shape.profile(str(D/ds))
    out[ds] = hashlib.sha256(json.dumps(p.to_dict(), sort_keys=False, default=repr, allow_nan=True).encode()).hexdigest()[:16]
    print(ds, out[ds], flush=True)
