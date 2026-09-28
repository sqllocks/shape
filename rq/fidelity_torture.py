import json,random
from shape.capture import capture_rows
from shape.generation import GenerationPlan,SequenceStrategy,Choice,Uniform,Normal,certify
results=[]
# Identity/categorical plan: deliberately assess what the current certificate sees.
plan=GenerationPlan((("id",SequenceStrategy()),("segment",Choice(("A","B","C"),(.7,.2,.1))),("value",Uniform(0,100))),seed=42)
reference=capture_rows(plan.rows(20000)).to_dict()
cert=certify(reference,plan.rows(20000),tolerance=.05)
results.append({"case":"same_plan_same_seed","passed":cert.passed,"score":cert.score,"failed":[m.path for m in cert.metrics if not m.passed]})
cert2=certify(reference,GenerationPlan((("id",SequenceStrategy()),("segment",Choice(("A","B","C"),(.1,.2,.7))),("value",Uniform(0,100))),seed=99).rows(20000),tolerance=.05)
results.append({"case":"changed_categorical_distribution","passed":cert2.passed,"score":cert2.score,"failed":[m.path for m in cert2.metrics if not m.passed]})
print(json.dumps(results))
