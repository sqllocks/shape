# G1 re-run evidence (lane/G1-rerun, 2026-10-01)

Base: `c7bd366` (build/main-plan with P1-14..P1-17 done). Gates, tolerances and harness are unchanged.
Every run is reported; none was repeated or dropped.

## Machine
4 cores (`nproc` = 4), Intel Xeon Processor @ 2.80 GHz (33 MB cache), 15 GiB RAM, Linux 6.18.44, Python 3.11.15,
rustc 1.97.0, kernel built with `maturin develop --release`. pyarrow 25.0.1 / numpy 2.4.6 in both venvs.
Pinned RefEngine 3.0.1 `422e78df` (profiler.py md5 `4b253e4f...`), built by `setup_refengine.sh`. T-19 asks for a
4-vCPU machine; this one is 4 vCPU, so the ratios are on the gate's machine class. The RefEngine checkout shows 95
"modified" files: CRLF/`eol=lf` artifacts of the fresh clone, no content difference ignoring EOL; not touched.
Load average before PROF-IN 0.02 and before PROF-CLI 1.14 (`load_before_*.txt`); the harness's own 1.5 gate held
and it holds `$BENCH_OUT_DIR/bench.lock`.

## Commands (each from the repo root, after `source scripts/env.sh`)
```
bash benchmarks/vs_refengine/setup_refengine.sh; maturin develop --release
$SHAPE_VENV/bin/python benchmarks/vs_refengine/profile_1to1/datasets.py
SHAPE_KERNEL=rust   ... profile_1to1/verify.py --impl shape --refresh   -> verify_shape_rust.txt   (exit 0)
SHAPE_KERNEL=python ... profile_1to1/verify.py --impl shape             -> verify_shape_python.txt (exit 0)
... profile_1to1/bench.py --impl shape --out prof_in.json               -> prof_in.txt/json
... profile_1to1/bench_cli.py                                           -> prof_cli.txt/json (equivalence per dataset, START, PROF-CLI)
pytest tests/regressions/test_phase1_bugs.py -q                         -> regressions.txt
mypy (project config)                                                   -> mypy.txt
py-spy record -r 500 -f raw --subprocesses -- python mt.py 15           -> mt_pyspy.raw, mt_hotspots.txt
```
Equivalence ran before any timing. Harness note: `bench.py` warms the page cache once per file and then takes 5
fresh-process runs (median); it makes no separate warm-up call (the docstring says so). That is the harness as
committed; it was not changed.

## PROF-IN (in-process, `exact=True`, median of 5 fresh processes; gate >= 10x)
| workload | RefEngine s | Shape s (default threads) | ratio | Shape 1 thread s | ratio 1T | gate |
|---|---:|---:|---:|---:|---:|---|
| d1.csv | 1.84 | 0.14 | 13.6x | 0.26 | 7.2x | PASS |
| d1.parquet | 1.85 | 0.14 | 12.9x | 0.23 | 8.1x | PASS |
| d2.csv | 38.02 | 1.94 | 19.6x | 4.68 | 8.1x | PASS |
| d2.parquet | 31.61 | 1.73 | 18.2x | 3.84 | 8.2x | PASS |
| d3.csv | 103.11 | 5.56 | 18.5x | 13.59 | 7.6x | PASS |
| d3.parquet | 62.61 | 3.57 | 17.5x | 8.70 | 7.2x | PASS |
| d4.csv | 39.78 | 3.75 | 10.6x | 10.42 | 3.8x | PASS |
| d4.parquet | 34.98 | 2.51 | 13.9x | 7.33 | 4.8x | PASS |
| **mt** | 2.73 | 0.30 | **9.1x** | 0.40 | 6.8x | **MISS** |

MT raw (s): RefEngine 2.735 2.566 2.857 2.725 2.653; Shape 0.354 0.453 0.245 0.256 0.301. Median ratio 9.06x.
Raw per-run values for every workload, loads and RSS: `prof_in.json`, `prof_in.txt`.

## PROF-CLI and START (`prof_cli.json`; equivalence "pass" before timing)
| check | RefEngine | Shape | ratio / value | gate |
|---|---:|---:|---:|---|
| PROF-CLI d2.csv | 41.78 s | 2.86 s | 14.6x | PASS (>= 10x) |
| PROF-CLI d3.csv | 107.62 s | 6.47 s | 16.6x | PASS (>= 10x) |
| START (`shape --version`, median of 10) | | 43.6 ms | 43.6 ms | PASS (<= 300 ms) |

## Other checks
- T-22 parity: `verify.py --impl shape` 49/49 PASS, exit 0, in both `SHAPE_KERNEL=rust` and `python`.
- Phase-1 P-bug regression tests (`tests/regressions/test_phase1_bugs.py`, P19 excluded by design): 21 passed.
- mypy strict on the project config: no issues in 197 files; `shape.profile` is not in the ratchet list.
- P1-14..P1-17: `done` in the §11 tracker at the base commit (04fe94c, 7238901, 981bdd7, 9c0f75b).

## MT hotspots (py-spy, T-28; profile only, no optimisation)
`mt_hotspots.txt`, from `mt_pyspy.raw` (500 Hz, 16 in-process MT profiles; the first includes lazy imports, about 17%
of samples, so read the steady-state frames). In-process MT is 0.27-0.34 s per call here. Steady-state top frames:
1. `table.py:303` (`run`: per-table column pool, 29.8% inclusive) with `pyarrow.compute` kernels (`compute.py:271`, 8.6% self).
2. `fitting.py:26` `detect_distribution` (4.8% self), the orders `amount` column fit.
3. `readers.py:196` `read_csv` (3.6% self; pyarrow reads of the three CSVs).
4. `column.py` `_profile_column` lines 529/541/728 (about 7.7% self together) and `_keys_py` (`column.py:162`).
5. `profile.py` `dataset_to_dict` / `_clean` (5.9% inclusive, `table_to_dict`).
Same picture as P1-17's notes: 3 small tables, so reads, pool start-up and per-column Python dominate.
