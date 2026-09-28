import json,random,math
from shape.profile.sketches import HyperLogLog,KLL
out={"hll":[],"kll":[]}
for seed in range(20):
 rng=random.Random(seed)
 n=10000;h=HyperLogLog()
 vals=set()
 for _ in range(n):
  x=rng.randrange(20000);vals.add(x);h.update(x)
 out["hll"].append(abs(h.estimate()-len(vals))/max(len(vals),1))
 vals=[rng.random() for _ in range(n)];k=KLL()
 for x in vals:k.update(x)
 true=sorted(vals)[n//2];out["kll"].append(abs(k.quantile(.5)-true))
print(json.dumps({"hll_mean_rel_error":sum(out["hll"])/len(out["hll"]),"hll_max_rel_error":max(out["hll"]),"kll_mean_abs_median_error":sum(out["kll"])/len(out["kll"]),"kll_max_abs_median_error":max(out["kll"])}))
