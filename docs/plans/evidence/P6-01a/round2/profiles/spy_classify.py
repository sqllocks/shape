"""Classify py-spy --native raw samples (one per line: 'frame;frame;... count') by the library or
file of the LEAF frame: where the CPU is at the sampled instant."""
import collections, re, sys
tot = 0
by = collections.Counter()
for line in open(sys.argv[1]):
    line = line.rstrip("\n")
    if not line:
        continue
    stack, _, n = line.rpartition(" ")
    n = int(n)
    if len(sys.argv) > 2 and sys.argv[2] not in stack:  # e.g. "engine.py" frames of generate()
        continue
    leaf = stack.split(";")[-1]
    m = re.search(r"\(([^)]*)\)\s*$", leaf)
    where = m.group(1) if m else leaf
    where = where.split(":")[0]
    base = where.rsplit("/", 1)[-1]
    if base.endswith(".py"):
        key = "python code (.py frames)"
    elif "_kernel" in base:
        key = "Rust kernel (shape._kernel)"
    elif base.startswith("libarrow") or "pyarrow" in where:
        key = "Arrow C++ / pyarrow"
    elif "numpy" in where or base.startswith("libscipy_openblas") or "_multiarray" in base:
        key = "numpy C"
    elif base.startswith("python") or base.startswith("libpython"):
        key = "CPython interpreter"
    elif base.startswith("libc") or base.startswith("ld-") or base.startswith("libm") or base.startswith("libpthread"):
        key = "libc / malloc / kernel entry"
    else:
        key = "other: " + base
    by[key] += n
    tot += n
for k, v in by.most_common():
    if v / tot >= 0.005:
        print(f"{100 * v / tot:5.1f}%  {v:6d}  {k}")
print("samples", tot)
