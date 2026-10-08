# G1-rerun: Gate G1 re-run (lane/G1-rerun)

Status: **G1 NOT MET: one check misses (PROF-IN mt 9.1x < 10x). Every other check passes.**
Gates, tolerances and the harness were not changed. §11 and §2.3 were not edited (the lead does). Base `c7bd366`.
Evidence, commands, machine metadata and raw JSON: `docs/plans/evidence/G1-rerun/` (README has the full tables).
Machine: 4 vCPU Intel Xeon 2.80 GHz (matches T-19's 4-vCPU requirement), Python 3.11.15, pinned RefEngine `422e78d`.

| Check | Result | Value |
|---|---|---|
| Equivalence, `verify.py --impl shape`, `SHAPE_KERNEL=rust` | PASS | exit 0, 49/49 |
| Equivalence, `SHAPE_KERNEL=python` | PASS | exit 0, 49/49 |
| CLI equivalence (bench_cli.py adapter, T-22) | PASS | "pass" on d2.csv and d3.csv |
| T-22 parity on every dataset | PASS | D1-D4, MT, EDGE |
| PROF-IN d1.csv / d1.parquet | PASS | 13.6x / 12.9x |
| PROF-IN d2.csv / d2.parquet | PASS | 19.6x / 18.2x |
| PROF-IN d3.csv / d3.parquet | PASS | 18.5x / 17.5x |
| PROF-IN d4.csv / d4.parquet | PASS | 10.6x / 13.9x |
| **PROF-IN mt** | **MISS** | **9.1x** (RefEngine 2.73 s, Shape 0.30 s; needs <= 0.27 s) |
| PROF-CLI d2.csv / d3.csv | PASS | 14.6x / 16.6x |
| START | PASS | 43.6 ms (gate 300) |
| Phase-1 P-bug regression tests (P19 excluded) | PASS | 21 passed |
| Profile modules mypy strict | PASS | no issues in 197 files; no `shape.profile` in the ratchet |
| P1-14..P1-17 done | PASS | tracker rows 21a-21d |

Single-threaded Shape (reported, not gated): 3.8x (d4.csv) to 8.2x. Per-run values are in `prof_in.json`.

## Notes for the lead
- d4.csv passes with a thin margin (10.6x); medians of 5 on this VM vary roughly 10-20% run to run, so a re-run could
  move it either side of 10x. MT's five Shape runs ranged 0.245-0.453 s. I reported medians only and did not re-run.
- MT hotspots (py-spy, T-28; `mt_hotspots.txt`): per-table column pool in `table.py`, `pyarrow.compute` kernels, the
  orders `amount` distribution fit, pyarrow CSV reads, per-column Python in `column.py`, `dataset_to_dict`/`_clean`.
  No optimisation was started.
- Harness caveat: `bench.py` warms the page cache once per file and then runs 5 fresh processes; there is no separate
  warm-up call (T-19 says 1 warm-up). Unchanged from earlier G1 runs, so the numbers are comparable with them.
- The RefEngine checkout is unmodified in content (CRLF artifacts only). No PyPI, no tags, no force-push.
