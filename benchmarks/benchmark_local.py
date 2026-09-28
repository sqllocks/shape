from time import perf_counter
from shape.generation.strategies import GenerationPlan,SequenceStrategy,Choice
from shape.profile.dependencies import candidate_key
N=200000
p=GenerationPlan((("id",SequenceStrategy()),("tier",Choice(("a","b","c"),(.7,.2,.1)))),seed=42)
t=perf_counter(); rows=list(p.rows(N)); gen=perf_counter()-t
t=perf_counter(); candidate_key(rows,("id",)); prof=perf_counter()-t
print(f"rows={N} generation_rows_per_sec={N/gen:.0f} key_profile_rows_per_sec={N/prof:.0f}")
