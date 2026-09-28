from time import perf_counter
import resource
from shape.generation import GenerationPlan,SequenceStrategy,Choice
from shape.capture import capture_rows
N=250_000
p=GenerationPlan((("id",SequenceStrategy()),("segment",Choice(("A","B","C"),(.7,.2,.1)))),seed=2026)
t=perf_counter();rows=list(p.rows(N));g=perf_counter()-t
t=perf_counter();s=capture_rows(rows,batch_size=10000);c=perf_counter()-t
rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(f"rows={N} generation_rps={N/g:.0f} capture_rps={N/c:.0f} maxrss_kb={rss} columns={len(s.columns)}")
