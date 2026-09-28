import time,json,resource,hashlib
from ga_generation_qualification import generate
def run(total,chunk=1_000_000):
 t=time.perf_counter();done=0;dig=hashlib.sha256()
 while done<total:
  n=min(chunk,total-done);r=generate(n,7+done//chunk);dig.update(r["digest"].encode());done+=n
 e=time.perf_counter()-t;return {"rows":total,"chunk":chunk,"seconds":e,"rows_per_second":total/e,"maxrss_kb":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"digest":dig.hexdigest()}
out={}
for n in (10_000_000,):
 out[str(n)]=run(n)
print(json.dumps(out))
