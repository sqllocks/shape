import time, numpy as np, pyarrow as pa, pyarrow.compute as pc, pyarrow.parquet as pq, os
FIRST=pa.array([f"First{i}" for i in range(5000)]); LAST=pa.array([f"Last{i}" for i in range(20000)])
CITY=pa.array([f"City{i}" for i in range(30000)]); STATES=pa.array(["OH","NY","CA","TX","FL","WA"])
def customers(n,rng):
    ids=np.arange(1,n+1)
    fn=FIRST.take(rng.integers(0,len(FIRST),n)); ln=LAST.take(rng.integers(0,len(LAST),n))
    email=pc.binary_join_element_wise(pc.utf8_lower(fn),".",pc.utf8_lower(ln),pc.cast(pa.array(ids),pa.string()),"")
    email=pc.binary_join_element_wise(email,"@example.com","")
    signup=pa.array((np.datetime64("2018-01-01")+rng.integers(0,2500,n)).astype("datetime64[D]"))
    seg=pa.array(rng.choice(np.array(["gold","silver","bronze"]),n,p=[.1,.3,.6]))
    return pa.table({"customer_id":ids,"first":fn,"last":ln,"email":email,"city":CITY.take(rng.integers(0,len(CITY),n)),"state":STATES.take(rng.integers(0,6,n)),"segment":seg,"signup":signup,"income":np.round(rng.lognormal(10.8,.5,n),2)})
def orders(n,ncust,rng):
    # Pareto-skewed FK to customers
    cust=(np.minimum(rng.pareto(1.2,n),50)/50*ncust).astype(np.int64)%ncust+1
    ts=(np.datetime64("2023-01-01T00:00:00")+rng.integers(0,525600,n).astype("timedelta64[m]")).astype("datetime64[s]")
    return pa.table({"order_id":np.arange(1,n+1),"customer_id":cust,"ts":pa.array(ts),"amount":np.round(rng.lognormal(3.5,1.0,n),2),"status":pa.array(rng.choice(np.array(["placed","shipped","returned"]),n,p=[.2,.75,.05])),"qty":rng.poisson(2,n)+1})
rng=np.random.default_rng(42); NC,NO=200_000,1_800_000
best=1e9
for _ in range(3):
    t=time.perf_counter()
    for i in range(0,NC,100_000): pq.write_table(customers(100_000,rng),f"./gc{i}.parquet")
    for i in range(0,NO,250_000): pq.write_table(orders(250_000,NC,rng),f"./go{i}.parquet")
    best=min(best,time.perf_counter()-t)
print("gen %d rows in %.2fs -> %.0fk rows/s (to Parquet, single core)"%(NC+NO,best,(NC+NO)/best/1e3))
