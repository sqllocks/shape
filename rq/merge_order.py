import json
from shape.profile.numeric import NumericProfile
vals=[((i*7919)%10003)/10 for i in range(5000)];parts=[]
for j in range(5):
 p=NumericProfile();p.update(vals[j*1000:(j+1)*1000]);parts.append(p)
def m(order):
 x=NumericProfile()
 for i in order:x.merge(parts[i])
 return x.summary()
a=m(range(5));b=m(reversed(range(5)))
print(json.dumps({"mean_delta":abs(a["mean"]-b["mean"]),"distinct_delta":abs(a["distinct_estimate"]-b["distinct_estimate"]),"q50_delta":abs(a["q50"]-b["q50"])}))
