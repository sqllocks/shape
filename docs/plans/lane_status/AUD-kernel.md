# AUD-kernel — audit and fix lane (kernels, Rust, scale)

Branch `lane/AUD-kernel`, from `origin/build/main-plan` at `5c91ea5`.

Area: `src/shape/kernel/**`, `src/shape/_kernel.pyi`, `rust/**`, `src/shape/scale/**`,
`tests/kernel/**`, `tests/scale/**`, `docs/GENERATION_KERNEL.md`, `docs/SCALE.md`.

## Baseline (before any change)

`pytest -q tests/kernel tests/scale --cov=shape.kernel --cov=shape.scale --cov-report=term-missing`:
397 passed, total coverage 93% (lowest: `scale/chunk_worker.py` 29% — it runs in spawned
workers, `scale/api.py` 79%, `kernel/reference/sketch.py` 81%).

## Findings

Severity, file and line, reproduction, expected, actual. Issue numbers are in sqllocks/shape.

### Scale

| # | Sev | Issue | Where | Defect |
|---|---|---|---|---|
| S1 | high | #482 | `scale/sinks/memory.py:28`, `scale/sinks/__init__.py:47-60` | `--sink-config memory.max_memory_gb=0.5` stays a string, and `"0.5" * 1024**3` allocates 3 GiB before `int()` fails; other numeric settings given as strings fail with a bare `TypeError`. |
| S2 | high | #483 | `scale/sinks/parquet.py:79-85` | Parts of an earlier, larger run (and `*.tmp*` leftovers) stay in the table directory: `_COMPLETE` says 20 rows, a dataset read gives 50. |
| S3 | high | #484 | `scale/spark_worker.py:143-152` | The driver calls `engine.generate()` for the small tables, which also regenerates every executor-made table in full. |
| S4 | medium | #485 | `scale/spark_worker.py:141` | Executor-made tables report the spec's row count, not the written count, so `check_result` is vacuous for them. |
| S5 | medium | #486 | `scale/jobs.py:284-291`, `:463-468` | `shape jobs cancel` from another process marks a running local job cancelled, but the run never reads it and ends `succeeded`. |
| S6 | medium | #487 | `scale/sinks/writer.py:279-297` | A writer that returns before reading every batch hangs the run forever. |
| S7 | low | #488 | `scale/jobs.py:156-189` | One malformed job file (`{}`, `[]`) makes `JobStore.list` raise `TypeError`/`AttributeError`. |
| S8 | low | #490 | `scale/router.py:194-202` | When the run failed and a sink's `close` fails too, the close error replaces the original (a cancelled job is then recorded as failed). |
| S9 | low | #491 | `scale/chunked.py:111-135`, `:191-213` | Negative row overrides fail with `IndexError`; an override for a table not in the schema is silently ignored. |
| S11 | high | #509 | `scale/chunk_worker.py:56`, `scale/spark_worker.py:99,135` | `--processes` part files and Spark executor tables skip `Engine.finalize`: a declared `decimal(10,2)` column is written as `double` (and the Delta DDL says `double`). |
| S10 | low | #288 (existing) | `scale/sinks/parquet.py`, `scale/chunk_worker.py` | Fixed temp names follow planted symlinks; table directories are not checked to stay inside the base. |

Reproductions are in each issue.

### Kernel (native `rust/shape-kernel` vs twin `src/shape/kernel/reference`)

Every repro was run in this session against the release build (overflow wraps, it does not
panic). A `PanicException` derives from `BaseException`, so `except Exception` misses it.

| # | Sev | Issue | Where | Defect |
|---|---|---|---|---|
| K1 | high | #531 | `sketch.rs:179,210` | `Kll.update(nan)` then a sort panics (`Option::unwrap()` on `None`); reachable from `streaming.evidence.NumericEvidence`. Twin returns NaN quantiles. |
| K2 | medium | #532 | `sketch.rs:92,288`, `profile.rs:1056-1106` | `Hll.from_registers` / `ProfileState.from_snapshot` accept out-of-range registers, capacity 0, NaN KLL items; later calls panic. |
| K3 | medium | #534 | `profile.rs:30-60` | Text pattern counts differ: Rust `$` is end of text, Python `$` also before a final `\n`; Python `\s` includes `\x1c-\x1f`. |
| K4 | medium | #536 | `reference/profile.py:311` | Twin raises `OverflowError` on dates outside years 1-9999 (native profiles them). |
| K6 | medium | #538 | `reference/hashing.py:97-136` | `hash_value`: `pd.NA`, NaT, null Arrow scalars get a hash; `pd.NaT` raises `struct.error`; `datetime64[ns]` hashes unlike `hash_column`. |
| K7 | medium | #540 | `reference/hashing.py:218` | Twin `hash_array` crashes on a negative-scale decimal. |
| G1 | medium | #547 | `gen/strings.rs:220-226` | `string_case` title/upper/lower differ for non-ASCII (word rule and Unicode version). |
| G2 | medium | #548 | `gen/strings.rs:317`, `gen/*` | Unbounded sizes: `uuid4_strings` panics at 59,652,324 rows (i32 offset wrap); 2**40 rows aborts the interpreter (allocation failure); `day_weights` hangs holding the GIL. |
| G3 | medium | #549 | `gen/relational.rs:238,270`, `gen/mod.rs:49,73` | Overflow: `dense_rows` returns negative rows, `group_sums` panics, `check_slot` passes a wrapped slot then panics, word addressing aliases rows. |
| G10 | low | #550 | `gen/mod.rs:41` | Native reads nulls in int64 inputs as values; twin rejects them. |
| G7 | low | #551 | `gen/temporal.rs:98,149`, `gen/relational.rs:155` | `hour_weights_peaks` hangs on a large std, zeros/NaN on non-finite input; `temporal_sample`, `scd2_offsets` wrap. |
| K9-K14 | low | #552 | `profile.rs`, `exact.rs`, `sketch.rs` | Parity: dictionary null_count, `-0.0` min, tz in `temporal_counts`, SpaceSaving wrap, twin input checks, self-merge. |
| K15 | low | #553 | `_kernel.pyi`, `reference/__init__.py` | Stub misses 9 native functions; `lognorm_probe`, `numpy_loops_mode` have no twin; stale comments. |

Recorded, not filed as defects:

- **K5, escalation for the lead (T-13).** Exact mode keys timestamps after flooring them to µs
  (T-13 normalisation), so `timestamp[ns]` `[1, 2, 999]` reports `distinct 1.0`,
  `distinct_exact True`, min = max = 0, in both kernels. Changing it touches how T-13 applies to
  exact statistics, which this lane may not decide.
- K8 (low): integral floats below -2**63 or at/above 2**64 do not hash like the equal Python
  int, and Python ints beyond 128 bits collide. No int64/uint64 column can hold them; changing
  hashes of persisted sketches is not worth it without a decision.
- K12 (low): decimal-to-float in the profile (`10f64.powi(scale)`) is off by an ulp at scale 37
  in both kernels (differently); within the tests' tolerances.
- G11 (low): `fit_distribution` on more than 2000 values: the twin re-samples, native fits all.
  Every caller pre-samples with `sample_for_fitting`.
- G13/G14 (low): native errors for negative ints are `OverflowError: can't convert negative int
  to unsigned` without the argument's name; twin `template_strings` accepts a negative column
  index.
- G15 (improvement): dead code `fit.rs:1592 dist_from_name`, `fit.rs:1596 _LN2`,
  `temporal.rs:21 era`.
