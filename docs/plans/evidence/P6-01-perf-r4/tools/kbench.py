import time, numpy as np, pyarrow as pa, os
from shape.kernel.dispatch import get_kernel
K=get_kernel(); T=time.perf_counter
def tm(f,n=200):
    for _ in range(10): f()
    t=T()
    for _ in range(n): f()
    return (T()-t)/n
w=pa.array([5.0,3.0,2.0,1.0,0.5],type=pa.float64()); prob,alias=K.alias_build(w); pool=pa.array(["Credit Card","ACH","Check","Wire","Cash"])
for n in (5000, 16384, 32768, 65536, 262144):
    t1=tm(lambda: K.alias_pool(prob,alias,pool,1,2,0,n))
    t2=tm(lambda: K.alias_sample(prob,alias,1,2,0,n))
    t3=tm(lambda: K.philox_normal(1,2,0,n))
    t4=tm(lambda: K.uniform_index(1,2,0,n,1000))
    t5=tm(lambda: K.philox_words(1,2,0,n,2))
    print(f"n={n:7d}  alias_pool {1e9*t1/n:5.1f} ns/row  alias_sample {1e9*t2/n:5.1f}  philox_normal {1e9*t3/n:5.1f}  uniform_index {1e9*t4/n:5.1f}  words(2) {1e9*t5/n:5.1f}")
print("--- pool_take alone")
for n in (5000, 65536):
    idx=K.alias_sample(prob,alias,1,2,0,n)
    t=tm(lambda: K.pool_take(pool, idx))
    idx_py=pa.array(np.random.randint(0,5,n),type=pa.int64())
    t2=tm(lambda: K.pool_take(pool, idx_py))
    big=pa.array([f"name{i}" for i in range(5000)])
    idx_b=pa.array(np.random.randint(0,5000,n),type=pa.int64())
    t3=tm(lambda: K.pool_take(big, idx_b))
    print(n, f"pool_take(5 entries) {1e9*t/n:.1f} ns/row  random idx {1e9*t2/n:.1f}  5000-entry pool {1e9*t3/n:.1f}")
    # arrow take for comparison
    import pyarrow.compute as pc
    tt=tm(lambda: pc.take(pool, idx_py))
    print("   pyarrow take", f"{1e9*tt/n:.1f} ns/row")
