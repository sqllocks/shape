import json, os, statistics, subprocess, sys, tempfile
dom=sys.argv[1]; configs=json.loads(sys.argv[2]); n=int(sys.argv[3])
py=os.environ["SHAPE_VENV"]+"/bin/python"; gen=os.environ.get("GENPY","benchmarks/vs_spindle/domain_1to1/generate.py")
res={k:[] for k in configs}
for i in range(n):
    for name,env in configs.items():
        e=dict(os.environ); e.update(env); e["BENCH_OUT_DIR"]=tempfile.mkdtemp()
        r=subprocess.run([py,gen,"--impl","shape","--domain",dom,"--scale","medium","--seed","1042"] if gen.endswith("generate.py") else [py,gen,dom],capture_output=True,text=True,env=e)
        line=[l for l in r.stdout.splitlines() if l.startswith("GEN_JSON ")][-1]
        res[name].append(json.loads(line[9:])["total_s"]*1e3)
for k,v in res.items(): print(f"{dom:14s} {k:14s} median {statistics.median(v):6.1f}  min {min(v):6.1f}  q1 {sorted(v)[len(v)//4]:6.1f}")
