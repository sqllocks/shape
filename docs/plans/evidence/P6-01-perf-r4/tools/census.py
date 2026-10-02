import collections, sys
from shape.generation.domains import load_domain, domain_names
from shape.generation.engine import Engine
cells=collections.Counter(); cols=collections.Counter()
per_dom={}
for d in domain_names():
    s=load_domain(d).schema; e=Engine(s,scale="medium",seed=1042)
    tot=0
    for t,tb in s.tables.items():
        n=e.row_counts[t]
        for c in tb.columns.values():
            g=c.generator; st=g.get("strategy")
            sub = g.get("distribution") or g.get("provider") or g.get("pattern") or ""
            if st=="faker" or st=="native": key=(st,g.get("provider"))
            elif st=="distribution": key=(st,g.get("distribution","uniform"))
            elif st=="foreign_key": key=(st,g.get("distribution","uniform"))
            else: key=(st,"")
            cells[key]+=n; cols[key]+=1; tot+=n
        
    per_dom[d]=tot
T=sum(cells.values())
for k,v in cells.most_common(45): print(f"{100*v/T:5.1f}% {v:>11,} cells {cols[k]:4d} cols  {k}")
print(T)
