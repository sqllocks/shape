# RQ-1 Local Performance Baseline

Synthetic four-column streaming rows on this host:
- 10,000 rows: ~15,249 rows/sec
- 50,000 rows: ~17,764 rows/sec
- process max RSS observed: ~156,764 KB

This is a diagnostic baseline, not a production performance claim. The result identifies profiling throughput as an optimization target. The capture path itself is single-pass and no longer materializes batch-sized per-column lists, but Python sketch/update overhead dominates.
