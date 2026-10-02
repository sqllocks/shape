# Tools of the P6-01-perf-domains lane

Small scripts used to profile and compare (not part of the product; they are kept so the numbers in
`docs/plans/lane_status/P6-01-perf-domains.md` can be reproduced):

| script | what it does |
|---|---|
| `digest.py OUT.json scale[,scale]` | sha256 of every generated table of every domain (3nf and star) at seed 1042; two trees that give the same file give the same values |
| `ab_domains.py` | interleaved fresh-process before/after timing of load + engine + write for each domain (`BEFORE_SRC` names a checkout of the start commit) |
| `timeline.py DOMAIN SCALE` | when each chunk is generated and each file written, by thread (set `FLOOR=1` for one row per table) |
| `floor.py DOMAIN...` | the fixed cost of the product path: one row per table against the preset |
| `decomp.py SCALE` | wall time against the end of generation, per domain |
| `strat_bench.py DOMAIN TABLE ROWS` | warm cost per row of each column of one table through the engine |
