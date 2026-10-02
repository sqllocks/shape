import os, sys, time, tempfile, shutil, json
import numpy, pyarrow
pyarrow.array(["w"])
from shape.generation import engine as eng
if os.environ.get("RELAX")=="1":
    orig=eng._dependency_graph
    def relaxed(schema):
        g=orig(schema)
        for child, deps in list(g.items()):
            tdef=schema.tables[child]
            for parent in list(deps):
                pt=schema.tables.get(parent)
                if pt is None or len(pt.primary_key)!=1: continue
                pk=pt.columns.get(pt.primary_key[0])
                if pk is None or pk.strategy!="sequence" or (pk.nullable and pk.null_rate): continue
                ok=True; refs=0
                for col in tdef.columns.values():
                    g_=col.generator; blob=json.dumps(g_)
                    if f'"{parent}.' in blob or f'"{parent}"' in blob:
                        refs+=1
                        if not (col.strategy=="foreign_key" and g_.get("ref")==f"{parent}.{pt.primary_key[0]}" and not g_.get("constrained_by") and g_.get("sample_rate") is None):
                            ok=False
                if ok and refs and not any(r.parent==parent and r.child==child for r in schema.relationships):
                    deps.discard(parent)
        return g
    eng._dependency_graph=relaxed
from shape.generation.domains import load_domain, domain_names
from shape.generation.output import write_engine
from shape.plugins.host import default_host
h = default_host(); h.load_all("shape.strategies"); h.load_all("shape.sinks"); domain_names()
from shape.kernel.dispatch import get_kernel; get_kernel()
T=time.perf_counter
t0=T(); loaded=load_domain(sys.argv[1]); e=eng.Engine(loaded.schema, scale="medium", seed=1042)
out=tempfile.mkdtemp(); write_engine(e,"parquet",out); t1=T(); shutil.rmtree(out)
print("GEN_JSON "+json.dumps({"total_s":t1-t0}))
