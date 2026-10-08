"""Hash the contents (values and null positions, never the bytes under a null) of every Parquet table."""
import sys, hashlib, glob, os
import pyarrow as pa, pyarrow.compute as pc, pyarrow.parquet as pq

def zero(t):
    if pa.types.is_string(t) or pa.types.is_large_string(t): return pa.scalar("", type=t)
    if pa.types.is_boolean(t): return pa.scalar(False, type=t)
    return pa.scalar(0, type=pa.int64()).cast(t)

def norm(col):
    arr = col.combine_chunks()
    if arr.null_count:
        valid = pc.is_valid(arr)
        arr = pa.chunked_array([pc.if_else(valid, arr, zero(arr.type))]).combine_chunks()
        return arr, valid
    return arr, None

out = {}
for f in sorted(glob.glob(os.path.join(sys.argv[1], "**/*.parquet"), recursive=True)):
    t = pq.read_table(f)
    h = hashlib.sha256()
    h.update(str(t.schema).encode())
    for name in t.column_names:
        arr, valid = norm(t[name])
        arr = pa.array(arr) if isinstance(arr, pa.ChunkedArray) else arr
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, pa.schema([pa.field(name, arr.type)])) as w:
            w.write_batch(pa.record_batch([arr], names=[name]))
        h.update(sink.getvalue().to_pybytes())
        if valid is not None:
            h.update(str(valid.to_numpy(zero_copy_only=False).tobytes()).encode()[:0] + valid.cast(pa.uint8()).to_numpy(zero_copy_only=False).tobytes())
    out[os.path.relpath(f, sys.argv[1])] = (t.num_rows, h.hexdigest()[:16])
for k, v in out.items(): print(k, *v)
