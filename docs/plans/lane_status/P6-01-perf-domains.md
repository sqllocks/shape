# P6-01-perf-domains - domain hot paths, round 3 (lane/P6-01-perf-domains)

Status: **done for this round; the GEN-IN gate (10x at medium) is still missed in 12 of the 13 non-retail
domains, and reached by telecom (11.86x, verifier exit 0 on the timed output).** Nothing was lowered, skipped
or changed: no gate, tolerance, decision, seed, floor, case or test. Every check in section 6 was run in this
session on the final tree. The baseline checkout was only read.

In short: Shape's own median time at medium fell by **16% to 41% in all 13 non-retail domains** (retail, which
must not regress, by 23%, and its GEN-CLI by 20%); roughly a quarter of that comes from this lane's changes
(section 2; before the first merge this lane alone measured -7.8% in the median against -33% for the final
tree, and the two lanes' scheduling effects overlap) and the rest from the engine lane's scheduler, native
Parquet writer and allocator tuning, which are merged. **No generated value changed**: every table of all 14 domains (3nf and star, small and medium: 496
tables) hashes the same on the start commit and on the final tree, and all 26 non-retail verifier cells give
exactly the reports the domain lanes recorded (same columns, same clause (h) misses, every table score equal).

Machine for every number below: **Intel(R) Xeon(R) Processor @ 2.80GHz, 4 vCPU (KVM), 15 GiB**, Python
3.11.15, numpy 2.4.6, pyarrow 25.0.1 in both venvs, pinned baseline 3.0.1 (`422e78df`) built by
`setup_spindle.sh`. P6-01b/c/d measured on 2.10 GHz VMs and P6-01a on a 2.80 GHz one, so the ratios here are not
comparable with theirs; every lane's numbers are re-measured here, before and after, on this one machine.
"Before" is the start of this branch (`8b6d99c`, the merge of P6-01a-d), checked out as a git worktree with its
own build of the kernel; "after" is the final tree, which contains the merge of `origin/lane/P6-01-perf-engine`
(dependency-driven scheduler, native parallel Parquet writer, ...; section 4) and of `origin/build/main-plan`.

## 1. Result

### 1.1 Equivalence first (verifier, Shape seed 1042, reference seed 42, baseline seeds 43-46)

`"$SPINDLE_PY" benchmarks/vs_spindle/domain_1to1/verify.py --domain D --scale S --impl shape`, small and
medium, on the final tree, before any timing; then again with `--no-generate` on the directory the final GEN-IN
run wrote (the timed output). Reports: `docs/plans/evidence/P6-01-perf-domains/verify/` (`exit_codes.txt`).

| Domain | small | medium | medium, timed GEN-IN output |
|---|---|---|---|
| hr | PASS (59/59) | PASS (59/59) | PASS (identical to the medium run) |
| real_estate | FAIL (66/66); (h) neighborhood: 72.75 < floor 74.25 | FAIL (66/66); (h) neighborhood: 72.75 < floor 74.25 | FAIL (identical to the medium run) |
| pulse | PASS (40/40) | PASS (40/40) | PASS (identical to the medium run) |
| supply_chain | FAIL (76/76); (h) purchase_order_line: 97.65 < floor 97.67 | PASS (76/76) | PASS (identical to the medium run) |
| insurance | FAIL (63/63); (h) claim: 95.42 < floor 95.46 | PASS (63/63) | PASS (identical to the medium run) |
| capital_markets | PASS (64/64) | FAIL (64/64); (h) company: 84.58 < floor 84.96 | FAIL (identical to the medium run) |
| education | FAIL (61/61); (h) department: 80.08 < floor 83.63; (h) enrollment: 98.82 < floor 99.16 | FAIL (61/61); (h) department: 80.08 < floor 83.63 | FAIL (identical to the medium run) |
| financial | FAIL (77/77); (h) account: 95.67 < floor 95.83; (h) branch: 84.59 < floor 84.91; (h) loan: 96.95 < floor 96.98 | FAIL (77/77); (h) branch: 84.59 < floor 84.91 | FAIL (identical to the medium run) |
| healthcare | PASS (80/80) | FAIL (79/80); (b-e) provider.last_name: failed ['distinct_ratio'] | FAIL (identical to the medium run) |
| iot | PASS (51/51) | FAIL (51/51); (h) sensor: 99.13 < floor 99.24 | FAIL (identical to the medium run) |
| manufacturing | FAIL (62/62); (h) production_line: 92.47 < floor 94.01; (h) work_order: 96.33 < floor 97.29 | FAIL (62/62); (h) production_line: 92.47 < floor 94.01 | FAIL (identical to the medium run) |
| marketing | FAIL (60/60); (h) lead: 98.06 < floor 98.40 | PASS (60/60) | PASS (identical to the medium run) |
| telecom | PASS (62/62) | PASS (62/62) | PASS (identical to the medium run) |
| retail | PASS (60/60) | PASS (60/60) | PASS (identical to the medium run) |

* **Every one of the 26 non-retail cells is exactly what the domain lanes reported** (P6-01a to d): the same
  number of equivalent columns (all in 25 cells, 79 of 80 in healthcare medium), the same misses, the same
  verdict and the same score for every table. The failing cells are the clause (h) chance misses at seed 1042
  that the lanes already reported, and healthcare medium's `provider.last_name` distinct ratio (chance, P6-01b
  Report 1). They are with the owner; I changed no seed, floor, tolerance or case. The timed output of each
  domain gives the same verdict as the generated medium run. A timing below counts only where the verifier of
  the timed output exits 0 (marked); the others are the clause (h) cells, as the lanes recorded.
* Retail small and medium: exit 0 before (start commit) and after, and the two T-21 reports are identical
  (`retail/verify_shape_retail_{small,medium}_before.*` against `verify/verify_shape_retail_{small,medium}.*`:
  columns, verdict and every table score equal).
* Stronger than the verifier: `tools/digest.py` hashes every generated table of every domain (3nf and star) at
  small and medium; `table_digests_start_commit.json` and `table_digests_final_tree.json` are identical
  (496 of 496 tables); the same holds with `SHAPE_THREADS=1`.

### 1.2 GEN-IN at medium (T-19: `bench.py --impl shape --domain D --scales medium --runs 5 --warmup 1`)

One discarded warm-up, five timed runs, each tool in a fresh process, interleaved run by run, median, imports
excluded for both, 4 vCPU, exclusive `bench.lock`, each run waits for a 1-minute load average <= 1.5. Timed
region: load the domain, build the engine, generate, write snappy Parquet (baseline: `Spindle().generate` +
`PandasWriter.to_parquet`). Reports: `docs/plans/evidence/P6-01-perf-domains/{before,after}/bench_gen_in_D.{json,txt}`
(`before` for hr and real_estate was measured first thing in the session, the other twelve with the worktree
right before the `after` runs; same code and kernel build; `after` is the final tree).

| Domain (rows) | Tree | Spindle runs (s) | Spindle median | Shape runs (s) | Shape median | Ratio |
|---|---|---|---|---|---|---|
| hr (78,460) | before | 0.466, 0.465, 0.507, 0.294, 0.283 | 0.465 | 0.070, 0.081, 0.065, 0.084, 0.068 | **0.070** | **6.69x** |
| hr (78,460) | after | 0.489, 0.422, 0.343, 0.321, 0.380 | 0.380 | 0.059, 0.057, 0.056, 0.061, 0.060 | **0.059** | **6.48x** |
| real_estate (140,650) | before | 0.543, 0.652, 0.428, 0.626, 0.602 | 0.602 | 0.106, 0.121, 0.115, 0.095, 0.104 | **0.106** | **5.65x** |
| real_estate (140,650) | after | 0.558, 0.552, 0.495, 0.774, 0.488 | 0.552 | 0.083, 0.082, 0.084, 0.080, 0.088 | **0.083** | **6.64x** |
| pulse (560,000) | before | 1.794, 2.166, 2.018, 2.065, 1.601 | 2.018 | 0.366, 0.347, 0.406, 0.378, 0.443 | **0.378** | **5.34x** |
| pulse (560,000) | after | 1.787, 1.934, 1.903, 1.636, 1.608 | 1.787 | 0.192, 0.235, 0.233, 0.221, 0.207 | **0.221** | **8.07x** |
| supply_chain (210,450) | before | 0.855, 0.770, 0.551, 0.865, 0.547 | 0.770 | 0.136, 0.157, 0.149, 0.189, 0.136 | **0.149** | **5.18x** |
| supply_chain (210,450) | after | 0.878, 0.620, 0.594, 0.773, 0.525 | 0.620 | 0.095, 0.104, 0.111, 0.099, 0.088 | **0.099** | **6.27x** |
| insurance (212,630) | before | 0.746, 0.962, 0.891, 0.550, 0.488 | 0.746 | 0.104, 0.103, 0.119, 0.103, 0.132 | **0.104** | **7.20x** |
| insurance (212,630) | after | 0.838, 0.875, 0.707, 0.585, 0.744 | 0.744 | 0.092, 0.076, 0.074, 0.074, 0.084 | **0.076** | **9.76x** |
| capital_markets (630,350) | before | 1.339, 1.389, 1.296, 1.268, 1.309 | 1.309 | 0.217, 0.209, 0.204, 0.207, 0.217 | **0.209** | **6.25x** |
| capital_markets (630,350) | after | 1.383, 0.889, 0.918, 1.301, 1.232 | 1.232 | 0.146, 0.144, 0.157, 0.139, 0.155 | **0.146** | **8.43x** |
| education (238,275) | before | 0.798, 0.550, 0.594, 0.731, 0.835 | 0.731 | 0.112, 0.141, 0.123, 0.108, 0.101 | **0.112** | **6.53x** |
| education (238,275) | after | 0.793, 0.887, 0.570, 0.462, 0.882 | 0.793 | 0.086, 0.083, 0.097, 0.094, 0.083 | **0.086** | **9.22x** |
| financial (1,678,240) | before | 3.036, 3.328, 3.752, 3.075, 2.782 | 3.075 | 0.476, 0.484, 0.462, 0.462, 0.474 | **0.474** | **6.49x** |
| financial (1,678,240) | after | 3.331, 3.045, 3.033, 3.048, 3.075 | 3.048 | 0.359, 0.340, 0.356, 0.363, 0.399 | **0.359** | **8.48x** |
| healthcare (1,260,800) | before | 3.052, 3.216, 3.155, 3.352, 3.702 | 3.216 | 0.476, 0.441, 0.487, 0.471, 0.434 | **0.471** | **6.83x** |
| healthcare (1,260,800) | after | 3.001, 3.032, 3.294, 3.382, 3.170 | 3.170 | 0.323, 0.381, 0.357, 0.339, 0.340 | **0.340** | **9.33x** |
| iot (292,620) | before | 0.949, 0.537, 0.569, 0.520, 0.534 | 0.537 | 0.115, 0.113, 0.111, 0.129, 0.101 | **0.113** | **4.75x** |
| iot (292,620) | after | 0.925, 0.952, 0.539, 0.560, 0.532 | 0.560 | 0.086, 0.087, 0.079, 0.095, 0.085 | **0.086** | **6.52x** |
| manufacturing (50,520) | before | 0.304, 0.257, 0.324, 0.339, 0.235 | 0.304 | 0.060, 0.059, 0.057, 0.059, 0.056 | **0.059** | **5.18x** |
| manufacturing (50,520) | after | 0.195, 0.239, 0.240, 0.373, 0.216 | 0.239 | 0.061, 0.041, 0.047, 0.051, 0.042 | **0.047** | **5.08x** |
| marketing (438,060) | before | 1.265, 1.222, 1.056, 1.334, 1.158 | 1.222 | 0.238, 0.216, 0.246, 0.262, 0.223 | **0.238** | **5.13x** |
| marketing (438,060) | after | 1.255, 0.788, 1.336, 1.324, 1.238 | 1.255 | 0.157, 0.160, 0.147, 0.156, 0.120 | **0.156** | **8.05x** |
| telecom (1,468,060) | before | 3.649, 4.118, 3.972, 3.434, 3.508 | 3.649 | 0.523, 0.507, 0.469, 0.476, 0.544 | **0.507** | **7.20x** |
| telecom (1,468,060) | after | 4.105, 3.937, 4.463, 4.150, 4.026 | 4.105 | 0.346, 0.324, 0.348, 0.357, 0.333 | **0.346** | **11.86x** |
| retail (1,965,400) | before | 6.740, 6.474, 7.359, 6.685, 6.830 | 6.740 | 0.527, 0.597, 0.510, 0.541, 0.550 | **0.541** | **12.45x** |
| retail (1,965,400) | after | 7.184, 7.022, 6.789, 7.144, 6.678 | 7.022 | 0.458, 0.398, 0.418, 0.485, 0.388 | **0.418** | **16.81x** |

| Domain | Shape median before (s) | after (s) | change | ratio before | ratio after | cumulative Shape runs min-max after |
|---|---|---|---|---|---|---|
| hr | 0.070 | 0.059 | -15.8% | 6.69x | 6.48x | 0.056-0.061 |
| real_estate | 0.106 | 0.083 | -21.9% | 5.65x | 6.64x | 0.080-0.088 |
| pulse | 0.378 | 0.221 | -41.4% | 5.34x | 8.07x | 0.192-0.235 |
| supply_chain | 0.149 | 0.099 | -33.5% | 5.18x | 6.27x | 0.088-0.111 |
| insurance | 0.104 | 0.076 | -26.5% | 7.20x | 9.76x | 0.074-0.092 |
| capital_markets | 0.209 | 0.146 | -30.2% | 6.25x | 8.43x | 0.139-0.157 |
| education | 0.112 | 0.086 | -23.1% | 6.53x | 9.22x | 0.083-0.097 |
| financial | 0.474 | 0.359 | -24.1% | 6.49x | 8.48x | 0.340-0.399 |
| healthcare | 0.471 | 0.340 | -27.8% | 6.83x | 9.33x | 0.323-0.381 |
| iot | 0.113 | 0.086 | -23.9% | 4.75x | 6.52x | 0.079-0.095 |
| manufacturing | 0.059 | 0.047 | -19.8% | 5.18x | 5.08x | 0.041-0.061 |
| marketing | 0.238 | 0.156 | -34.5% | 5.13x | 8.05x | 0.120-0.160 |
| telecom | 0.507 | 0.346 | -31.8% | 7.20x | 11.86x | 0.324-0.357 |
| retail | 0.541 | 0.418 | -22.8% | 12.45x | 16.81x | 0.388-0.485 |

Reading the tables:

* **Shape's own median fell in every domain, by 16% to 41%** (retail by 23%: the "retail must not regress"
  check; it improved).
* The ratio moves less and less evenly, because the baseline's own medians differ by up to 30% between two sets of
  five runs on this VM (hr 0.465 s in the "before" set against 0.380 s in the "after" set, telecom 3.65 against
  4.11 s): P6-01a saw the same. The interleaved comparison below does not involve the baseline and is the better
  measure of the change.
* **Gate (>= 10x with the verifier exit 0 on the timed output): telecom (11.86x) and retail (16.81x).**
  Equivalent and below the gate: hr 6.48x, pulse 8.07x, supply_chain 6.27x, insurance 9.76x, marketing 8.05x.
  Ratios whose timed output has a clause (h) miss, so they do not count: real_estate 6.64x, capital_markets
  8.43x, education 9.22x, financial 8.48x, healthcare 9.33x, iot 6.52x, manufacturing 5.08x.
* The three domains this task named first: hr 6.48x (Shape 0.059 s, the baseline's median 0.38 s, so 10x is
  38 ms), real_estate 6.64x (not countable) and pulse 8.07x (0.221 s; 10x is 179 ms).

### 1.3 Interleaved fresh-process comparison (the evidence for the change itself)

The two trees alternate run by run (`tools/ab_domains.py`: load the domain, build the engine, generate and write
snappy Parquet to a temporary directory, 11 runs each, no baseline involved), start commit against final tree,
`ab_interleaved_before_after.{txt,json}`:

| Domain | before: median (min), ms | after: median (min), ms | change of the median |
|---|---|---|---|
| hr | 87.2 (78.2) | 54.4 (47.5) | -37.6% |
| real_estate | 118.3 (104.1) | 77.8 (71.2) | -34.2% |
| pulse | 370.5 (352.5) | 221.4 (194.7) | -40.2% |
| supply_chain | 148.9 (137.5) | 99.7 (88.6) | -33.1% |
| insurance | 120.9 (109.6) | 84.7 (75.1) | -30.0% |
| capital_markets | 209.6 (185.3) | 152.6 (137.6) | -27.2% |
| education | 132.3 (119.6) | 94.1 (78.8) | -28.9% |
| financial | 477.8 (433.7) | 343.9 (305.3) | -28.0% |
| healthcare | 436.2 (394.7) | 356.5 (322.5) | -18.3% |
| iot | 120.9 (112.5) | 89.1 (73.0) | -26.2% |
| manufacturing | 72.3 (56.2) | 47.7 (42.3) | -34.0% |
| marketing | 220.3 (211.0) | 142.6 (134.4) | -35.3% |
| telecom | 514.8 (468.9) | 334.1 (301.2) | -35.1% |
| retail | 528.5 (476.3) | 442.5 (397.0) | -16.3% |

This lane alone, before the first merge with the engine lane (`ab_interleaved_this_lane_only_before_merge.*`, 11
runs each), moved the median by -1% to -20% (median over the domains -7.8%; hr -1.1%, supply_chain -18.5%, iot
-19.6%, retail -2.8%); that tree included this lane's own thread-count and heaviest-first rules, which the engine
lane's scheduler replaces (section 4). The engine lane measures its own share in its status file.

### 1.4 Retail (must not regress): T-21 and GEN-IN / GEN-CLI at medium, before and after

| Check | Before (start commit) | After (final tree) |
|---|---|---|
| T-21 small (`verify.py --domain retail --scale small --impl shape`) | exit 0 | exit 0, report identical |
| T-21 medium | exit 0 | exit 0, report identical |
| GEN-IN medium (table 1.2) | Shape 0.541 s, 12.45x | Shape 0.418 s, **16.81x** |
| GEN-CLI medium (`bench_cli.py`: `shape generate` against `spindle generate`, start-up included) | Spindle 9.182, 9.920, 9.459, 9.703, 9.458 (median 9.459); Shape 0.856, 0.948, 0.967, 0.975, 0.972 (median 0.967): **9.78x** | Spindle 9.983, 9.585, 8.769, 9.464, 9.143 (median 9.464); Shape 0.768, 0.778, 0.781, 0.783, 0.718 (median 0.778): **12.17x** |
| GEN-CLI medium output verified (`verify.py --cli`) | - | exit 0 |

`docs/plans/evidence/P6-01-perf-domains/retail/` holds the "before" T-21 reports, both GEN-CLI reports and
`exit_codes.txt`. (P6-01a measured GEN-CLI before round 2 at 11.61x on its own VM: the baseline's CLI medians
differ more between sittings than Shape's do.)

## 2. What the profiles say, domain by domain, and what I changed

Tools (`docs/plans/evidence/P6-01-perf-domains/tools/`): `floor.py` (the fixed cost of the product path: one
row per table), `strat_bench.py` (warm cost per row of each column of one table through the engine),
`ab_domains.py`, `digest.py`. Besides these: cProfile of the cold run, `py-spy`, and throw-away timeline
scripts that stamped every chunk and every file write by thread; those patched the start tree's internals,
do not run against the engine lane's scheduler and writer, and are not kept: the numbers from them are marked
"start tree".

**hr (78,460 rows), real_estate, supply_chain, insurance, manufacturing, iot, education: nothing in the schema
is slow; the work is small and the fixed cost of the path is most of it.** hr medium on the start tree took 70 ms
in all, and `floor.py` (the same path with **one row per table**, nine files) took **50 ms** (domain 5.4, engine 1.3,
generate and write nine 1-row tables 43.6). On the final tree the same floor is **28 ms** and hr medium 56 ms
(manufacturing 27 and 47, real_estate 35 and 78). 10x for hr needs 38 ms at the baseline's median of this sitting
(23 to 47 ms in the sessions measured), which is the size of the floor. What the floor is made of (hr, start
tree, cold process):

* generation threads: the same run with `SHAPE_THREADS=1` took **32 ms against 50 ms** (a 1-row table 1.3 to
  2.4 ms on one thread, 4 to 14 ms in the pool: each worker's first calls are cold and the GIL is shared); at
  medium, threads=1 was within noise of the default for hr, real_estate and insurance, i.e. the parallelism bought
  nothing at that size; this is what the engine lane's scheduler now handles (work under about 100,000 cells
  runs on the calling thread);
* loading the domain 5.4 ms (JSON parse 1.1, schema check 1.0, schema build 0.4, three IPC reference files
  1.6 to 2.2) and 1.3 ms to copy the schema into the engine;
* writing: each Parquet file costs 1.3 to 4 ms to open, write and close even for one row; at medium the nine
  files of hr need **37 to 41 ms of CPU to encode** with pyarrow (serial `write_table`), more than the 21 ms
  (warm, one thread; 32 ms cold) that generating them takes. Generating and encoding are about equal parts of
  the CPU time of every domain on the start tree (pulse 300 ms and 265 ms), which is why the native parallel
  writer matters most for these domains;
* the Python glue per column per chunk is flat, 77 us warm (seven columns, one row: 537 us) and spread over a
  hundred small calls (the conversions in `arrowkit`, the context, stream keys, alias tables): no function above
  6% of it, so there is no single target.

A tiny warm-up run (one row per table) before the real one removes 11 to 13 ms (10 to 15%) from hr, real_estate
and insurance, but the warm-up itself costs 44 to 54 ms; I did not pursue a background warm-up thread. The GIL
switch interval (5, 1, 0.2 and 0.05 ms) and the number of writer threads (1 to 4) were measured on hr,
manufacturing and real_estate and made no difference beyond noise (hr floor 53.1, 54.9, 55.5, 59.7 ms for the
four intervals).

What I changed that these domains feel (all values unchanged):

1. **E-mail slugs use Arrow's ASCII lower-case kernel when the text is ASCII** (`providers._lower`): the Unicode
   kernel loads its case tables on its first call, **2.5 to 2.7 ms in every fresh process**, and is five times
   slower afterwards; 12 of the 14 domains have an e-mail column and it was the one cold call above 0.5 ms in
   any domain (the profile of the first generation: `pc.utf8_lower` 2.50 ms in financial, 2.61 in telecom).
   Tests: `tests/generation/test_provider_pools.py` (ASCII, non-ASCII, nulls, slices and other string types
   equal the Unicode kernel).
2. **`_truncate` skips a column whose longest value already fits `max_length`** (the longest value is the
   largest gap in the offsets buffer): `phone_number` 112 -> 62 ns/row inside the engine (telecom's
   `usage_record.destination` was 159 ms of its 467 ms of single-threaded generation); every provider column
   with a `max_length` pays one pass less.
3. **Native kernel** (Rust tests and Python-twin parity tests, both kernels):
   * integers in `template_strings` / `join_strings` are written without the `format!` machinery (`write_int`,
     byte-identical, `i64::MIN` and any width included): three integer slots 98 -> 46 ns/row on one thread, four
     slots 124 -> 57; the buffers are sized from the column lengths and the validity buffer is only built when
     there is a null (`build_utf8_sized`);
   * `pool_take` gathers pools and indices without nulls in one sized pass: sentences 79-95 -> 16 ns/row (20,000
     rows), first names 22 -> 7, cities 25 -> 12; at 200,000 rows sentences 36-48 -> 28-30, the others within
     +-2 ns;
   * `cdf_search` (new; twin = `numpy.searchsorted(side="right")`, NaN handled as there): the draw of a
     discrete distribution from its cumulative table. A Zipf foreign key over 5,000 parents went 51 -> 21.5 ns/row,
     49 parents 38 -> 17, 50,000 parents 57 -> 32 (equal draws on pools of 1 to 2,000,000 and alpha 1.0 to 1.5).
     The engine lane then drew the foreign key itself in one native pass (`zipf_draw`); `cdf_search` stays for
     the tail of a pool beyond 2^20 and for the zipf, poisson and negative_binomial families.

**pulse (560,000 rows).** Start tree (4 threads, 346 ms): generation ends at 202 ms and the file of `trip`
(500,000 rows x 13 columns) is written from 171 ms to 344 ms: **the tail after the last chunk is 45% of the run
and it is one table's serial Parquet encode** (157 ms for the file in isolation, 10 to 20 ms per column per
500,000 rows; dictionary, statistics and snappy). `trip` was held until the post-passes only because of one
rule, `trip.requested_at >= rider.joined_at`, which repairs a row from the row its `rider_id` names and so need
not wait for the table:
* **streamed rule repair** (`early_rules.plan_streamed_rules`, `Engine._repair_chunk`, `fix_rule(..., row_start)`):
  a table whose every rule is row-local is repaired chunk by chunk and is final at generation, so its writer
  starts at its first row group. The plan fires for pulse `trip`, healthcare `encounter`, capital_markets
  `dividend` and retail `product` (retail `order` and healthcare `claim` have computed columns and stay in the
  post-passes). A table qualifies with all of its rules or none; no computed or correlated column may change it
  later; the compute phase must read none of the columns it rewrites; the table a cross-table rule reads is in
  an earlier level (a dependency in the scheduler); and no rule of another table reads a column it rewrites before
  it or rewrites one it reads after it (those rules see the repaired values, as in the plain order). The result
  equals the plain order: 74 tests (both chunk sizes, 1 and 4 threads, the `<=` operator that draws from a
  row-addressed stream), mutation-checked (replacing `row_start` by 0 fails 3 and 7 of them). On the first
  merged tree: pulse 253 ms against 276 ms with the repair switched off (medians of 9 interleaved runs, -8%);
  healthcare, capital_markets and retail within noise.
* **rules that cannot repair do not hold a table back** (`rules.can_repair`): `high >= low` on one table,
  `allowed_amount <= charge_amount`, ... are validated and never change a row (`fix_rule` repairs `<` and `>`
  within a table and `>=`, `>` and `<=` across tables), yet `repair_target` named their table, so capital_markets
  `daily_price` (126,000 rows) and healthcare `claim_line` (356,000) were written after the post-passes; they now
  stream.

The serial encode of `trip` and the final rule validation were the tail. The engine lane's native writer encodes
row groups and columns on the kernel's pool and its validation runs beside the copula; pulse's own median went
from 0.378 s to 0.221 s. A row-group plan with a small last group (first group 128k rows, last 64k) measured
-7% on pulse and -6% on financial on the start tree (9 runs each) and was not adopted: the native writer changes
the picture.

**healthcare, financial, telecom, capital_markets, marketing (the larger ones).** The profile is flat. Across all
14 domains at medium (cold, `SHAPE_THREADS=1`, start tree) the largest strategy costs were `foreign_key` 583 ms
(14.6 M values, 40 ns each), `faker` 564 ms (2.9 M, 196 ns), `weighted_enum` 378 ms (11.5 M, 33 ns), `distribution`
327 ms (10.8 M, 30 ns) and `lookup` 272 ms (4.5 M, 60 ns); nothing above 6% of any domain. After the changes:
`foreign_key` 551 ms and `faker` 367 ms (127 ns per value; e-mail alone is still 236 ns/row, the dearest provider,
for 5,000 to 50,000-row tables). Fused native passes for `weighted_enum`, `distribution` and the uniform
integer draws (each is four to six numpy and Arrow passes of 5 to 8 ns today), and one native pass for e-mail,
are the next step and the engine lane's "native generation core"; I did not duplicate them.

## 3. Measured and not adopted, and what the engine lane did of it

* GIL switch interval, writer thread count, a background warm-up run: no gain (section 2).
* Row groups with a small last group: -6% to -7% on pulse and financial on the start tree; left to the writer.
* Building the whole-table pass of a capped foreign key (`cap_per_parent` over 500,000 rows plus the index
  draw, 25 ms in pulse `trip.rider_id`, similar in financial and retail) while the earlier levels run, instead of
  inside the first chunk of the table, which holds the engine-wide lock for the whole build: this needed
  per-key locks in `Engine.cached`; the engine lane built exactly that (`Engine.cached` per key, the `prepare`
  hook), so I did not.
* `load_domain` (5.4 ms for hr) and the engine's schema copy (1.3 ms) could lose about 2 ms (3% of hr) at the
  price of a trusted-schema flag in the plugin API; not done.

## 4. Merges

* `origin/lane/P6-01-perf-engine` (native parallel Parquet writer, dependency-driven scheduler, glibc thresholds,
  Zipf in one native pass, per-key `Engine.cached` with a `prepare` hook, final validation beside the copula;
  it had already merged this lane's first three commits) was merged twice, `29340eb` and at the end. The one
  conflict was `engine.py`: its scheduler (`scheduler.run_tables`, `Engine._generate_tables`) replaces this
  lane's `_generate_level`, `_small_level` and `_heaviest_first`. I took its side in all three hunks (it had
  already integrated the streamed rule repair, including the cross-table dependency of a repaired table on its
  parent) and removed my two rules and their tests, keeping a test that the result lists the tables in level
  order. Its `zipf_draw` and my `cdf_search` coexist (above). Both lanes' tests pass together.
* `origin/build/main-plan` was merged (merge commits only, no rebase, no force-push); the two conflicts were
  both-sides-added entries in `CHANGELOG.md` and `benchmarks/vs_spindle/domain_1to1/README.md`, kept both.
  Every check was re-run after the last merge.

## 5. For the owner

* **GEN-IN at medium is still below 10x for 12 of the 13 non-retail domains**, and for several of them the
  timed output does not pass the verifier because of the chance misses already recorded. What is left in the small
  domains is the fixed cost of the path (about 28 ms for hr with one row per table, against 38 ms that 10x allows),
  and in the large ones the per-value cost of a flat set of strategies. The two things that remain are a native
  generation core (fused weighted_enum / distribution / uniform draws / e-mail) and a cheaper domain load.
* Decisions already waiting from the domain lanes are unchanged: clause (h) at seed 1042 (P6-01a Escalation 1,
  P6-01b Reports 1 and 2, P6-01c, P6-01d), the notice wording (P6-01a round 2, item 3).
* The engine lane's own deviations (T-17 and T-02 for the native Parquet writer, the crate notices, the
  `mallopt` process setting) are in its status file; this lane adds none. This lane added one native kernel
  function (`cdf_search`) with its twin, documented in `docs/GENERATION_KERNEL.md`.

## 6. Checks (final tree)

| Check | Result |
|---|---|
| `ruff check` / `ruff format --check` (src tests plugins benchmarks/vs_spindle) | all passed / 729 files formatted |
| `mypy` (strict) | no issues, 306 source files |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | exit 0 |
| `lint-imports` | 1 kept, 0 broken |
| `scripts/check_user_facing.py`, and `--wheel` on the built `sqllocks_shape-0.9.0` and `sqllocks_shape_domains-0.9.0` wheels | clean, clean |
| `bandit -q -r src -ll` | exit 0 |
| `check_requirements`, `check_secrets`, `check_plugin_skeletons`, `check_conformance_coverage`, `compileall` | OK (89 requirements; 7 distributions; 32 of 32 conformance tests) |
| `cargo fmt --check`, `cargo clippy --release --all-targets -- -D warnings`, `cargo test --release` | clean, clean, 40 passed |
| START (`shape version`, 11 runs) | median 77 ms (min 69, max 96); gate 300 ms |
| `benchmarks/vs_spindle/strategy_1to1/baseline.py`, `baseline_p404b.py`, `relational_baseline.py` with `--check` | every strategy `match`, exit 0 (all three) |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric` | 4950 passed, 46 deselected |
| `SHAPE_KERNEL=python`, same selection | 4950 passed, 46 deselected |
| `SHAPE_KERNEL=python pytest tests/kernel` | 360 passed |
| `pytest -m heavy --ignore=tests/demo/fabric` | 42 passed (the Fabric demo tests need `nbformat`, which this venv does not have; the other selections ignore that directory as the brief says) |
| `pytest plugins/shape-domains` | 1 passed |
| New tests of this lane | `tests/generation/test_streamed_rules.py` (74, both kernels), `tests/kernel/test_gen_kernel.py` and `test_dense_kernels.py` (string assembly, `pool_take`, `cdf_search`: native against twin and against numpy), `tests/generation/test_provider_pools.py` (lower-case, truncation), `tests/generation/test_engine_parallel.py` |
| `make check` extras not in the list above | `compileall`, the three scripts above and `SHAPE_KERNEL=python pytest tests/kernel` run; the `--cov-fail-under=86` run was not repeated |

Evidence: `docs/plans/evidence/P6-01-perf-domains/` (`verify/`, `before/`, `after/`, `retail/`, the interleaved
comparisons, the table digests, `tools/`).
