# Streaming Performance Contract

Shape's high-throughput streaming unit is a columnar microbatch, not an individual Python object callback.

A benchmark claim MUST identify transport, serialization, batch size, columns/types, evidence enabled, checkpoint policy, window policy, dedupe policy, row count/duration, host and peak memory.

Local reference target: >=1M rows/sec sustained for profiling + drift on qualified columnar workloads. RQ-6 measured ~5.19M rows/sec locally.

Networked managed-service throughput remains unqualified until live connector tests are performed.
