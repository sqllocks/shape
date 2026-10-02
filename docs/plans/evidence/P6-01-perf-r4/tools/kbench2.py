import time, numpy as np, pyarrow as pa
from shape._process import tune_malloc; tune_malloc()
from shape.kernel.dispatch import get_kernel
K=get_kernel(); T=time.perf_counter
def tm(f,n=300):
    for _ in range(20): f()
    t=T()
    for _ in range(n): f()
    return (T()-t)/n
w=pa.array([5.0,3.0,2.0,1.0,0.5],type=pa.float64()); prob,alias=K.alias_build(w); pool=pa.array(["Credit Card","ACH","Check","Wire","Cash"])
for n in (20000, 65536):
    fused=tm(lambda: K.alias_pool(prob,alias,pool,1,2,0,n))
    def two():
        idx=K.alias_sample(prob,alias,1,2,0,n); return K.pool_take(pool, idx)
    sep=tm(two)
    print(n, f"fused {1e9*fused/n:.1f} ns/row   separate {1e9*sep/n:.1f}")
