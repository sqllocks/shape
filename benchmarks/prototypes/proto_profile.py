import time, pyarrow as pa, pyarrow.csv as pcsv, pyarrow.compute as pc, numpy as np
def prof(tbl):
    out={}
    for name in tbl.column_names:
        c=tbl[name]; t=c.type; d={"type":str(t),"nulls":c.null_count}
        vc=pc.value_counts(c)   # exact distinct + top-k in one hash pass
        d["distinct"]=len(vc)
        cnt=vc.field("counts").to_numpy(); k=min(10,len(cnt))
        idx=np.argpartition(-cnt,k-1)[:k]; d["topk"]=vc.take(pa.array(idx)).to_pylist()
        if pa.types.is_integer(t) or pa.types.is_floating(t):
            mm=pc.min_max(c); d.update(min=mm["min"].as_py(),max=mm["max"].as_py(),mean=pc.mean(c).as_py(),var=pc.variance(c).as_py(),q=pc.quantile(c,q=[.25,.5,.75]).to_pylist())
        else:
            L=pc.utf8_length(c); d.update(lmin=pc.min(L).as_py(),lmax=pc.max(L).as_py(),lmean=pc.mean(L).as_py())
        out[name]=d
    return out
for f in ["c.csv","big.csv"]:
    try:
        ts=[]
        for _ in range(5):
            t=time.perf_counter(); tbl=pcsv.read_csv(f); p=prof(tbl); ts.append(time.perf_counter()-t)
        ts.sort(); print(f, tbl.num_rows, "rows  median %.3fs  -> %.0fk rows/s"%(ts[2], tbl.num_rows/ts[2]/1e3)); 
    except FileNotFoundError: pass
print(p["age"]["type"], p["amount"]["type"], p["state"]["topk"][:2])
