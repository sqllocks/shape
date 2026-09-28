import time,json,resource
from shape.capture import capture_rows
def rows(n):
 for i in range(n):yield {"id":i,"x":(i%1000)/10,"cat":f"c{i%100}","nullable":None if i%10==0 else i%7}
for n in (10000,50000):
 t=time.perf_counter();s=capture_rows(rows(n));e=time.perf_counter()-t
 print(json.dumps({"rows":n,"seconds":e,"rows_per_second":n/e,"maxrss_kb":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
