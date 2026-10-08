# FINDING (lead, 2026-09-30): bounded mode's memory grows with rows on the Python kernel

`tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows` fails with
`SHAPE_KERNEL=python` (the Rust kernel passes; CI runs the heavy test with Rust only):

    SHAPE_KERNEL=python pytest ... at a679619
    AssertionError: {24000000: 570245120, 48000000: 956891136}   # peak VmHWM, +68% (limit 10%)

The pure-Python wheel (T-29) runs this kernel, so bounded mode must hold there too. P1-14's
acceptance ("the suite is green in both kernel modes") and G1 need it fixed; never relax the test.

## Measurements (lead, SHAPE_KERNEL=python, 3-column CSV from the test's generator)
| rows | peak VmHWM | VmRSS after | Python blocks after | Arrow pool max |
|---|---|---|---|---|
| 2M | 296 MB | 235 MB | 283,561 | 71 MB |
| 6M | 391 MB | 232 MB | 283,684 | 160 MB |
| 24M | 544 MB | - | - | - |
| 48M | 913 MB | - | - | - |

- Nothing is retained: RSS and Python block counts after the run are flat.
- Kernel state is bounded by inspection: `_Tracker` (HLL p=14 + SpaceSaving 64), `KLL(200)`
  (bounded mode never fills `values`), text lengths/patterns, temporal histograms.
- A 5-second trace of one 6M-row run shows Arrow's live bytes (`pa.total_allocated_bytes()`)
  in a sawtooth: they climb to about 140 MB, drain slowly while the Python kernel consumes
  batches, then jump by about 40 MB and drain again, reaching 5 MB at the end. RSS follows
  Arrow (308 MB at t=20 s, 163 MB at the end). So batches the reader has already produced
  are held while the slow consumer works through them.
- Tried: `use_threads=False` for the bounded CSV reader on the Python kernel. Arrow's pool max
  at 6M was still 149 MB, so the threaded read-ahead alone is not it. Reverted.

## Where to look next
What holds already-read batches in the bounded CSV path (`io/readers.py::open_batches`:
`(b for raw in reader for b in _slice(raw, size))`, `engine.profile_table`), and whether the
held amount scales with file size (bursts at 24M/48M rows). Reproduce with a timeline probe
at 12M and 24M rows before changing code; fix; then run the heavy test with
`SHAPE_KERNEL=python` at both sizes.

## Resolved (builder, 2026-09-30)
Cause: `finalize` expanded the text-length histogram to one float per row. Fixed in 9c0f75b; the test passes with `SHAPE_KERNEL=python` unchanged. See the decision log (§2.3).
