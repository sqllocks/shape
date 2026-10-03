import sys
from pathlib import Path
import pyarrow.parquet as pq
a, b = Path(sys.argv[1]), Path(sys.argv[2])
fa = sorted(p.relative_to(a) for p in a.rglob("*.parquet")); fb = sorted(p.relative_to(b) for p in b.rglob("*.parquet"))
assert fa == fb, (len(fa), len(fb))
bad = [str(r) for r in fa if not pq.read_table(a / r).equals(pq.read_table(b / r))]
print(len(fa), "files compared;", len(bad), "differ", bad)
sys.exit(1 if bad else 0)
