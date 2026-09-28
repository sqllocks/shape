# RQ-2 Findings

1. **Pure Python is the performance ceiling to attack.** Small hot-path optimizations yielded measurable improvement, but a native/vectorized profiler should be evaluated before making high-throughput claims.
2. **Approximate sketches need empirical as well as theoretical metadata.** The observed HLL/KLL envelopes are retained as release evidence.
3. **Fidelity must test the evidence users care about, not only easy scalar summaries.** RQ-2 found and fixed missing categorical-distribution evidence.
4. **A high aggregate fidelity score can hide a failed important metric.** Pass/fail remains conjunctive; the score is descriptive only and must not override individual failures.
5. **Gold/Platinum remain evidence levels, not promises that every dependency in arbitrary data has been reconstructed.**
