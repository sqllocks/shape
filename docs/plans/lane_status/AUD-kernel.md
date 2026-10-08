# AUD-kernel — audit and fix lane (kernels, Rust, scale)

Branch `lane/AUD-kernel`, from `origin/build/main-plan` at `5c91ea5`.

Area: `src/shape/kernel/**`, `src/shape/_kernel.pyi`, `rust/**`, `src/shape/scale/**`,
`tests/kernel/**`, `tests/scale/**`, `docs/GENERATION_KERNEL.md`, `docs/SCALE.md`.

## Baseline (before any change)

`pytest -q tests/kernel tests/scale --cov=shape.kernel --cov=shape.scale --cov-report=term-missing`:
397 passed, total coverage 93% (lowest: `scale/chunk_worker.py` 29% — it runs in spawned
workers, `scale/api.py` 79%, `kernel/reference/sketch.py` 81%).

## Status

All defects filed; every one fixed except G1 (#547), which needs a decision (below). One
defect outside the area was filed only: #558. No `.github/workflows/*` change is needed.

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

## Fixes

Each fix is a failing regression test commit (its failing output is in the commit message), then
the fix commit. All are pushed to `lane/AUD-kernel`.

| Issue | Finding | Test commit | Fix commit |
|---|---|---|---|
| #482 | S1 | 691eea3 | b58f926 |
| #483 | S2 | 691829f | d4b3bdb |
| #484, #485 | S3, S4 | 5b5e140 | cd06a0c |
| #509 | S11 | 4c15c00 | fbb67ab |
| #531 | K1 | e7d5b14 | b87b7f5 |
| #486 | S5 | 878fc71 | 2545e80 |
| #487 | S6 | a271b7c | 233e177 |
| #532 | K2 | 637858b | 3fa4d91 |
| #488 | S7 | 9efacbe | 7072edb |
| #534 | K3 | 2bd97ed | 95600f4 |
| #536 | K4 | eb0cf51 | e03c6df |
| #538, #540 | K6, K7 | 50b740d | c7b53da |
| #549 | G3 | 334c6f1 | 2d11b12 |
| #548 | G2 | 517c33e | 5acb285 |
| #490 | S8 | bec3159 | 09dd77c |
| #491 | S9 | 8dfeb97 | 763667d |
| #288 | S10 | 0ddd336 | 7027fcc |
| #553 | K15 | 96cbf20 | 56413b4 |
| #550, #551 | G10, G7 | f06de82, 26180f4 | 46302d4 |
| #552 | K9-K14 | 5226a49 | a634582 |

Improvements (behaviour unchanged): dead code removed from `fit.rs` and `gen/temporal.rs`
(c5421a6); tests for `scale/api.py`'s request checks (ca3e952).

Notes on the fixes:

- New bounds, documented in `docs/GENERATION_KERNEL.md`: one kernel call makes at most 2**31
  rows, words or days ("use smaller chunks"); the Philox stream ends at 2**64 words; template pad
  widths are at most 1024; above a std of 10,000 hours the hour weights are uniform; sampled days
  must stay in the int64-microsecond range. Engine calls are per chunk, far below these.
- #548's test and #551's hang test run the calls in a child process, so a regression fails the
  test instead of aborting or hanging the session.
- Two #549 test cases were corrected in this lane before or with their fix (my own mistakes: a
  key that lies inside the sequence, and a call that is the #548 size case). No pre-existing test
  was changed.

## Left open, for the lead

- **#547 (G1, medium) — needs a decision.** `string_case` differs between the native kernel and
  the twin for non-ASCII text. There are two causes. The word rule differs: Rust
  `char::is_alphanumeric` is Alphabetic|Numeric, while Python `str.isalnum` is categories L*/N*.
  The case tables also differ: Rust uses Unicode 16, while Python 3.11 uses 14.0, so the twin's
  result also depends on the Python version.

  Making the two agree needs one of these:
  - a Unicode general-category table on the Rust side, which is a new crate, against the T-02
    pinned set;
  - a narrower contract, for example ASCII-only case mapping. That changes generated bytes for
    non-ASCII pools, which the T-21 verifiers compare.

  This lane may not decide either.
- **K5 — escalation (T-13).** Exact mode keys timestamps after flooring them to µs. As a result,
  `timestamp[ns]` `[1, 2, 999]` reports `distinct 1.0` with `distinct_exact True`, and min = max = 0,
  in both kernels. The question is whether T-13's µs normalisation is meant to apply to exact
  statistics.
- Low, recorded and not changed:
  - K8: hashes of integral floats outside int64/uint64, and of Python ints beyond 128 bits.
    Changing them would change persisted sketch hashes.
  - K12: decimal-to-float conversion at scale 37 is off by an ulp, within tolerance.
  - G11: `fit_distribution` re-sampling. Every caller pre-samples.
  - G13/G14: native `OverflowError` messages do not name the argument.
  - Sign of a zero `min` in the profile (`-0.0` vs `0.0`).
- **#558 (outside the area, filed only).** `tests/security/test_credential_refs.py::
  test_core_imports_no_cloud_sdk_to_resolve_references` checks the whole `sys.modules`. It
  therefore fails in a full run once the Azure SDK is installed, as it is for the Fabric demo
  tests.

## Environment notes

- `tests/demo/fabric/requirements.txt` pins `fabric-user-data-functions`, which downgrades pyarrow
  to 19.x. Under 19.x, two hashing tests (float16) and one iss_gaps test fail because of pyarrow
  itself. CI runs the main suite on the newest pyarrow and the Fabric demo on 19.x, so the suites
  below ran on pyarrow 25.0.1, with unixODBC installed (§6.2.7).
- The RefEngine baseline was set up with `benchmarks/vs_refengine/setup_refengine.sh`. `$REFENGINE_ROOT`
  was not modified.

## Commands and results (this session)

```text
ruff check src tests plugins benchmarks/vs_refengine          All checks passed!
ruff format --check src tests plugins benchmarks/vs_refengine 1090 files already formatted
mypy                                                         no issues in 436 source files
check_requirements / check_secrets / check_user_facing / check_shipped_data /
check_plugin_skeletons / check_conformance_coverage          all OK; vulture clean; lint-imports 1 kept, 0 broken
cargo fmt --check; cargo clippy --all-targets -D warnings    clean
cargo test                                                   34 passed
pytest tests/kernel (SHAPE_KERNEL=rust)                      350 passed
pytest tests/kernel (SHAPE_KERNEL=python)                    350 passed
pytest tests/scale                                           159 passed (scale coverage 93%)
"$REFENGINE_PY" benchmarks/vs_refengine/scale_1to1/verify.py     PASS local_single, local_mp; exit 0
  ... --negative-control                                     all 5 controls flagged; exit 0
profile_1to1/verify.py --impl shape d1.csv d1.parquet d2.csv d2.parquet mt   exit 0
  same, SHAPE_KERNEL=python (d1.csv d1.parquet d2.parquet mt)                exit 0
stream_prof/verify.py                                        stream == batch PASS; identical across processes PASS; exit 0
pytest -m "not emulator and not live" (SHAPE_KERNEL=rust)   7155 passed, 17 skipped, 1 failed (#558, outside the area)
pytest -m "not emulator and not live and not heavy" (SHAPE_KERNEL=python)
                                                             7113 passed, 17 skipped, 1 failed (#558)
  (heavy is excluded under the twin, as in CI: test_bounded_mode_memory_does_not_grow_with_rows
   profiles 48M rows in pure Python and does not finish in the time limit; CI runs heavy tests
   only with the native kernel)
