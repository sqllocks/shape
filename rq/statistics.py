import json,random
from shape.capture import capture_rows
out=[]
for seed in range(5):
 rng=random.Random(seed);vals=[rng.gauss(10,3) for _ in range(5000)]
 s=capture_rows(({"x":x} for x in vals)).to_dict()["columns"]["x"]
 out.append({"mean_error":abs(s["mean"]-10),"q50_error":abs(s["q50"]-10)})
print(json.dumps(out))
