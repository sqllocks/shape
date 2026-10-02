# Tools of the P6-01-perf round 4 lane

Scripts used to profile and compare; they are kept so the numbers in
`docs/plans/lane_status/P6-01-perf-r4.md` can be reproduced. They are not part of the product. Run
from the repository root with `source scripts/env.sh` first; the venv's Python runs them.

| script | what it does |
|---|---|
| `phase.py DOMAIN [floor]` | fresh-process phases of the product path: plugin, JSON, reference files, schema check and build, engine, write (`floor`: one row per table) |
| `timeline.py DOMAIN` | one cold run: per-thread timeline of the chunks, per-table first/last chunk, writer calls |
| `colcold.py DOMAIN MS` | the columns slower than MS in one cold run, by thread |
| `coldwarm.py DOMAIN` | cold against warm time of every column of a domain, by strategy (run with `SHAPE_THREADS=1`) |
| `cols.py DOMAIN` | warm single-thread cost of every column at the real chunk size, by strategy |
| `colprof.py DOMAIN TABLE COLUMN N` | per-call cost of one column at N rows, with the cProfile of the calls |
| `chunkprof.py DOMAIN TABLE N [prof]` | per-call cost of one chunk of N rows |
| `overheads.py N` | mean per-call cost at N rows of every strategy over all domains (the fixed cost of a column) |
| `chunkcost2.py`, `chunkcost3.py` | the cost of one more chunk, with threads and with one thread |
| `abenv.py DOMAIN 'JSON' N` | fresh-process A/B of environment variants (`GENPY` names the script that runs one) |
| `chunklow.py`, `swi.py`, `rgrows.py`, `relax.py` | variants for `abenv.py`: minimum parallel chunk size, GIL switch interval, Parquet row-group size, early start of tables that only need a sequence key |
| `kbench.py`, `kbench2.py` | kernel functions per row at 5,000 to 262,144 rows |
| `genonly.py`, `ru.py` | generation without the writer; CPU and page-fault counts of a run |
| `census.py` | the share of every strategy and provider in the cells of all domains |
| `loop.py` | a repeated run, for py-spy |
| `micro.py` | cost of the Arrow and numpy primitives the glue is made of |
| `final_runs.sh PHASE` | the measurement session: `verify`, `before`, `after` |

The tools of round 3 (`../P6-01-perf-domains/tools/`: `digest.py`, `ab_domains.py`, `floor.py`,
`strat_bench.py`) are used as they are; `digest.py` hashes every table of every domain.
`benchmarks/vs_spindle/domain_1to1/compare_trees.py` (interleaved fresh-process comparison of two
source trees) takes `--old` with several directories joined by `:` since this lane.
