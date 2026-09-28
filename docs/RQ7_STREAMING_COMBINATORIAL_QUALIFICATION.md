# RQ-7 — Streaming Combinatorial Qualification

## Scope
RQ-7 moves beyond the RQ-6 numeric throughput benchmark. It exercises interactions among profiling, drift, structural quality checks, history, checkpoints, privacy detection/release guards, schema evolution, geography columns, FK constraints, non-finite values, event-time windows, lateness, restart/replay, and arbitrary batch boundaries.

Testing uses targeted dangerous combinations, n-wise combinations and randomized property trials rather than claiming an impossible Cartesian enumeration of every parameter value.

## Invariants
The principal invariant is: the same logical ordered stream processed with different microbatch boundaries must produce equivalent evidence for exact vectorized metrics. 100 randomized batching trials passed.

Additional qualified properties:
- multiple injected checkpoint failure points replay without loss/duplicate in the reference simulator;
- history/checkpoint chains preserve prior Shape objects and parent linkage;
- schema additions and numeric drift are detected while FK/geographic structural constraints remain valid;
- NaN/+Inf/-Inf counts survive streaming profiling;
- sensitive-value detectors identify email-like values and near-unique summaries fail conservative release assessment;
- aggregate event-time windows retain aggregate state rather than all events;
- too-late events are rejected under watermark policy.

## Full pipeline performance
10M rows, 250k-row microbatches, six numeric/geographic/relationship columns, with:
- vectorized profiling;
- drift comparison each batch;
- vectorized FK/geographic quality assertions;
- durable local checkpoint write/fsync/atomic replace every batch.

Measured locally: **~2.89M rows/sec**, max RSS ~157 MB. This exceeds the 1M/sec local full-pipeline target.

## Roadblocks / limits
1. Text/semantic classification remains Python-oriented and is not yet part of the 2.89M/sec full-pipeline benchmark.
2. Privacy *detection* is qualified; encrypted production checkpoint/history stores and classified-field redaction require deployment-specific integration.
3. Cross-partition relational joins and parent/child arrival disorder are not a general distributed join engine. They require bounded keyed state or delegation to Kafka Streams/Flink/Spark/Event Hubs processing.
4. Aggregate windows now provide bounded state for numeric aggregates, but HLL/KLL/heavy-hitter/dependency aggregate windows should be added to cover the full Shape evidence model.
5. Broker/network/TLS/serialization throughput remains external qualification.
6. Exactly-once semantics are proven only for the local reference replay simulator, not claimed for external brokers.
