import json,random,math
from shape.capture import capture_rows
from shape.profile.advanced import pearson,missingness_dependency
from shape.generation import certify,relational_fidelity,geographic_fidelity
out={}
# Conditional dependency: y strongly depends on category.
rng=random.Random(7)
ref=[{"cat":"A" if i%2==0 else "B","y":rng.gauss(0 if i%2==0 else 100,3)} for i in range(10000)]
bad=[{"cat":"A" if i%2==0 else "B","y":rng.gauss(50,3)} for i in range(10000)]
c=certify(capture_rows(ref).to_dict(),bad,tolerance=.1)
out["conditional_dependency"]={"certificate_passed":c.passed,"detected_by_current_scalar_certificate":not c.passed,
 "note":"marginals can detect this construction, but certificate has no explicit P(y|cat) metric"}
# nonlinear y=x^2 where Pearson can be near zero
ref=[{"x":(i-5000)/1000,"y":((i-5000)/1000)**2} for i in range(10000)]
perm=[dict(r) for r in ref];ys=[r["y"] for r in perm];random.Random(1).shuffle(ys)
for r,y in zip(perm,ys):r["y"]=y
out["nonlinear_dependency"]={"pearson_reference":pearson(ref,"x","y"),"pearson_destroyed":pearson(perm,"x","y"),
 "note":"Pearson alone cannot represent this dependency; nonlinear dependency evidence required for Platinum"}
# missingness dependency
ref=[{"a":None if i%3==0 else i,"b":None if i%3==0 else i*2} for i in range(3000)]
bad=[{"a":None if i%3==0 else i,"b":None if i%5==0 else i*2} for i in range(3000)]
out["missingness_dependency"]={"reference":missingness_dependency(ref,"a","b"),"destroyed":missingness_dependency(bad,"a","b")}
# relational
parents=[{"id":i} for i in range(1000)];good=[{"pid":i%1000} for i in range(10000)];badrel=good+[{"pid":999999}]
out["relational"]={"good":relational_fidelity(parents,good,"id","pid").passed,"bad":relational_fidelity(parents,badrel,"id","pid").passed}
# geography
geo=geographic_fidelity({"OH":.6,"PA":.4},[{"state":"OH"}]*900+[{"state":"PA"}]*100,tolerance=.1)
out["geography"]={"passed":geo.passed,"total_variation":geo.total_variation}
# temporal: same marginals, destroyed order
series=[{"v":i//100} for i in range(10000)]
shuf=[r["v"] for r in series];random.Random(2).shuffle(shuf)
def lag1(vals):
 m=sum(vals)/len(vals);num=sum((a-m)*(b-m) for a,b in zip(vals,vals[1:]));den=sum((a-m)**2 for a in vals);return num/den
out["temporal"]={"reference_lag1":lag1([r["v"] for r in series]),"destroyed_lag1":lag1(shuf),"note":"temporal autocorrelation needs explicit Platinum evidence"}
print(json.dumps(out,default=str))
