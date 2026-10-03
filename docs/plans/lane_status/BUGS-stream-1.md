# BUGS-stream-1 — issues #298, #473 — status

Branch `lane/BUGS-stream-1` (from `build/main-plan`; merged `origin/build/main-plan` before
finishing: already up to date). No D-xx/T-xx decision, gate or tolerance was touched; no existing
test was changed; `.github/workflows`, §11, §2.3 and `$SPINDLE_ROOT` are untouched. #166 was not
touched. Issues were commented on, not closed.

## #473 `shape stream --json` reports elapsed 0.0 and rate 0.0 for a one-batch run — fixed

- Test first: cfcd58f. Failing output: `AssertionError: 0.0  assert 0.0 >= 0.05` (report.elapsed of a
  500-event one-batch run). Two boundary tests (two batches, zero events) passed before and after.
- Fix: d3fcb20 (`src/shape/streaming/emit/runtime.py`). The runner records when the first send began;
  with one batch, `elapsed` is that batch's delivery time and `rate` is events over it. Runs with
  two or more batches keep the old definition (paced-rate tests unchanged and green).
- Equivalence verifier: not needed. Only the report's timing fields change; no emitted bytes, no
  checkpoint bytes.

## #298 checkpoint state is zlib-decompressed with no size limit — fixed

- Test first: fde6044 (`tests/streaming/test_checkpoint_bounds.py`, 10 failed / 1 passed before).
- Fix: b167d01. `keyed._inflate` uses `zlib.decompressobj().decompress(data, max)` and refuses
  an output over the bound, an unfinished (truncated) stream, bad base64/zlib and a length that
  is not a whole number of items, all as `ValueError("corrupt checkpoint: ...")`. Bounds: keyed
  arrays = declared `live` × item size (registers × `2^hll_p`); dedupe run = `max_keys` items per
  array; window state (declares no size) = `runtime.MAX_STATE_BYTES` (1 GiB).
- Persisted formats: written bytes are unchanged, so `shape-keyed-sketches-v1`,
  `shape-dedupe-v1` and `shape-stream-window-v1` checkpoints written before the fix restore
  (compatibility test `test_checkpoints_written_before_the_bound_still_restore`). These formats
  carry `format` with a `-v1` suffix but no separate integer `version` field; adding one is a
  format change outside this bug fix, left for the lead.
- Limitation: the dedupe bound uses `max_keys` read from the same file, so a file that also
  declares a huge `max_keys` is bounded by that number.
- Equivalence verifier: not run; the fix does not change any bytes the verifiers compare (the
  encoder is unchanged, only decoding of checkpoints is bounded).

## Commands and results

| Command | Result |
|---|---|
| `ruff check` / `ruff format --check` on `src tests plugins benchmarks/vs_spindle` | clean |
| `mypy` | Success, no issues in 436 files |
| `python scripts/check_user_facing.py` | clean |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live" --ignore=tests/demo/fabric` | 6832 passed, 2 failed, 19 skipped |
| `SHAPE_KERNEL=python` same | 6832 passed, 2 failed, 19 skipped |
| `make check` | see the last section |

The 2 failures (`tests/demo_cmd/test_notebook_and_outputs.py`, "needs the shape-fabric plugin")
came from the plugin not being installed in this container; after
`pip install --no-deps -e plugins/shape-eventhubs -e plugins/shape-fabric` that file passes
(14 passed) in both kernel modes.

Not run and why: `tests/demo/fabric` (needs the `fabric` functions package, absent here; `make
check` ignores it too); 19 skips are missing optional packages (`sklearn`, `shape_databases`);
emulator and live tests (no Docker, by the plan). Nothing in `tests/demo/fabric` touches streaming.
