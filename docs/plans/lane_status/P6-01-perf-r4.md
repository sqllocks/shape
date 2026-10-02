# P6-01-perf-r4 - domain fixed cost and fused generation, round 4 (lane/P6-01-perf-r4)

Status: **built, measured and verified; the GEN-IN gate (10x at medium) is now reached by insurance (11.5x) and marketing
(10.9x) on top of telecom (13.6x) and retail (18.4x), with the verifier on the timed output exiting 0, and it is still
missed for hr (8.3x), pulse (9.0x) and supply_chain (7.6x).** Four more domains measure 10.4x to 11.3x, but their timed
output carries the clause-(h) chance misses the owner accepted on 2026-10-02, so under the gate as written (verifier exit 0)
they do not count; the other four are at 7.5x to 9.4x (table B). Nothing was lowered, skipped or changed: no gate,
tolerance, decision, seed, floor, case or test. The baseline checkout was only read. Every check in section 7 was run in this
session on the final tree.

In short: **no generated value changed** (all 496 tables of all 14 domains, 3nf and star, small and medium, hash the same on
the start commit and on the final tree, with the default threads and with `SHAPE_THREADS=1`; all 28 verifier reports of the
final tree equal the round-3 reports). Shape's own time at medium fell in every domain: **-5% to -26%** in the interleaved
fresh-process comparison (hr -23%, insurance -20%, education -20%, iot -21%, marketing -21%, manufacturing -26%; retail -6%,
which must not regress and did not; retail large -23%). The two levers of the brief were both worth something and neither is
the whole story: the fixed cost of the path in a small domain fell from 32 ms to 22 ms for hr (one row per table), and the
per-call and per-row cost of the commonest strategies fell by 20% to 80%; what is left in the small domains is the cold start
of the process, a serial chain of tables, Python glue held under the GIL, and the Parquet encoder, none of which a further
round of this kind removes (section 5).

Machine for every number below: **Intel(R) Xeon(R) Processor @ 2.80GHz, 4 vCPU (KVM, Linux 6.18 "fc" kernel), 15 GiB**,
Python 3.11.15, numpy 2.4.6, pyarrow 25.0.1 in both venvs, the pinned baseline 3.0.1 (`422e78df`) built by `setup_spindle.sh`,
Rust 1.97. This VM is slower than the ones of the earlier lanes (retail large takes 209 s for the baseline here, 97 s there;
the first touch of a fresh memory page costs 3.3 us here when idle), so absolute times and ratios are not comparable with
theirs; every figure below was measured here, before and after, in this session. "Before" is the start of this branch
(`06cf647`, the final tree of round 3), checked out as a git worktree with its own copy of the kernel build (the Rust source
was identical at that point); "after" is the final tree (it contains the merge of `origin/build/main-plan`).

## 1. Result

### 1.1 Equivalence first

* **Table digests.** `docs/plans/evidence/P6-01-perf-domains/tools/digest.py` (sha256 of the IPC bytes of every generated
  table, seed 1042) over 14 domains x 3nf/star x small/medium = **496 tables**: the start commit and the final tree give the
  same file (`table_digests_start.json` = `table_digests_final.json`, and = round 3's `table_digests_final_tree.json`);
  the same holds with `SHAPE_THREADS=1` (`table_digests_final_threads1.json`). The digests were re-taken after every change
  (nine times in the session), never equal by accident of the order: each time against the file taken before the first change.
* **Verifier** (`"$SPINDLE_PY" domain_1to1/verify.py --domain D --scale S --impl shape`, Shape seed 1042, reference seed 42,
  baseline seeds 43-46), small and medium, **before any timing**, on the final tree, 28 cells: the report of every cell is
  identical to the round-3 report (same columns, same clause-(h) misses, same verdict, every table score equal; only the
  NUL-padded progress line and the timing lines differ): `docs/plans/evidence/P6-01-perf-r4/verify/` (`exit_codes.txt`).
  Pass: hr, pulse, telecom, retail (both scales), capital_markets, healthcare and iot small, supply_chain, insurance and
  marketing medium. The failing cells are the clause-(h) chance misses of the domain lanes and healthcare medium
  `provider.last_name`; the owner accepted them as chance on 2026-10-02 (decision log), and I changed no seed, floor,
  tolerance or case.
* **The timed output** of every domain's last GEN-IN run is verified with `--no-generate` right after its benchmark
  (table 1.2 last column): the exit codes are the medium cells above (`verify_shape_D_medium_timed_output.*`).
* **Tests of the equality itself** (new): every fused kernel function against its twin and against the unfused composition of
  the older calls it replaces, bit for bit (`tests/kernel/test_fused_kernel.py`, 107 tests with both kernels; the log-normal
  one compares the raw 64-bit patterns over 120 random parameter sets, clips and scales, and 300 more in the session);
  `distribution` log-normal with the fused pass against numpy's steps for 5 specs x 5 scales x 4 sizes
  (`test_distribution_fused.py`); temporal, derived, sequence and foreign keys likewise (`test_temporal_fast_units.py`,
  `test_derived_fused.py`, `test_range_keys_fused.py`: whole tables of three domains equal with and without the fused call).

### 1.2 GEN-IN at medium (T-19: `bench.py --impl shape --domain D --scales medium`)

Each tool in a fresh process, interleaved run by run, imports excluded for both, exclusive `bench.lock`, each run waits for a
1-minute load average <= 1.5, 4 vCPU. Three sets of both trees were timed in one sitting (set 1: five runs after one warm-up;
set 2 in the opposite order, after-tree first: five runs after one warm-up; set 3: eleven runs after two warm-ups), because
**the baseline's own median moves by up to 50% between two sets on this VM** (hr 0.34-0.59 s, supply_chain 0.68-1.07 s,
manufacturing 0.24-0.45 s for the same tool in the same sitting), which moves the ratio by as much as the change being
measured. Table A is set 3 with every raw run; table B has the three sets and the pooled medians (median of all 42 baseline
runs of a domain against the median of the 21 Shape runs of a tree); the raw runs of every set are in
`docs/plans/evidence/P6-01-perf-r4/{before,after,before2,after2,before3,after3}/`.

**Table A - set 3 (one discarded warm-up of two, eleven timed runs each, interleaved run by run).**

| Domain (rows) | Tree | Baseline runs (s) | Baseline median | Shape runs (s) | Shape median | Ratio | Verifier on the timed output |
|---|---|---|---|---|---|---|---|
| hr (78,460) | before | 0.542, 0.498, 0.334, 0.623, 0.442, 0.413, 0.432, 0.436, 0.414, 0.550, 0.431 | 0.436 | 0.079, 0.058, 0.069, 0.069, 0.067, 0.072, 0.079, 0.079, 0.083, 0.068, 0.087 | **0.072** | **6.06x** |  |
| hr (78,460) | after | 0.384, 0.494, 0.554, 0.417, 0.400, 0.354, 0.547, 0.336, 0.598, 0.446, 0.551 | 0.446 | 0.057, 0.048, 0.052, 0.057, 0.050, 0.054, 0.055, 0.051, 0.046, 0.064, 0.060 | **0.054** | **8.26x** | exit 0 |
| real_estate (140,650) | before | 0.667, 0.703, 0.814, 0.795, 0.513, 0.717, 0.680, 0.490, 0.839, 0.747, 0.491 | 0.703 | 0.098, 0.083, 0.096, 0.085, 0.104, 0.093, 0.088, 0.092, 0.096, 0.091, 0.102 | **0.093** | **7.56x** |  |
| real_estate (140,650) | after | 0.797, 0.670, 0.875, 0.676, 1.077, 0.885, 1.045, 0.668, 0.683, 0.729, 0.678 | 0.729 | 0.086, 0.074, 0.082, 0.090, 0.081, 0.079, 0.091, 0.082, 0.086, 0.077, 0.087 | **0.082** | **8.89x** | exit 1 |
| pulse (560,000) | before | 1.866, 2.210, 2.217, 2.176, 2.262, 1.960, 2.158, 2.290, 1.868, 2.113, 2.298 | 2.176 | 0.252, 0.255, 0.238, 0.261, 0.237, 0.275, 0.276, 0.256, 0.244, 0.232, 0.268 | **0.255** | **8.53x** |  |
| pulse (560,000) | after | 2.150, 2.731, 1.728, 2.039, 2.370, 2.179, 2.293, 2.349, 1.696, 2.451, 2.491 | 2.293 | 0.270, 0.256, 0.249, 0.222, 0.246, 0.216, 0.253, 0.245, 0.272, 0.310, 0.236 | **0.249** | **9.21x** | exit 0 |
| supply_chain (210,450) | before | 0.708, 1.057, 0.846, 0.682, 0.623, 0.793, 0.699, 0.600, 0.740, 0.915, 0.658 | 0.708 | 0.121, 0.103, 0.095, 0.109, 0.113, 0.128, 0.104, 0.102, 0.114, 0.124, 0.115 | **0.113** | **6.27x** |  |
| supply_chain (210,450) | after | 1.054, 1.178, 0.979, 0.798, 1.070, 0.712, 1.039, 1.053, 0.677, 1.052, 1.132 | 1.052 | 0.093, 0.108, 0.108, 0.101, 0.114, 0.128, 0.107, 0.095, 0.120, 0.102, 0.095 | **0.107** | **9.83x** | exit 0 |
| insurance (212,630) | before | 0.976, 0.938, 0.581, 0.602, 0.897, 0.596, 0.818, 0.932, 0.909, 0.994, 1.051 | 0.909 | 0.084, 0.097, 0.098, 0.085, 0.103, 0.102, 0.108, 0.115, 0.111, 0.084, 0.101 | **0.101** | **9.00x** |  |
| insurance (212,630) | after | 1.287, 0.797, 0.984, 0.601, 0.584, 1.140, 1.095, 1.109, 0.669, 0.643, 1.036 | 0.984 | 0.078, 0.103, 0.094, 0.077, 0.080, 0.094, 0.079, 0.076, 0.073, 0.077, 0.068 | **0.078** | **12.62x** | exit 0 |
| capital_markets (630,350) | before | 1.596, 1.141, 1.155, 1.126, 1.461, 1.042, 1.073, 1.322, 1.197, 1.280, 1.562 | 1.197 | 0.162, 0.210, 0.167, 0.172, 0.171, 0.186, 0.190, 0.183, 0.181, 0.172, 0.160 | **0.172** | **6.96x** |  |
| capital_markets (630,350) | after | 1.524, 1.348, 1.532, 1.387, 1.545, 1.407, 1.299, 1.297, 1.292, 1.516, 1.472 | 1.407 | 0.162, 0.152, 0.147, 0.149, 0.164, 0.157, 0.162, 0.149, 0.175, 0.158, 0.170 | **0.158** | **8.91x** | exit 1 |
| education (238,275) | before | 0.937, 1.058, 0.640, 0.942, 0.637, 0.907, 1.001, 0.824, 1.010, 1.049, 0.947 | 0.942 | 0.094, 0.094, 0.111, 0.100, 0.105, 0.101, 0.103, 0.102, 0.112, 0.114, 0.113 | **0.103** | **9.15x** |  |
| education (238,275) | after | 0.864, 0.823, 0.952, 1.324, 0.620, 0.960, 0.999, 0.837, 1.084, 0.545, 0.936 | 0.936 | 0.079, 0.085, 0.079, 0.086, 0.078, 0.087, 0.073, 0.098, 0.094, 0.099, 0.080 | **0.085** | **11.01x** | exit 1 |
| financial (1,678,240) | before | 4.102, 3.879, 4.304, 3.972, 4.026, 3.866, 3.816, 3.918, 3.891, 3.782, 4.154 | 3.918 | 0.404, 0.395, 0.408, 0.410, 0.419, 0.392, 0.390, 0.389, 0.398, 0.396, 0.382 | **0.396** | **9.89x** |  |
| financial (1,678,240) | after | 4.166, 4.084, 3.652, 3.902, 3.841, 4.123, 4.111, 4.496, 4.816, 4.133, 4.259 | 4.123 | 0.327, 0.320, 0.340, 0.354, 0.370, 0.378, 0.331, 0.412, 0.401, 0.352, 0.408 | **0.354** | **11.65x** | exit 1 |
| healthcare (1,260,800) | before | 3.935, 3.909, 4.321, 4.459, 4.086, 3.851, 3.594, 4.205, 3.689, 3.610, 4.019 | 3.935 | 0.397, 0.467, 0.435, 0.425, 0.466, 0.440, 0.441, 0.457, 0.437, 0.386, 0.413 | **0.437** | **9.00x** |  |
| healthcare (1,260,800) | after | 4.248, 4.168, 3.915, 4.150, 3.681, 3.627, 3.886, 4.345, 4.264, 3.726, 4.086 | 4.086 | 0.367, 0.368, 0.407, 0.377, 0.343, 0.390, 0.409, 0.378, 0.381, 0.358, 0.351 | **0.377** | **10.84x** | exit 1 |
| iot (292,620) | before | 0.632, 0.923, 0.651, 0.796, 1.060, 0.948, 0.696, 0.955, 0.693, 0.733, 0.611 | 0.733 | 0.091, 0.099, 0.114, 0.096, 0.096, 0.119, 0.115, 0.105, 0.099, 0.100, 0.156 | **0.100** | **7.33x** |  |
| iot (292,620) | after | 0.900, 1.029, 0.941, 1.062, 0.689, 0.599, 0.695, 0.647, 0.870, 0.857, 1.005 | 0.870 | 0.075, 0.101, 0.073, 0.070, 0.069, 0.069, 0.072, 0.077, 0.069, 0.080, 0.069 | **0.072** | **12.08x** | exit 1 |
| manufacturing (50,520) | before | 0.323, 0.271, 0.252, 0.300, 0.378, 0.444, 0.265, 0.418, 0.267, 0.360, 0.417 | 0.323 | 0.065, 0.056, 0.058, 0.047, 0.055, 0.055, 0.056, 0.064, 0.047, 0.058, 0.061 | **0.056** | **5.77x** |  |
| manufacturing (50,520) | after | 0.350, 0.370, 0.439, 0.426, 0.263, 0.238, 0.431, 0.249, 0.336, 0.237, 0.268 | 0.336 | 0.039, 0.045, 0.043, 0.040, 0.042, 0.042, 0.040, 0.047, 0.041, 0.037, 0.043 | **0.042** | **8.00x** | exit 1 |
| marketing (438,060) | before | 1.022, 1.366, 1.665, 1.480, 1.603, 1.022, 1.578, 1.676, 1.026, 1.636, 1.193 | 1.480 | 0.165, 0.159, 0.201, 0.185, 0.184, 0.175, 0.182, 0.193, 0.180, 0.185, 0.167 | **0.182** | **8.13x** |  |
| marketing (438,060) | after | 0.981, 0.985, 1.627, 1.403, 0.986, 1.381, 1.189, 1.435, 1.458, 1.462, 1.070 | 1.381 | 0.132, 0.120, 0.137, 0.130, 0.119, 0.112, 0.114, 0.122, 0.113, 0.128, 0.127 | **0.122** | **11.32x** | exit 0 |
| telecom (1,468,060) | before | 4.051, 4.608, 5.020, 4.970, 4.956, 4.778, 5.233, 4.403, 4.516, 4.968, 4.771 | 4.778 | 0.406, 0.334, 0.383, 0.376, 0.401, 0.350, 0.391, 0.391, 0.378, 0.397, 0.410 | **0.391** | **12.22x** |  |
| telecom (1,468,060) | after | 4.597, 4.805, 4.474, 5.045, 4.891, 4.392, 4.651, 5.076, 4.546, 4.610, 4.298 | 4.610 | 0.377, 0.334, 0.346, 0.393, 0.367, 0.338, 0.369, 0.321, 0.302, 0.344, 0.323 | **0.344** | **13.40x** | exit 0 |
| retail (1,965,400) | before | 8.576, 9.299, 9.711, 8.605, 9.116, 8.829, 8.972, 8.506, 8.674, 8.328, 8.702 | 8.702 | 0.539, 0.599, 0.520, 0.528, 0.535, 0.476, 0.503, 0.535, 0.542, 0.520, 0.577 | **0.535** | **16.27x** |  |
| retail (1,965,400) | after | 8.198, 8.262, 8.426, 7.997, 8.773, 8.346, 7.993, 8.981, 8.459, 8.023, 7.676 | 8.262 | 0.482, 0.487, 0.513, 0.461, 0.473, 0.461, 0.527, 0.466, 0.429, 0.474, 0.455 | **0.473** | **17.47x** | exit 0 |

**Table B - the three sets and the pooled medians (ratio = median of all baseline runs / median of Shape's runs of that tree).**

| Domain | set 1 before / after | set 2 before / after | set 3 before / after | baseline median, all 6 sets (s) | Shape before, pooled (s) | Shape after, pooled (s) | change | ratio before | ratio after | verifier (timed output) |
|---|---|---|---|---|---|---|---|---|---|---|
| hr | 6.05x / 7.10x | 6.68x / 10.54x | 6.06x / 8.26x | 0.459 | 0.075 | 0.055 | -26.7% | 6.11x | **8.34x** | exit 0 |
| real_estate | 7.47x / 9.78x | 7.29x / 10.52x | 7.56x / 8.89x | 0.718 | 0.092 | 0.081 | -12.0% | 7.81x | **8.87x** | exit 1 |
| pulse | 8.25x / 8.98x | 9.30x / 8.90x | 8.53x / 9.21x | 2.292 | 0.268 | 0.254 | -5.2% | 8.55x | **9.02x** | exit 0 |
| supply_chain | 5.62x / 10.07x | 6.65x / 7.18x | 6.27x / 9.83x | 0.796 | 0.115 | 0.105 | -8.7% | 6.92x | **7.58x** | exit 0 |
| insurance | 8.88x / 12.97x | 9.90x / 7.91x | 9.00x / 12.62x | 0.896 | 0.097 | 0.078 | -19.6% | 9.24x | **11.49x** | exit 0 |
| capital_markets | 7.60x / 9.42x | 8.25x / 7.40x | 6.96x / 8.91x | 1.347 | 0.172 | 0.160 | -7.0% | 7.83x | **8.42x** | exit 1 |
| education | 6.63x / 10.00x | 9.36x / 7.60x | 9.15x / 11.01x | 0.903 | 0.103 | 0.087 | -15.5% | 8.77x | **10.39x** | exit 1 |
| financial | 9.96x / 11.23x | 9.44x / 10.17x | 9.89x / 11.65x | 4.095 | 0.407 | 0.378 | -7.1% | 10.06x | **10.83x** | exit 1 |
| healthcare | 9.14x / 10.60x | 9.04x / 10.95x | 9.00x / 10.84x | 4.016 | 0.435 | 0.386 | -11.3% | 9.23x | **10.40x** | exit 1 |
| iot | 7.62x / 12.31x | 8.24x / 11.79x | 7.33x / 12.08x | 0.827 | 0.099 | 0.073 | -26.3% | 8.35x | **11.33x** | exit 1 |
| manufacturing | 5.19x / 9.44x | 6.20x / 5.62x | 5.77x / 8.00x | 0.321 | 0.056 | 0.043 | -23.2% | 5.72x | **7.45x** | exit 1 |
| marketing | 8.99x / 9.71x | 7.01x / 10.94x | 8.13x / 11.32x | 1.433 | 0.170 | 0.131 | -22.9% | 8.43x | **10.94x** | exit 0 |
| telecom | 13.09x / 14.37x | 12.11x / 12.83x | 12.22x / 13.40x | 4.774 | 0.384 | 0.352 | -8.3% | 12.43x | **13.56x** | exit 0 |
| retail | 17.24x / 19.15x | 17.47x / 17.82x | 16.27x / 17.47x | 8.712 | 0.525 | 0.474 | -9.7% | 16.59x | **18.38x** | exit 0 |

Reading the tables:

* **Shape's own median fell in every domain** (pooled, last-but-one column: hr -27%, iot -26%, manufacturing -23%, marketing
  -23%, insurance -20%, education -16%, real_estate -12%, healthcare -11%, retail -10%, supply_chain -9%, telecom -8%,
  capital_markets -7%, financial -7%, pulse -5%). The interleaved comparison of 1.3 does not involve the baseline and is the
  better measure of the change.
* **Gate (>= 10x at medium with the verifier exit 0 on the timed output), pooled:** telecom 13.6x and retail 18.4x (as
  before), and now **insurance 11.5x and marketing 10.9x**. In single sets insurance is 7.9x to 13.0x and marketing 9.7x to
  11.3x, hr reaches 10.5x once (set 2) and supply_chain 10.1x once (set 1) and 9.8x once (set 3): the gate is crossed by those
  two on some sittings and not on others, and I do not count them.
* **At or above 10x pooled, but the timed output has a clause-(h) chance miss (verifier exit 1), so by the gate as written
  they do not count:** iot 11.3x, financial 10.8x, healthcare 10.4x, education 10.4x. Whether they count after the owner's
  acceptance of the misses is the owner's to say.
* **Below 10x:** pulse 9.0x (exit 0), hr 8.3x (exit 0), supply_chain 7.6x (exit 0); real_estate 8.9x, capital_markets 8.4x,
  manufacturing 7.5x (exit 1).

### 1.3 Interleaved fresh-process comparison (the evidence for the change itself)

The two trees alternate run by run (`benchmarks/vs_spindle/domain_1to1/compare_trees.py`: load the domain, build the engine,
generate and write snappy Parquet to a temporary directory, 15 pairs each, no baseline involved), round-3 tree against final
tree, `docs/plans/evidence/P6-01-perf-r4/ab/interleaved_before_after.{txt,json}`:

| Domain | old median (min), ms | new median (min), ms | change of the median | CPU-s old | CPU-s new |
|---|---|---|---|---|---|
| hr | 63.4 (51.8) | 48.5 (39.9) | -23.5% | 0.106 | 0.091 |
| real_estate | 86.2 (78.4) | 74.4 (63.5) | -13.6% | 0.164 | 0.143 |
| pulse | 256.6 (231.3) | 243.8 (218.6) | -5.0% | 0.716 | 0.687 |
| supply_chain | 112.7 (94.9) | 91.3 (80.4) | -18.9% | 0.225 | 0.193 |
| insurance | 92.1 (75.9) | 73.9 (65.4) | -19.8% | 0.183 | 0.165 |
| capital_markets | 165.4 (153.8) | 139.5 (129.8) | -15.7% | 0.406 | 0.365 |
| education | 97.6 (87.9) | 77.8 (69.1) | -20.3% | 0.201 | 0.175 |
| financial | 385.9 (365.7) | 357.4 (315.4) | -7.4% | 1.091 | 1.075 |
| healthcare | 400.3 (366.0) | 355.1 (323.0) | -11.3% | 1.001 | 0.948 |
| iot | 90.6 (83.3) | 71.7 (63.2) | -20.9% | 0.176 | 0.151 |
| manufacturing | 52.5 (44.0) | 38.8 (36.4) | -26.1% | 0.084 | 0.073 |
| marketing | 159.2 (141.1) | 125.4 (116.8) | -21.2% | 0.397 | 0.324 |
| telecom | 360.1 (337.1) | 314.1 (291.2) | -12.8% | 0.972 | 0.925 |
| retail | 490.4 (449.9) | 463.6 (414.6) | -5.5% | 1.296 | 1.266 |

(`compare_trees.py` takes `--old` with several directories joined by `:` since this lane, because the two trees' plugins
differ too.) Shape's CPU-seconds fell with its wall time (hr -14%, marketing -18%, retail -2%).

### 1.4 Retail (must not regress): GEN-IN and GEN-CLI at medium and large, before and after

| Check | Before (round-3 tree) | After (final tree) |
|---|---|---|
| T-21 small and medium (`verify.py --domain retail --impl shape`) | exit 0, report = round 3 | exit 0, report identical |
| GEN-IN medium, set 3 (table A): baseline / Shape median | 8.702 s / 0.535 s = 16.27x | 8.262 s / 0.473 s = **17.47x** (pooled 16.59x -> 18.38x) |
| GEN-IN large (`bench.py --scales large --runs 3 --warmup 1`) | baseline 209.653, 206.348, 210.439 (median 209.653); Shape 8.893, 8.185, 8.369 (median **8.369**): **25.05x** | baseline 205.888, 209.879, 208.975 (median 208.975); Shape 6.811, 6.230, 6.449 (median **6.449**): **32.40x** |
| GEN-CLI medium (`bench_cli.py`, 5 runs after 1 warm-up, start-up included) | baseline 10.879 s, Shape 0.842 s: **12.92x** | baseline 10.518 s, Shape 0.779 s: **13.50x** |
| GEN-CLI large | baseline 212.635 s, Shape 7.737 s: **27.48x** | baseline 209.657 s, Shape 6.391 s: **32.80x** |
| GEN-CLI medium output verified (`verify.py --cli`) | - | exit 0 |

Raw runs of every row are in `docs/plans/evidence/P6-01-perf-r4/retail/`. Retail improved on all four timings (Shape's
time -11% medium GEN-IN pooled, -23% large GEN-IN, -7% medium and -17% large GEN-CLI).

## 2. Profile: what the fixed cost of the path is made of

Tools: `docs/plans/evidence/P6-01-perf-r4/tools/` (README there) and round 3's `floor.py`. All in a cold fresh process, as
T-19 times it.

**The domain load (hr, round-3 tree, medians of 7 fresh processes).** Plugin import 0.23 ms; reading the schema 1.34 ms, of
which `importlib.resources.files` alone 0.8 ms; the three IPC reference files 1.99 ms (reading through a Python file object:
the first file 1.3 ms, the next two 0.4 ms each, against 0.47 / 0.13 / 0.06 ms from memory); registering them 0.12 ms; the
JSON Schema check 1.4 ms (it loads and parses `generation-schema-v1.json` first, again through `importlib.resources`) and the
build of the schema 0.45 ms; then the engine's `copy.deepcopy` of it 1.3 ms. Total 5.5 ms + 1.6 ms.
`floor.py`: hr one row per table 32.4 ms, of which 24.0 ms is nine files and the first call of everything.

| Domain, rows | round-3 tree: load / Engine / generate+write / total (ms) | final tree: load / Engine / generate+write / total (ms) |
|---|---|---|
| hr, one row per table | 6.8 / 1.6 / 24.0 / **32.4** | 2.5 / 0.5 / 19.3 / **22.3** |
| hr, medium | 6.3 / 1.5 / 58.8 / **66.7** | 2.7 / 0.5 / 48.4 / **51.6** |
| manufacturing, one row per table | 6.3 / 1.6 / 19.3 / **27.1** | 2.7 / 0.4 / 19.8 / **22.8** |
| manufacturing, medium | 6.3 / 1.6 / 43.5 / **51.4** | 2.5 / 0.5 / 39.0 / **42.0** |
| real_estate, one row per table | 8.5 / 1.8 / 25.4 / **35.6** | 3.3 / 0.4 / 20.9 / **24.7** |
| real_estate, medium | 9.1 / 1.7 / 81.1 / **91.9** | 3.5 / 0.4 / 71.0 / **74.9** |
| insurance, one row per table | 8.4 / 1.7 / 22.9 / **32.9** | 3.3 / 0.4 / 19.0 / **22.7** |
| insurance, medium | 8.3 / 1.6 / 83.8 / **93.8** | 3.3 / 0.5 / 67.3 / **71.0** |

(The three small domains lose 3.8 to 5.2 ms of load and 1.1 to 1.3 ms of engine set-up; real_estate and insurance have more
reference files, so more to gain.)

**One cold run, by thread (final tree, `timeline.py`, with the cost of the stamps in it).** hr (66 ms with the stamps, 50 ms
without): the calling thread makes `position` (80 rows), `department` and `employee` (5,000 rows) from 3 to 16 ms, because
nothing else is ready (every other table points at `employee`) and the work is below the 100,000 cells at which workers start;
then `time_off_request` (25,000 rows, one chunk) is the longest chain to 35 ms and `performance_review` ends the generation at
57 ms; the writer tail after the last chunk is 4.6 ms. insurance (83 ms with the stamps): `agent` -> `policyholder` (10,000
rows, 11 ms) -> `policy` (18,000 rows, 11 ms) -> `premium_payment`, `coverage`, `underwriting` (one chunk of 18,000 rows, 22 ms)
-> `claim_payment`; for the first 33 ms one or two threads work. education: `student` (20,000 rows, 25 ms) is the first chain.
`docs/plans/evidence/P6-01-perf-r4/profile/timeline_*.txt`.

**CPU and faults of a run (final tree, five runs each, `ru.py`).** hr: wall 50-56 ms, user 67-89 ms, system 9-32 ms, about
3,900-4,060 minor page faults; insurance: wall 67-79 ms, user 102-140 ms, system 25-56 ms, 6,400-6,800 faults. So about a
quarter of a small run's CPU is the kernel (page faults at 3.3 us each when the VM is idle, `futex` and `sched_yield` calls
of idle workers), the generation is a threaded run that is **not** faster than one thread for the smallest domains (warm, in
one process: hr generation 22 ms on one thread, 24 ms with the scheduler's threads), and the cold start costs about 12 ms
over a warm second run in the same process.

**The cost of a column (hr/insurance/telecom; `colprof.py` for the fixed cost at 10 rows, round 3's `strat_bench.py` per row
at the real chunk size).** The fixed cost of a call is the Python around the work (the context, the stream key, the strategy
lookup, Arrow and numpy conversions, the kernel call) and was 60-290 us for the commonest strategies; after the changes of
section 3 it is 7-84 us:

| Column (strategy) | round-3 tree, us | final tree, us |
|---|---|---|
| employee.employment_status (weighted_enum) | 63 | 20 |
| employee.email (faker e-mail) | 289 | 84 |
| employee.phone (faker phone number) | 177 | 37 |
| employee.hire_date (temporal, ns) | 152 | 23 |
| employee.department_id (foreign_key, zipf) | 60 | 17 |
| employee.employee_id (sequence) | 13 | 7 |
| compensation.base_salary (log_normal) | 58 | 32 |
| claim.filing_date (derived add_days, parent date) | 620 | 444 |

and per row at 50,000 and 65,536 rows (warm, one column at a time):

| Column (strategy) | round-3 tree, ns/row | final tree, ns/row |
|---|---|---|
| premium_payment.payment_method (weighted_enum) | 36.9 | 32.1 |
| premium_payment.amount (distribution log_normal) | 34.0 | 24.5 |
| premium_payment.payment_date (temporal uniform) | 28.4 | 10.3 |
| premium_payment.status (weighted_enum) | 27.9 | 26.6 |
| premium_payment.policy_id (foreign_key) | 19.2 | 10.6 |
| premium_payment.payment_id (sequence) | 3.8 | 4.4 |
| usage_record.destination (faker phone_number) | 107.3 | 95.3 |
| usage_record.data_mb (distribution log_normal) | 35.9 | 27.8 |
| usage_record.duration_seconds (distribution log_normal) | 31.7 | 22.9 |
| usage_record.record_type (weighted_enum) | 29.3 | 25.8 |
| usage_record.record_date (temporal uniform) | 26.2 | 10.1 |
| usage_record.line_id (foreign_key) | 19.7 | 11.6 |
| usage_record.record_id (sequence) | 4.2 | 4.4 |

(`sequence` is as cheap per row as before, 4 ns; what changed is its 13 -> 7 us per call.)

**What a warm single-thread run is made of (round-3 tree, healthcare, insurance via cProfile).** Healthcare: the encoder is
52% (204 ms in `finish` and 123 ms in `write_batch` of 629 ms, with `SHAPE_THREADS=1`), generation 37%
(231 ms), of which the native calls are about half; the per-column Python is flat (no function above 2% of the generation).
With threads the encoder runs on the kernel's pool beside the generation: the run is CPU-bound on 4 vCPUs at about 2.5 busy
cores and 3.5 s of CPU for the large domains. A native Philox draw costs 5 ns per word (two words per alias draw, one per
uniform, five per timestamp), a string gather 9 ns per row, `exp` 6 ns, the Box-Muller normal 10 ns; those are the floor of
the draw strategies given that every value must stay the same.

## 3. What changed (all values unchanged)

**Lever 1: the fixed cost of the path.**

1. **A packaged domain schema is not checked again on every load.** `shape_domains/_digests.py` records the SHA-256 of every
   packaged `schema*.json` (28 files) and of `generation-schema-v1.json`; `scripts/update_domain_digests.py` writes it and
   refuses to record a schema that fails the JSON Schema check or does not build; `plugins/shape-domains/tests/test_digests.py`
   fails when the file is out of date, and shows that changed content, or another JSON Schema, brings the check back. The
   plugin tells the host through a new optional field, `DomainDefinition.validated` (default `False`, additive to plugin API
   v1, `docs/plugins/api-v1.md` regenerated); `load_domain` passes it to `GenSchema.from_dict(document, validated=...)`, which
   then skips the check and copies the generators without a JSON round trip. Anything else (a third-party plugin that does
   not set it, `from_dict` called by hand, a `.shape` fit, a DDL import) is checked as before; tests count the calls to
   `schema_problems`. The digests are in the wheel (`check_user_facing --wheel` clean) and equal the files inside it.
2. **Plain file reads** for the schema, the JSON Schema, the provider pools and the reference tables (`importlib.resources`
   is the fallback for a zipped install); the small Arrow reference files are read into memory and opened from a buffer
   (3-4 times faster than through a Python file object), the large ones are memory-mapped.
3. **`GenSchema.clone()`** (a structural copy of JSON values and of the module's dataclasses) replaces the engine's
   `copy.deepcopy`: 1.3 ms to 0.2 ms; a test checks that nothing mutable is shared with the original for all 28 packaged
   schemas.
4. **Python per column and per chunk:** the column order, the strategy lookup and the Philox streams of a column are made once
   (an engine's, and an LRU of the stream keys) instead of per chunk; `temporal` parses its range once and builds
   `timestamp[us]`/`[ns]` from int64 buffers (no checked cast, which was 18 us per call); `arrowkit.array` sends a plain
   int64/float64 array straight to its buffer; the longest-string check of `max_length` no longer goes through
   `numpy.diff`.

**Lever 2: fused native passes** (each with a Python twin, Rust tests and differential tests; `docs/GENERATION_KERNEL.md`):

| Kernel function (new) | Replaces | Used by |
|---|---|---|
| `uniform_index(..., start, step)` | uniform draw, `u * size`, `astype`, `minimum` (and `start + index * step`) | pool and integer providers, uniform foreign keys and temporal columns, `reference_data` |
| `pool_pick` | `uniform_index` + `pool_take` | first/last name, company, city, sentence, state |
| `alias_pool`, `alias_values` | `alias_sample` + gather (`pool_take`, numpy) | `weighted_enum` |
| `compose_strings` | the draws, the gathers and `template_strings` of a provider | e-mail, phone number, ssn, ipv4, postcode, zip+4, street address, uri, company e-mail, name |
| `lognormal_values` | `philox_normal`, `mu + sigma * z`, `exp`, `maximum`, `minimum`, `round` | `distribution` log_normal, `derived` days |
| `range_values`, `zipf_draw(..., start, step)` | `arange`, `start + index * step`, the key pool's `take` | `sequence`, zipf and uniform keys over a sequence key |

`pool_take` copies an entry of up to 16 bytes as one fixed-size block (15 -> 9 ns per row for names and enum labels).
`lognormal_values` calls numpy's own `exp` loop (the kernel already had a validated way to call it without the GIL, from the
profiler lane), so its values are numpy's bit for bit; when that loop cannot be called it returns `None` and the strategy does
the numpy steps, and a negative or very large `scale` takes the numpy route too. `compose_strings` writes a `slug` column only
for ASCII text (the Python side applies the Unicode lower-casing itself to anything else, as before).

## 4. Measured and not adopted (A/B of fresh processes, medians of 14, none of them changes a value)

| Idea | Result |
|---|---|
| `SHAPE_THREADS` for generation: default / 1 / 2 / 3 | hr 63.0 / 78.0 / 63.4 / 73.4 ms; insurance 92.1 / 140.3 / 89.4 / 96.1; education 96.4 / 151.3 / 92.2 / 94.2: two threads do what four do, because the Python glue holds the GIL |
| Rayon pool 4 / 3 / 2 / 1 threads (`RAYON_NUM_THREADS`) | hr 61.2 / 59.1 / **52.4** / 62.7; insurance 90.1 / 89.1 / 89.9 / 122.2; education 93.7 / 96.6 / **103.7** / 137.5: one setting does not fit all |
| Keep the GIL for kernel calls under N rows (a `detach` threshold of 4,096 / 20,000 / always) | hr 60.2 / 66.3 / 76.4 ms against 63.3 with release: no gain; releasing is what lets the native parts overlap |
| GIL switch interval 5 ms / 1 ms / 200 us / 50 us | hr 55.5 / 59.0 / 59.2 / 54.7: nothing |
| Smaller parallel chunks (minimum 32,768 / 8,192 / 4,096 / 2,048 rows) | hr 59.4 / 87.6 / 107.9 / 128.7 ms: an extra chunk costs about 0.7 ms of glue on one thread and 1.6-1.9 ms with the scheduler's threads |
| Smaller Parquet row groups (262,144 / 131,072 / 65,536 / 32,768 rows) | healthcare 388 / 378 / 409 / 436 ms; financial 379 / 397 / 393 / 415; pulse 259 / 248 / 260 / 265: no gain |
| Tables that need only the parent's sequence key start before the parent ends (hard to soft dependency) | hr 53.7 -> 59.7, insurance 82.4 -> 78.8, education 84.9 -> 87.9, real_estate 78.4 -> 80.6 ms: concurrency does not help a GIL-bound run |
| glibc arenas 1 / 2 / 4 on top of the tuned thresholds | hr 54.1 / 52.4 / 54.3 against 52.7 default; insurance 79.2 / 74.4 / 72.0 against 74.7; education 89.2 / 86.2 / 88.3 against 81.7: nothing |
| Four Philox blocks per step in the kernel (`cipher4`) | 4.96 -> 4.87 ns per word: the scalar loop already overlaps the blocks; removed |
| Two-digit table for integers in string assembly | no change from the divide loop (3 `write_int` 31 -> 36 ns noise); reverted |
| Null masks as a validity buffer instead of `if_else` | would change the bytes under the null slots, so the table digests would differ; not done |

## 5. Why 10x is still missed, and what would move it

10x of this sitting's pooled baseline median is the target; "floor" is `floor.py`'s one row per table (load, engine, nine
cold one-row tables), "gap" is the pooled Shape time minus the target.

| Domain | pooled baseline (s) | 10x target (ms) | Shape pooled (ms) | gap (ms) | what the profile says |
|---|---|---|---|---|---|
| hr | 0.459 | 46 | 55 | 9 | floor 22 ms (was 32), generation 22 ms on one thread whatever the threads, a serial prefix of 14 ms (`position` -> `department` -> `employee`, one chunk, cold) and a chain `employee` -> `time_off_request` (25,000 rows in one chunk, 17 ms), tail 5 ms |
| manufacturing | 0.321 | 32 | 43 | 11 | floor 23 ms (was 27): load 2.7, engine 0.4, the rest nine cold one-row tables; the data is only 50,520 rows |
| supply_chain | 0.796 | 80 | 105 | 25 | (baseline pooled 0.80 s, but 0.68-1.07 s by set) 210,450 rows in 12 tables, one-chunk tables on the longest chain |
| pulse | 2.292 | 229 | 254 | 25 | `trip` (500,000 rows x 13 columns) is half the run: encoder 35-45% of the CPU, rule repair per chunk, formula and correlated columns in numpy |
| real_estate | 0.718 | 72 | 81 | 9 | floor 25 ms, nine reference files |
| capital_markets | 1.347 | 135 | 160 | 25 | `trade` log-normal columns (Box-Muller 10 ns + exp 6 ns + two Philox words 10 ns per value), zipf key, encoder |

What bounds them, in the order of its size on the small and mid domains: (1) **the cold start of a fresh process**, about 12 ms
over a warm second run (first calls, 4,000 minor faults at 3.3 us, thread and writer creation), which no longer depends on
the work done; (2) **Python glue held under the GIL**: per column and chunk 7-84 us now, per chunk another 0.7 ms
single-threaded and 1-1.5 ms more when threads hand work over; threads beyond two bring nothing; (3) **the Parquet encoder**,
35-50% of the CPU of the larger domains and fixed by T-17 (snappy, dictionary, statistics) - the native writer already runs
it on the kernel's pool; (4) **the draws themselves**: Philox costs 5 ns per word and the string gather 9 ns per row, both
the floor for these exact values; (5) **a serial chain of single-chunk tables** (hr, insurance, education): letting the next
table start early, or cutting tables into smaller chunks, was measured and is slower (section 4). A native generation core
that makes a whole chunk of a table in one call (every column of it, with the null masks, the rules and the hand-over to the
writer) would remove (2) and most of (1)'s first calls; it is the multi-week change the engine lane named, and the fusions of
this lane show its size: each fused strategy gave 20-80% on its columns and 5-10% on a domain.

## 6. For the owner

* **API addition:** `DomainDefinition.validated` (optional, default `False`; plugin API v1 stays compatible). It lets a plugin
  say that its schema was checked against `generation-schema-v1.json` already; the host trusts it (plugins are trusted code, D-09).
  Shape's own domains set it only when the content digest of the schema file (and of the JSON Schema) is the one recorded by
  `scripts/update_domain_digests.py`; the plugin test fails when the record is stale. Say if you would rather keep the check
  on for every load: it costs 1.4-2 ms per load of a packaged domain.
* **Kernel contract:** seven new functions and the `start`/`step` of `uniform_index` and `zipf_draw` in
  `docs/GENERATION_KERNEL.md` (the twins and the differential tests are there). `lognormal_values` relies on numpy's own `exp`
  loop being callable by the kernel (`SHAPE_NUMPY_LOOPS=python` or an unsupported numpy turns it off and the strategy falls back to
  numpy's steps, with the same values).
* **The gate:** insurance 11.5x and marketing 10.9x now count; iot, financial, healthcare and education are at or above 10x but
  their timed output has the accepted clause-(h) misses, so by the gate as written they do not count (your call); pulse 9.0x,
  hr 8.3x, supply_chain 7.6x, real_estate, capital_markets and manufacturing (7.5x-8.9x) are below. The ratios move by
  up to 30% between sets on this VM because the baseline's own time does (table B); the Shape-only numbers of 1.3 do not.
* **Decisions still open:** (a) a native generation core as work packages (section 5); (b) whether the sub-0.2 s workloads
  should be judged at large (retail large is 32.4x; the other domains were not timed at large); (c) the engine lane's
  T-17/T-02 deviation for the native Parquet writer and the missing crate notices (that lane's status file) are unchanged.

## 7. Checks (final tree)

| Check | Result |
|---|---|
| `ruff check` / `ruff format --check` (src tests plugins benchmarks/vs_spindle) | all passed / 750 files formatted |
| `mypy` (strict) | no issues, 309 source files |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | exit 0 |
| `lint-imports` | 1 kept, 0 broken |
| `scripts/check_user_facing.py`, and `--wheel` on the built `sqllocks_shape-0.9.0` and `sqllocks_shape_domains-0.9.0` wheels | clean, clean |
| `bandit -q -r src -ll` | exit 0 |
| `check_requirements`, `check_secrets`, `check_plugin_skeletons`, `check_conformance_coverage` | OK (89 requirements; 7 distributions; 32 of 32) |
| `cargo fmt --check`, `cargo clippy --release --all-targets -- -D warnings`, `cargo test --release` | clean, clean, 43 passed |
| START (`shape version`, 11 runs) | median 96 ms (min 88, max 115); gate 300 ms |
| `benchmarks/vs_spindle/strategy_1to1/baseline.py`, `baseline_p404b.py`, `relational_baseline.py` with `--check` | every strategy `match`, exit 0 (all three) |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric` | 5,371 passed and 1 failed at the first run: `test_every_skeleton_builds_a_pure_wheel` found the `plugins/shape-domains/build` directory that my own manual wheel build had left in the tree; removed, the file passes (7 passed) |
| `SHAPE_KERNEL=python`, same selection | 5,372 passed, 46 deselected |
| `pytest -m heavy --ignore=tests/demo/fabric` | 42 passed |
| `pytest plugins/shape-domains` | 6 passed |
| `python -m shape.plugins.kit sqllocks-shape-domains` | OK, 14 plugins conform to plugin API 1.0 |
| Profile parity (`profile_1to1`) | not run: no profile or shared Arrow code was touched except `arrowkit.array`'s fast path for plain int64/float64 arrays, which `tests/generation/test_arrowkit.py` (54) checks against `pyarrow.array` for both with and without `from_pandas` |
| Merge | `origin/build/main-plan` merged (one conflict, `CHANGELOG.md`, both sides kept); no Rust change came in |

## 8. Tests and evidence

New tests: `tests/kernel/test_fused_kernel.py` (107; both kernels), `tests/generation/test_schema_clone.py` (45),
`test_distribution_fused.py` (102), `test_temporal_fast_units.py` (59), `test_derived_fused.py` (49),
`test_range_keys_fused.py` (5), `plugins/shape-domains/tests/test_digests.py` (4); Rust tests for `compose`, the uniform index
and the gather; the docs tests (`test_generation_docs.py`, `test_api_v1.py`) cover the new documentation.

Evidence: `docs/plans/evidence/P6-01-perf-r4/` (`verify/`, `before*/`, `after*/`, `retail/`, `ab/`, `profile/`, the table
digests, `tools/` with its README and the two driver scripts). Scripts and code of this lane: `scripts/update_domain_digests.py`,
`benchmarks/vs_spindle/domain_1to1/compare_trees.py` (`--old` with several directories).
