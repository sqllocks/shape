import json, time, sys
import shape
p = shape.load("/root/bench-out/p408/d2.shape")
t0=time.time()
r = shape.generate(p, n=int(sys.argv[1]) if len(sys.argv)>1 else 200000, seed=5)
print("generated", time.time()-t0)
t = r.tables["d2"]
print(t.schema)
q = shape.profile(t, name="d2").to_dict()
o = p.to_dict()
for n, c in o["columns"].items():
    d = q["columns"][n]
    def f(k): return (c[k], d[k])
    bad = []
    for k in ("dtype","is_enum","is_primary_key","pattern"):
        if c[k]!=d[k]: bad.append((k,c[k],d[k]))
    for k in ("null_rate","mean","std"):
        a,b=c[k],d[k]
        if a is None and b is None: continue
        if a is None or b is None or abs(a-b)>0.02*max(abs(a),1e-9)+ (0.003 if k=="null_rate" else 0): bad.append((k,a,b))
    if c["distribution"]!=d["distribution"]: bad.append(("dist",c["distribution"],d["distribution"]))
    print(n, "OK" if not bad else bad)
