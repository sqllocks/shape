# Benchmark methodology

Performance claims are evidence from named qualification scripts, not universal hardware promises.

For GA generation qualification use `rq/ga_worst_case_generation.py`. It simultaneously exercises coherent addresses, latitude/longitude, LocationScope, composite PK/FK relationships, joint numeric dependence, temporal generation, scenarios and classification policy. It uses bounded 1M-row chunks and reports deterministic digests, peak RSS and rows/sec.

The accepted GA gate is >=1,000,000 generated rows/sec median for 1M and 10M workloads on the qualification environment. Larger 100M/1B runs are release-hardware endurance evidence and must not be inferred from shorter tests.

Profiling benchmarks and connector qualification have separate scripts under `rq/`.
