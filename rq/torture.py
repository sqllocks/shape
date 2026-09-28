import json,math,random
from shape.capture import capture_rows
CASES={}
CASES["all_null"]=[{"x":None} for _ in range(1000)]
CASES["high_cardinality"]=[{"x":f"id-{i:08d}"} for i in range(10000)]
CASES["skew"]=[{"x":0 if i<9900 else i} for i in range(10000)]
CASES["unicode"]=[{"x":("😀漢字e\u0301"*10)+str(i%7)} for i in range(2000)]
CASES["nonfinite"]=[{"x":v} for v in ([float("nan"),float("inf"),float("-inf"),1.0]*1000)]
CASES["late_column"]=[{"a":i} if i<999 else {"a":i,"b":"late"} for i in range(1000)]
out={}
for name,rows in CASES.items():
 try:
  s=capture_rows(rows).to_dict()
  out[name]={"passed":True,"rows":s["rows"],"columns":list(s["columns"])}
  if name=="nonfinite":out[name]["counts"]={k:s["columns"]["x"].get(k) for k in ("nan_count","pos_inf_count","neg_inf_count","finite_count")}
  if name=="late_column":out[name]["b_nulls"]=s["columns"]["b"]["null_count"]
 except Exception as e:out[name]={"passed":False,"error":repr(e)}
print(json.dumps(out,ensure_ascii=False))
