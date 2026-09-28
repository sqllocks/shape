import json,numpy as np
from shape.capture import capture_columns
from shape.generation import generate_numeric,compare_numeric
N=100000
source=(np.arange(N)%1000).astype(float)/10
shape=capture_columns({"score":source})
target=shape["columns"]["score"]
generated=np.asarray(generate_numeric(target,N,seed=42),dtype=float)
observed=capture_columns({"score":generated})["columns"]["score"]
result=compare_numeric(target,observed)
print(json.dumps({"status":"completed","rows":N,"target":{"mean":target.get("mean"),"variance_population":target.get("variance_population")},
"observed":{"mean":observed.get("mean"),"variance_population":observed.get("variance_population")},"fidelity":result.dimensions}))
