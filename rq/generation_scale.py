import numpy as np,time,json,resource
from shape.generation import *
records=[]
# synthetic compiled geography reference; benchmark measures generation, not external resolution/geocoding.
for i in range(1000):
 records.append({"city":f"City{i%100}","state":"OH","county":f"County{i%20}","postal_code":f"{43000+i%900:05d}",
 "latitude":39.0+(i%100)/100,"longitude":-84.0+(i%100)/100})
asset=CompiledAddressAsset.from_records(records,np.asarray([f"Street {i}" for i in range(5000)],dtype="U20"),"bench-v1")
for n in (100000,1000000):
 results={}
 t=time.perf_counter();k=composite_keys(n);results["composite_keys_rps"]=n/(time.perf_counter()-t)
 t=time.perf_counter();fk=foreign_keys(n,1000000,42);results["foreign_keys_rps"]=n/(time.perf_counter()-t)
 t=time.perf_counter();a=generate_addresses(asset,n,42);results["addresses_rps"]=n/(time.perf_counter()-t)
 # Combined realistic columnar record: composite keys + FK + coherent address components.
 t=time.perf_counter();k=composite_keys(n);fk=foreign_keys(n,1000000,42);a=generate_addresses(asset,n,43);results["combined_rps"]=n/(time.perf_counter()-t)
 results.update(rows=n,maxrss_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
 print(json.dumps(results))
