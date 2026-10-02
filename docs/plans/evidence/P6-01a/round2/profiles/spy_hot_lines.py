"""Share of py-spy --native samples (inside Engine._generate) by the innermost Shape source line."""
import collections, re, sys
c = collections.Counter(); tot = 0
for line in open(sys.argv[1]):
    stack, _, n = line.rstrip("\n").rpartition(" "); n = int(n)
    if "_generate (shape/generation/engine.py" not in stack:
        continue
    tot += n
    found = [m for f in stack.split(";") if (m := re.match(r"(\S+) \((shape/[^)]*\.py:\d+)\)$", f))]
    c[f"{found[-1].group(2)}  {found[-1].group(1)}" if found else "(no shape frame)"] += n
print(f"top Shape source lines by share of generation samples ({tot} samples)")
for k, v in c.most_common(int(sys.argv[2]) if len(sys.argv) > 2 else 18):
    print(f"{100 * v / tot:5.1f}%  {k}")
