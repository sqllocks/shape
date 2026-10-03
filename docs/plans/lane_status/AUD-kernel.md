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
| S10 | low | #288 (existing) | `scale/sinks/parquet.py`, `scale/chunk_worker.py` | Fixed temp names follow planted symlinks; table directories are not checked to stay inside the base. |

Reproductions are in each issue.

### Kernel

(see below; filled in as the native/reference audit completes)
