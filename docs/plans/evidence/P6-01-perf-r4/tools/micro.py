import time, numpy as np, pyarrow as pa
from shape.generation import arrowkit
T=time.perf_counter
def tm(f,n=20000):
    f(); t=T()
    for _ in range(n): f()
    return 1e6*(T()-t)/n
for n in (10, 5000):
    a=np.arange(n,dtype=np.int64); fl=np.random.rand(n)
    print(n, "arrowkit.array int64: %.2f us"%tm(lambda: arrowkit.array(a)), " pa.array: %.2f us"%tm(lambda: pa.array(a)),
      " from_buffers direct: %.2f us"%tm(lambda: pa.Array.from_buffers(pa.int64(), n, [None, pa.py_buffer(a)])))
    arr=arrowkit.array(a)
    print(n, "to_numpy arrowkit: %.2f us"%tm(lambda: arrowkit.to_numpy(arr)), " arr.to_numpy: %.2f us"%tm(lambda: arr.to_numpy(zero_copy_only=False)), "frombuffer direct %.2f"%tm(lambda: np.frombuffer(arr.buffers()[1], dtype=np.int64, count=n)))
    print(n,"pc.take %.2f us"%tm(lambda: pa.compute.take(arr, arr)) if hasattr(pa,'compute') else "", end="")
    import pyarrow.compute as pc
    print(" pc.take %.2f  arr.take %.2f  numpy fancy+array %.2f"%(tm(lambda: pc.take(arr,arr)), tm(lambda: arr.take(arr)), tm(lambda: arrowkit.array(a[a%max(n,1)]))))
print("from_numpy_dtype %.2f us"%tm(lambda: pa.from_numpy_dtype(np.dtype("int64"))))
print("np.diff %.2f vs subtract %.2f"%(tm(lambda: np.diff(a:=np.arange(11,dtype=np.int32)).max()), tm(lambda: (a[1:]-a[:-1]).max())))
print("cast: pc.cast %.2f, arr.cast %.2f"%(tm(lambda: pc.cast(arr, pa.float64())), tm(lambda: arr.cast(pa.float64()))))
import hashlib
print("blake2b key %.2f us"%tm(lambda: int.from_bytes(hashlib.blake2b(b"1042\x00hr\x00employee\x00v",digest_size=16).digest(),"little")))
