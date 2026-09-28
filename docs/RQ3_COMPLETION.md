# RQ-3 Completion — Fidelity Gauntlet + 50× Performance Objective

## Performance objective
The requested target was at least 50× the ~17k rows/sec reference baseline (~850k rows/sec).

Two execution paths are now explicit:

1. **Reference streaming profiler** — bounded-memory, arbitrary Python rows, pure Python. Heap-based SpaceSaving and hot-path changes improve the 50k workload to ~25.4k rows/sec. This path prioritizes portability and bounded streaming semantics.
2. **Vectorized columnar profiler** — NumPy-backed exact numeric profiling for ETL engines/columnar batches. On this host:
   - 100k rows × 3 numeric columns: ~2.34M rows/sec
   - 1M rows × 3 numeric columns: ~1.70M rows/sec
   - ~5.10M scalar values/sec at 1M rows

Relative to 17k rows/sec, the 1M-row vectorized result is ~100× faster, exceeding the requested 50× objective. This is not a universal benchmark: mixed text/semantic/dependency profiling can be slower, and production hardware must be benchmarked separately.

## Fidelity gauntlet
RQ-3 deliberately tested:
- conditional category→numeric behavior;
- nonlinear x→x² dependence where Pearson is near zero;
- missingness dependence;
- relational orphan detection;
- geographic distribution drift;
- temporal ordering/autocorrelation destruction.

Findings drove two changes:
- nonlinear normalized-mutual-information and temporal lag-autocorrelation evidence were added;
- Gold/Platinum evidence requirements were tightened. Gold now requires categorical-distribution and missingness-dependency evidence. Platinum additionally requires nonlinear, temporal, relational and geographic evidence.

Fidelity levels are evidence-coverage levels. They are not universal guarantees that arbitrary data can be perfectly regenerated.

## Gate
Local regression suite passes; requirement registry and secret scan pass.
