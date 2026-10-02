# P6-01-perf-engine - engine-wide performance, round 3 (lane/P6-01-perf-engine)

Status: **built and measured; the 10x GEN-IN gate at medium is still missed for 12 of 13 non-retail domains** (nothing was
lowered, skipped or changed: no gate, tolerance, decision, seed, floor, case or test). Shape is 17-43% faster in every domain
(same-session interleaved, `old_vs_new`). On the final tree **telecom (10.83x) reaches the gate with a passing verifier**;
iot measures 10.69x but its medium verifier does not pass (a clause-(h) chance miss at seed 1042, the owner's), so that number
does not count. Retail did not regress (15.71x medium before 12.45x; large 25.76x before 24.07x; GEN-CLI 11.52x / 25.86x
before 10.50x / 25.28x).

Environment: Python 3.11.15, `~/.venvs/shape` (`pip install -e ".[dev,advanced]"` and `-e plugins/shape-domains`, Rust
kernel by maturin), pinned baseline 3.0.1 (`422e78df`) built by `setup_spindle.sh`, numpy 2.4.6 and pyarrow 25.0.1 in both
venvs. **Machine for every number here: Intel(R) Xeon(R) Processor @ 2.10GHz, 4 vCPU (KVM), 15 GiB** (`meta.cpu` of every
report), THP `always [madvise] never`. This VM is 2.10 GHz; P6-01a's was 2.80 GHz, so ratios are not comparable with its
numbers. The baseline's own spread on this VM is wide (e.g. education 0.395 to 0.719 s in one set of five), which is why the
evidence for the change itself is the same-session interleaved comparison, not the ratio of two different sittings.

"Before" is the branch as merged from P6-01a-d at the start of the session (`8b6d99c`); "after" is the final tree. Evidence:
`docs/plans/evidence/P6-01-perf-engine/` (`before/`, `after/`, `after_threads1/`, `old_vs_new/`, `retail/`, `verify/`,
`exact_equality_base_vs_final.txt`).

## What was built (all values unchanged)

1. **Native Parquet writer** (`rust/shape-kernel/src/parquet_out.rs`, `shape._kernel.ParquetOut`; arrow-rs `parquet` crate).
   pyarrow's `ParquetWriter` encodes a file on one thread and cannot be threaded (checked: no write-side `use_threads`; the
   one in the docs is for reading). The native writer collects batches into row groups and encodes every (row group, column)
   pair as a task on the kernel's rayon pool while the caller goes on; finished column chunks are appended in row-group
   order and the last one writes the footer. `finish()` returns at once, `wait()` blocks, `parallel=False` (which
   `SHAPE_THREADS=1` selects) encodes on the calling thread. `write_engine` feeds each table's batches to it straight from the
   scheduler's callbacks (no per-table writer thread or queue); a table it declines (another codec, a nested column type,
   `SHAPE_PARQUET_WRITER=pyarrow`, the pure-Python kernel) still goes through pyarrow, which is also the T-03 twin. Files read
   back equal; the bytes differ (a test shows serial and parallel native files are byte-identical to each other). Measured:
   telecom's tables encode in 112 ms instead of 350 ms with 4 cores; non-blocking close made the small domains 7-11% faster.
2. **Dependency-driven scheduler** (`src/shape/generation/scheduler.py`). A table starts when the tables it points at are
   complete (not at level barriers); ready chunks are taken longest-path-first; the calling thread runs chunks itself while the
   queued work is under about 100,000 cells (rows x columns), because worker threads cost more than they gain on small tables
   (manufacturing: 17.6 ms of generation inline against 38 ms on three workers); workers are added when the queue holds enough
   for two or more. `SHAPE_THREADS` or `chunk_rows` given explicitly still force threads (the domains lane's rule).
   `EarlyRules` is polled per table instead of joined per level. Merged with the domains lane's per-chunk rule repair: a chunk
   is generated and then repaired, aggregates sum the generated chunk, writers get the repaired one, and a cross-table repair
   waits for the table it reads.
3. **glibc thresholds** (`shape._process.tune_malloc`, once, at the first generation): mmap threshold 32 MiB, trim threshold
   256 MiB, top pad 16 MiB, set together (the trim threshold alone froze the adaptive threshold and was 29% slower on retail).
   3-10% on the medium workloads. A host that sets `MALLOC_*` or `GLIBC_TUNABLES` is left alone; `SHAPE_MEMORY_POOL=default`
   turns it off. *Behaviour note for the owner:* like the Arrow pool setting of round 2, this changes a process setting of the
   embedding application for good (documented, opt-out exists).
4. **Zipf foreign keys in one native pass** (`zipf_guide`, `zipf_draw`, with twins): the Philox draw and the `searchsorted`
   fused, through a guide table; the same rows bit for bit (tests against numpy and every boundary of the cumulative table).
   The domains lane's `cdf_search` stays for the tail of a pool beyond 2^20. Retail -5%, capital_markets -7%, pulse -5%.
5. **`Engine.cached` per key** (a key is built under its own lock; before, the engine-wide lock was held while a whole-table
   result was built, so every other worker and the scheduler waited: retail's capped Pareto key 24 ms, financial 17 ms) and a
   strategy hook `prepare(spec, ctx)`: the engine builds data-independent whole-table results on a helper thread at the start.
   The capped Pareto foreign key uses it. Retail -5%, healthcare -6%.
6. The final rule validation runs on a thread beside the copula and the last hand-overs (financial -8%).
7. Harness: `benchmarks/vs_spindle/domain_1to1/compare_trees.py` (two source trees, fresh process each, interleaved).

Docs: `docs/GENERATION_ENGINE.md`, `docs/GENERATION_KERNEL.md`, `docs/GENERATION_STRATEGIES.md`, `CHANGELOG.md`.

## Deviations that need the owner

* **T-17 names pyarrow's `ParquetWriter`; this lane's brief allows a native writer "if pyarrow cannot", and it cannot.** The
  Parquet sink now uses the kernel's writer when it can (snappy, dictionary on, chunk statistics, row groups of 262,144 rows).
  This also **adds the arrow-rs `parquet` crate (59.3, features `arrow` and `snap`) to T-02's pinned set** and grows the kernel
  from 16.5 MB to 17.8 MB (the wheel is 5.6 MB). Plan §2 and T-17 were not edited. If you want T-17 as written, setting
  `SHAPE_PARQUET_WRITER=pyarrow` as the default is a one-line change and costs most of what this lane gained (the encoder is
  35-45% of the CPU and was the serial tail).
* `THIRD_PARTY_NOTICES.md` lists no Rust crate (arrow and rayon are not in it either), so I added nothing for `parquet` (Apache-2.0)
  and its dependencies (`snap`, BSD-3; `twox-hash`, MIT; `thrift`, Apache-2.0 and others). That is a gap for the release checklist.
* The clause-(h) chance misses at seed 1042 are unchanged and with the owner (table below); I changed no seed, floor, tolerance or case.

## Equivalence first

* **Exact table equality**, every domain at small and medium (28 sets, 14 domains): the session-start code *with its own
  kernel* (git worktree of `8b6d99c`, native kernel built from it) and the final tree generate the same tables, compared with
  `Table.equals` after reading the Parquet back: **28 of 28 identical** (`exact_equality_base_vs_final.txt`; the files differ in
  bytes, the tables do not).
* **`verify.py --domain D --impl shape`, small and medium, for all 14 domains** on the final tree (reports in `verify/`). Every
  score is identical, to two decimals, to the report of the lane that built the domain, as it must be. The medium directories
  are the ones the last benchmark run of each domain wrote (the single-thread run; values do not depend on threads, and the
  exact-equality check above covers the default-thread output too).

| Domain | small | columns | medium | columns | What fails (all identical to the domain lane's record) |
|---|---|---|---|---|---|
| retail | PASS | 60/60 | PASS | 60/60 | - |
| capital_markets | PASS | 64/64 | FAIL | 64/64 | medium: (h) company: 84.58 < floor 84.96 |
| education | FAIL | 61/61 | FAIL | 61/61 | small: (h) department: 80.08 < floor 83.63; (h) enrollment: 98.82 < floor 99.16 / medium: (h) department: 80.08 < floor 83.63 |
| financial | FAIL | 77/77 | FAIL | 77/77 | small: (h) account: 95.67 < floor 95.83; (h) branch: 84.59 < floor 84.91; (h) loan: 96.95 < floor 96.98 / medium: (h) branch: 84.59 < floor 84.91 |
| healthcare | PASS | 80/80 | FAIL | 79/80 | medium: (b-e) provider.last_name: failed ['distinct_ratio'] |
| hr | PASS | 59/59 | PASS | 59/59 | - |
| insurance | FAIL | 63/63 | PASS | 63/63 | small: (h) claim: 95.42 < floor 95.46 |
| iot | PASS | 51/51 | FAIL | 51/51 | medium: (h) sensor: 99.13 < floor 99.24 |
| manufacturing | FAIL | 62/62 | FAIL | 62/62 | small: (h) production_line: 92.47 < floor 94.01; (h) work_order: 96.33 < floor 97.29 / medium: (h) production_line: 92.47 < floor 94.01 |
| marketing | FAIL | 60/60 | PASS | 60/60 | small: (h) lead: 98.06 < floor 98.40 |
| pulse | PASS | 40/40 | PASS | 40/40 | - |
| real_estate | FAIL | 66/66 | FAIL | 66/66 | small: (h) neighborhood: 72.75 < floor 74.25 / medium: (h) neighborhood: 72.75 < floor 74.25 |
| supply_chain | FAIL | 76/76 | PASS | 76/76 | small: (h) purchase_order_line: 97.65 < floor 97.67 |
| telecom | PASS | 62/62 | PASS | 62/62 | - |

Every failure but one is a clause-(h) per-table fidelity miss by 0.02 to 1.5 points at the fixed seed (`education.department`
by 3.55); the exception is healthcare medium `provider.last_name`, a distinct ratio (|dR-1| 0.029 against 0.016). All were
reported by the domain lanes (P6-01a Escalation 1, P6-01b Reports 1-2, P6-01c, P6-01d) and are the owner's. **Consequence for
the gate:** a number counts only where the verifier exits 0, so the medium ratios that count are those of retail, hr, insurance,
marketing, pulse, supply_chain and telecom; the other domains' ratios are reported but do not count.

**Correction of an error of mine during the session:** the progress log of my verifier driver printed `exit 0` for every cell
(a `$(date)` in the same `echo` reset `$?`); I read it as passes for a few minutes and said so. The verdicts above come from the
`VERDICT:` lines of the reports, not from that log.

## GEN-IN at medium, final tree (`bench.py --impl shape --domain D --scales medium --runs 5 --warmup 1`)

Each tool in a fresh process, interleaved, one discarded warm-up, imports excluded, 4 vCPU, exclusive `bench.lock`, each run
waits for a 1-minute load average <= 1.5. Right-hand columns: the same workload with `SHAPE_THREADS=1` (T-19 asks for
single-threaded Shape alongside; the native writer then encodes on the calling thread too, and the baseline is re-timed in that
sitting).

| Domain | Baseline runs (s) | Baseline median | Shape runs (s) | Shape median | Ratio | Shape 1 thread median | Ratio 1 thread |
|---|---|---|---|---|---|---|---|
| retail | 5.043, 5.246, 5.131, 5.391, 5.483 | 5.246 | 0.314, 0.334, 0.353, 0.344, 0.321 | 0.334 | **15.71x** | 0.782 | 6.85x |
| capital_markets | 0.904, 0.969, 1.021, 1.083, 1.044 | 1.021 | 0.116, 0.119, 0.125, 0.120, 0.106 | 0.119 | **8.62x** | 0.223 | 3.89x |
| education | 0.633, 0.719, 0.437, 0.395, 0.628 | 0.628 | 0.068, 0.062, 0.065, 0.066, 0.060 | 0.065 | **9.60x** | 0.112 | 5.32x |
| financial | 2.410, 1.958, 2.398, 2.401, 2.229 | 2.398 | 0.280, 0.273, 0.299, 0.266, 0.277 | 0.277 | **8.64x** | 0.633 | 4.15x |
| healthcare | 2.352, 2.365, 2.389, 2.530, 2.231 | 2.365 | 0.261, 0.268, 0.267, 0.256, 0.309 | 0.267 | **8.87x** | 0.559 | 4.15x |
| hr | 0.345, 0.307, 0.232, 0.263, 0.265 | 0.265 | 0.052, 0.046, 0.041, 0.048, 0.042 | 0.046 | **5.75x** | 0.055 | 3.90x |
| insurance | 0.457, 0.517, 0.547, 0.589, 0.474 | 0.517 | 0.062, 0.062, 0.063, 0.060, 0.066 | 0.062 | **8.30x** | 0.102 | 5.38x |
| iot | 0.468, 0.610, 0.670, 0.676, 0.716 | 0.670 | 0.062, 0.060, 0.063, 0.073, 0.063 | 0.063 | **10.69x** | 0.092 | 5.66x |
| manufacturing | 0.151, 0.227, 0.160, 0.162, 0.162 | 0.162 | 0.032, 0.031, 0.034, 0.032, 0.036 | 0.032 | **5.11x** | 0.045 | 3.52x |
| marketing | 0.904, 0.852, 0.947, 0.954, 0.989 | 0.947 | 0.112, 0.112, 0.106, 0.111, 0.108 | 0.111 | **8.51x** | 0.201 | 4.24x |
| pulse | 1.568, 1.364, 1.504, 1.517, 1.573 | 1.517 | 0.177, 0.175, 0.183, 0.183, 0.197 | 0.183 | **8.29x** | 0.377 | 4.00x |
| real_estate | 0.420, 0.383, 0.338, 0.363, 0.395 | 0.383 | 0.063, 0.065, 0.061, 0.065, 0.059 | 0.063 | **6.10x** | 0.092 | 3.44x |
| supply_chain | 0.622, 0.407, 0.511, 0.641, 0.481 | 0.511 | 0.073, 0.072, 0.074, 0.080, 0.077 | 0.074 | **6.95x** | 0.111 | 4.26x |
| telecom | 2.888, 2.785, 2.761, 2.685, 2.769 | 2.769 | 0.256, 0.251, 0.257, 0.234, 0.259 | 0.256 | **10.83x** | 0.585 | 4.90x |

(Ratios of 10x or more that count: retail and telecom. iot's 10.69x does not count, see above.)

### Before and after, Shape only (medians of five, the session's two sittings)

| Domain | Shape before: runs (s) | median | Shape after: runs (s) | median | change |
|---|---|---|---|---|---|
| retail | 0.415, 0.447, 0.404, 0.424, 0.439 | 0.424 | 0.314, 0.334, 0.353, 0.344, 0.321 | 0.334 | -21.3% |
| capital_markets | 0.161, 0.170, 0.174, 0.176, 0.171 | 0.171 | 0.116, 0.119, 0.125, 0.120, 0.106 | 0.119 | -30.9% |
| education | 0.095, 0.095, 0.096, 0.103, 0.093 | 0.095 | 0.068, 0.062, 0.065, 0.066, 0.060 | 0.065 | -31.5% |
| financial | 0.369, 0.425, 0.380, 0.381, 0.368 | 0.380 | 0.280, 0.273, 0.299, 0.266, 0.277 | 0.277 | -27.0% |
| healthcare | 0.332, 0.341, 0.313, 0.330, 0.337 | 0.332 | 0.261, 0.268, 0.267, 0.256, 0.309 | 0.267 | -19.6% |
| hr | 0.069, 0.062, 0.056, 0.057, 0.058 | 0.058 | 0.052, 0.046, 0.041, 0.048, 0.042 | 0.046 | -21.2% |
| insurance | 0.086, 0.076, 0.091, 0.081, 0.092 | 0.086 | 0.062, 0.062, 0.063, 0.060, 0.066 | 0.062 | -27.2% |
| iot | 0.094, 0.102, 0.092, 0.086, 0.098 | 0.094 | 0.062, 0.060, 0.063, 0.073, 0.063 | 0.063 | -33.5% |
| manufacturing | 0.048, 0.047, 0.045, 0.048, 0.045 | 0.047 | 0.032, 0.031, 0.034, 0.032, 0.036 | 0.032 | -32.9% |
| marketing | 0.162, 0.184, 0.167, 0.182, 0.178 | 0.178 | 0.112, 0.112, 0.106, 0.111, 0.108 | 0.111 | -37.6% |
| pulse | 0.299, 0.321, 0.315, 0.352, 0.308 | 0.315 | 0.177, 0.175, 0.183, 0.183, 0.197 | 0.183 | -41.9% |
| real_estate | 0.084, 0.084, 0.094, 0.083, 0.084 | 0.084 | 0.063, 0.065, 0.061, 0.065, 0.059 | 0.063 | -25.2% |
| supply_chain | 0.114, 0.108, 0.105, 0.105, 0.102 | 0.105 | 0.073, 0.072, 0.074, 0.080, 0.077 | 0.074 | -30.3% |
| telecom | 0.439, 0.392, 0.420, 0.404, 0.441 | 0.420 | 0.256, 0.251, 0.257, 0.234, 0.259 | 0.256 | -39.1% |

### The change itself: same sitting, old tree against new tree, 12 pairs of fresh processes per domain (`compare_trees.py`)

| Domain | old median | new median | change | old min | new min | CPU-s old | CPU-s new |
|---|---|---|---|---|---|---|---|
| retail | 414.5 ms | 322.2 ms | -22.3% | 396.8 ms | 297.3 ms | 1.057 | 0.889 |
| capital_markets | 171.1 ms | 112.3 ms | -34.4% | 159.9 ms | 97.0 ms | 0.333 | 0.284 |
| education | 95.0 ms | 66.6 ms | -29.9% | 89.4 ms | 62.2 ms | 0.182 | 0.146 |
| financial | 379.6 ms | 273.5 ms | -27.9% | 365.7 ms | 263.2 ms | 0.875 | 0.788 |
| healthcare | 332.3 ms | 276.6 ms | -16.7% | 321.6 ms | 264.4 ms | 0.785 | 0.715 |
| hr | 59.9 ms | 43.2 ms | -27.9% | 54.3 ms | 39.9 ms | 0.110 | 0.074 |
| insurance | 84.5 ms | 59.0 ms | -30.2% | 77.9 ms | 57.0 ms | 0.166 | 0.128 |
| iot | 93.7 ms | 64.4 ms | -31.2% | 84.5 ms | 61.6 ms | 0.168 | 0.126 |
| manufacturing | 48.2 ms | 35.9 ms | -25.6% | 44.4 ms | 32.5 ms | 0.082 | 0.060 |
| marketing | 166.9 ms | 109.5 ms | -34.4% | 164.2 ms | 99.8 ms | 0.347 | 0.272 |
| pulse | 314.7 ms | 179.3 ms | -43.0% | 299.6 ms | 174.6 ms | 0.533 | 0.517 |
| real_estate | 90.0 ms | 62.3 ms | -30.8% | 77.1 ms | 56.7 ms | 0.162 | 0.121 |
| supply_chain | 112.6 ms | 77.6 ms | -31.1% | 100.0 ms | 67.3 ms | 0.215 | 0.154 |
| telecom | 414.5 ms | 259.5 ms | -37.4% | 381.9 ms | 238.6 ms | 0.894 | 0.728 |

Shape's CPU-seconds fell 5-35% (pulse only 3%: its rule repair and page faults remain). Unrounded raw runs: `old_vs_new/old_vs_new_medium.json`.

### Retail (must not regress): GEN-IN medium and large, GEN-CLI medium and large, before and after

| Retail | Phase | Baseline runs (s) | Baseline median | Shape runs (s) | Shape median | Ratio |
|---|---|---|---|---|---|---|
| GEN-IN medium | before | 4.94, 5.29, 5.34, 5.64, 5.17 | 5.28 | 0.41, 0.45, 0.40, 0.42, 0.44 | 0.424 | **12.45x** |
| GEN-IN medium (re-run, final session) | before | 5.36, 5.26, 5.46, 4.96, 5.39 | 5.36 | 0.41, 0.40, 0.43, 0.46, 0.40 | 0.413 | **12.96x** |
| GEN-IN medium | after | 5.04, 5.25, 5.13, 5.39, 5.48 | 5.25 | 0.31, 0.33, 0.35, 0.34, 0.32 | 0.334 | **15.71x** |
| GEN-IN large | before | 96.73, 97.56, 98.45 | 97.56 | 4.08, 4.03, 4.05 | 4.053 | **24.07x** |
| GEN-IN large | after | 97.38, 98.20, 96.41 | 97.38 | 3.78, 3.83, 3.67 | 3.780 | **25.76x** |
| GEN-CLI medium | before | 7.12, 7.07, 7.04, 7.11, 6.96 | 7.073 | 0.67, 0.69, 0.65, 0.64, 0.69 | 0.674 | **10.50x** |
| GEN-CLI large | before | 104.08, 102.53, 101.46, 102.75, 102.09 | 102.525 | 4.18, 4.05, 4.07, 4.04, 3.98 | 4.055 | **25.28x** |
| GEN-CLI medium | after | 7.32, 6.94, 6.64, 6.71, 6.94 | 6.942 | 0.65, 0.61, 0.60, 0.58, 0.58 | 0.603 | **11.52x** |
| GEN-CLI large | after | 103.40, 113.71, 103.37, 105.59, 103.56 | 103.561 | 4.00, 3.86, 3.88, 4.11, 4.22 | 4.004 | **25.86x** |

Retail's verifier passes at small and medium (table above). The "before" rows were run with the session-start tree and its own
kernel in a worktree (`PYTHONPATH`), the "after" rows with the final tree, on the same machine; the baseline's own time varied by
3% (large) between the two sittings.

## Why the gate is still missed (profiles in this session, scripts not committed)

* **After this work Shape is CPU-bound on 4 vCPUs at 2.5-2.9 busy cores for the large workloads and 1.6-2.1 for the small ones.**
  CPU-seconds of one run: retail 0.89, telecom 0.73, financial 0.79, pulse 0.52; about 35-45% of it is Parquet encoding (snappy is
  28% of the encoder, statistics 13%, dictionary 7%: all fixed by T-17), about 15-25% of it is page-fault system time, the rest
  generation. A perfectly packed run is CPU/4 (retail 0.22 s, telecom 0.18 s); we are at 0.33 and 0.26.
* **The small domains are at a floor.** Manufacturing generates in 21-25 ms cold (load 5 ms, engine 1 ms, write tail 4-8 ms,
  total 32 ms); 10x against its 0.162 s baseline is 16 ms. Generation there is Python/numpy at 25-40 ns per cell with no fixed
  cost left in the profile (12,500 calls in 25 ms, flat). It would need the fused native generation core, below.
* **Where the next gains are, by measured share of the column time over the 14 domains (1,515 ms single-threaded):**
  `faker` 306 ms (phone numbers 83 ns/row, URIs 123, e-mail 260-290: the domains lane's providers), `foreign_key` 265 ms,
  `weighted_enum` 261 ms (two native calls, 12 of its 23 ns/row are kernel, the rest allocation), `distribution` 226 ms,
  `temporal` 101 ms, null masks 49 ms. Rule repair per chunk in pulse is 50 ms and 19,000 of its 44,000 page faults (the domains
  lane's `fix_rule`).
* **Item (3), a pandas-free native generation core, was measured and not built.** The orchestration is 2-3 ms of CPU per domain
  (`generate_chunk` minus the strategies), pandas is not imported on the generation path, and the cost is in 40 numpy/Arrow
  steps per column; fusing them means about fifteen strategies in Rust, each with its twin and the same values (T-16). Fusing
  one (the Zipf key) gave 4-7% on the domains that use it, so the whole set is a multi-week change with an uncertain total;
  it needs a decision, not a round.

## Tried and not adopted (interleaved A/B, medians of 7-15; none changes a value)

| Idea | Result |
|---|---|
| Chunk size (min rows 16k / 8k, max 64k, a width-aware split of wide tables like healthcare's `patient`) | within noise, base best or tied |
| `sys.setswitchinterval` 1 ms to 50 us | within noise |
| Reserved core 0 instead of 1 with the native encoder | equal or slower (iot +10%, marketing +5%) |
| Spawn threshold 12k..100k cells | flat; 400k +9% (retail), 1.6M +25% (marketing): 50k chosen |
| A minimum plan size for worker threads (1M..8M cells) | helps manufacturing 5%, hurts education +30%, supply_chain +20% |
| Aggregate feeding on a helper thread | no gain (retail +4%) |
| Dropping all table dependencies (child chunks start at once, parents' columns made on demand) | retail +42%, healthcare +13%, the rest equal: the DAG is not the limiter |
| glibc `hugetlb=1/2` (transparent huge pages for malloc) | +5% / +20% slower |
| Encoder `write_batch_size`, page row limit, page bytes | within 3% |
| Encoder without statistics (-13% of encode CPU) or without dictionary | not adopted: changes the files' features, outside T-17 |
| zstd, lz4 | T-17 is snappy |

## Checks (final tree)

| Check | Result |
|---|---|
| `ruff check` / `ruff format --check` (src tests plugins benchmarks/vs_spindle) | all passed / 680 files formatted |
| `mypy` (strict) | no issues, 305 files |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | exit 0 |
| `lint-imports` | 1 kept, 0 broken |
| `scripts/check_user_facing.py`, and `--wheel` on the built `sqllocks_shape-0.9.0` and `sqllocks_shape_domains-0.9.0` wheels | clean, clean |
| `bandit -q -r src -ll` | exit 0 (nosec notes only) |
| `cargo fmt --check`, `clippy --release --all-targets -D warnings`, `cargo test --release` | clean, clean, 40 passed |
| START (`shape version`, 11 runs) | median 75 ms (min 60, max 108); gate 300 ms |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric` | 4930 passed, 46 deselected |
| `SHAPE_KERNEL=python`, same selection | 4930 passed, 46 deselected |
| `pytest plugins/shape-domains` | 1 passed |
| `pytest -m heavy` | **collection fails on `tests/demo/fabric` (`nbformat` is not installed here; the same directory the other suites ignore)**; with `--ignore=tests/demo/fabric`: 42 passed, 4934 deselected |
| `profile_1to1/verify.py` (both kernels) | **not run**: no profile or shared Arrow code was touched (new kernel functions and the Parquet sink only) |

## New and changed tests

`tests/generation/test_native_parquet.py` (40: equal tables for 12 column types and many row-group shapes, order, `finish`/`wait`,
errors, nested-type and codec fallbacks, serial equals parallel byte for byte, the engine's native route, a generator failure
leaves no open writer), `test_scheduler.py` (14: order, per-table start, inline vs threads, priority, prebuilt tables,
failures, cycles), `test_zipf_fk.py` (26), `test_prepare_hook.py` (12), additions to `test_engine_parallel.py`
(per-key locks, validation thread, worker-thread counting in place of pool counting for the domains lane's four tests),
`test_process_allocator.py` (`tune_malloc`: 6, including a `mallinfo2` check), `tests/kernel/test_gen_kernel.py` (zipf pair against numpy
and the twin).

## Not done / open

* The 10x gate at medium: 12 of 13 non-retail domains still short (5.1x-9.6x; iot 10.69x without a passing verifier). The decisions
  wanted: (a) accept the T-17/T-02 change above; (b) the fused native generation core as new work packages (the domains lane's
  `faker` providers first); (c) rule on the clause-(h) misses; (d) whether the sub-0.2 s workloads should be judged at large.
* Large scale was timed for retail only (GEN-IN and GEN-CLI); the other domains' large runs and the verifier at large were not done.
* Interleaving with the baseline was not used in `compare_trees.py` (it compares Shape trees only; the baseline ratios come from
  `bench.py`).
