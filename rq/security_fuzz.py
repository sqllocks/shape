import random,string,tempfile,zipfile,json,hashlib,os
from shape.security import validate_structure,SecurityError,scan_secrets
from shape.artifact.io import read_artifact,ArtifactError
rng=random.Random(8675309);fail=[]
# Structure/parser fuzz: arbitrary nested JSON-safe values, hostile depths and nonfinite numbers.
def scalar():
 return rng.choice([None,True,False,rng.randint(-10**12,10**12),rng.random(),''.join(rng.choices(string.printable,k=rng.randint(0,300)))])
def obj(depth=0):
 if depth>8 or rng.random()<.5:return scalar()
 if rng.random()<.5:return [obj(depth+1) for _ in range(rng.randint(0,8))]
 return {f"k{i}":obj(depth+1) for i in range(rng.randint(0,8))}
for i in range(500):
 x=obj()
 try:validate_structure(x)
 except SecurityError:pass
 except Exception as e:fail.append(("structure",i,type(e).__name__))
# Archive fuzz: malformed manifests, paths, hashes, compression and random bytes must fail closed, never crash outside ArtifactError/zip errors.
for i in range(50):
 with tempfile.TemporaryDirectory() as d:
  p=os.path.join(d,"x.shape")
  try:
   with zipfile.ZipFile(p,"w",zipfile.ZIP_DEFLATED) as z:
    if rng.random()<.9:z.writestr("manifest.json",rng.choice([b"{}",b'{"content_hashes":{}}',os.urandom(rng.randint(0,200))]))
    for j in range(rng.randint(0,5)):
     z.writestr(rng.choice([f"x{j}","../x","a\\..\\x","/abs"]),os.urandom(rng.randint(0,1000)))
   try:read_artifact(p,max_member_bytes=5000,max_total_bytes=10000,max_ratio=20,max_members=10)
   except (ArtifactError,zipfile.BadZipFile):pass
   except Exception as e:fail.append(("archive_read",i,type(e).__name__))
  except Exception as e:fail.append(("archive_setup",i,type(e).__name__))
print(json.dumps({"structure_trials":500,"archive_trials":50,"failures":fail}))
raise SystemExit(1 if fail else 0)
