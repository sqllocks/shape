# RQ-1 Source Audit

Audited all 107 Python source files (2,247 source lines in the 1.0 baseline before RQ additions), covering 246 functions and 117 classes.

Static findings:
- no bare `except` handlers;
- no mutable list/dict/set function defaults;
- six placeholder-pattern matches were reviewed: four are intentional exception/abstract-class bodies, one is a deliberate ignored date-parse failure, and one is the Strategy abstract `NotImplementedError`; none is an unfinished TODO/FIXME.
- core capture was changed to a row-at-a-time bounded path so batches no longer construct per-column value lists.
- approximate profiler outputs now carry algorithm/error metadata.

Important finding: reference KLL quantiles are merge-order sensitive. The RQ harness observed a q50 delta of 1.7 on a deliberately permuted merge test while mean delta was ~5.68e-14 and HLL distinct delta was 0. This is therefore treated as approximate evidence, not deterministic exact evidence. The 1.0 contract must not promise merge-order-identical quantiles.

This audit is an implementation review, not an independent security audit.
