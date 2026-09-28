# RQ-2 Completion — Profiler Performance + Statistical/Fidelity Torture

Completed locally executable RQ-2 work.

## Performance
RQ-1 baseline at 50k rows: ~17,764 rows/sec.
RQ-2 optimized 50k run: ~19,458 rows/sec (~9.5% improvement).
10k run improved from ~15,249 to ~17,890 rows/sec (~17.3%).
Optimizations reduce scalar hashing/list-allocation overhead and make ordinary fidelity certification streaming rather than materializing generated rows. This remains a pure-Python reference implementation; throughput is still a material optimization target.

## Statistical envelopes
20 deterministic 10k-item trials:
- HLL mean relative cardinality error: ~0.602%
- HLL max relative error: ~2.104%
- KLL mean absolute median error for U(0,1): ~0.00206
- KLL max absolute median error: ~0.00680

## Pathological capture corpus
Passed all-null, 10k high-cardinality strings, 99% skew, Unicode/combining characters, NaN/+Inf/-Inf, and late-column backfill cases.

## Fidelity red-team finding and correction
The original certificate did not directly compare categorical heavy-hitter distributions. A changed A/B/C distribution could evade categorical detection and fail only incidentally on another metric. RQ-2 added bounded top-k total-variation evidence to the certificate and a regression test. This is a substantive correctness fix.

## Current gate
93 tests pass. RQ-2 does not claim Gold/Platinum are universally sufficient: higher-order conditional, temporal and relational reconstruction still need explicit evidence before those labels can be treated as broad guarantees.
