# RQ-6 — Streaming at Scale

## Results
Local columnar microbatch benchmarks:
- raw batch construction/ingestion loop: ~279M rows/sec (transport-free upper-bound diagnostic only);
- streaming profiling, 50M rows, 250k batches: ~5.18M rows/sec;
- streaming profiling + drift comparison, 10M rows: ~5.19M rows/sec;
- max RSS in profiled tests: ~157 MB.

The meaningful Shape number is therefore **~5.18M rows/sec** for the tested 3-column numeric streaming microbatch workload, not the raw-loop number.

## Architecture
`VectorStreamProfiler` accepts columnar microbatches and profiles them without converting every event into Python dictionaries. The scalar `OnlineShape` remains the semantic/reference path and was hardened from list/pop(0) to a bounded deque.

## Adversarial qualification
Passed:
- event-time tumbling windows;
- bounded lateness and out-of-order acceptance;
- too-late event dropping;
- injected crash followed by checkpoint replay with no loss/duplicate in the reference simulator;
- corrupt checkpoint rejection;
- bounded online-buffer behavior after 100k events;
- exact reference duplicate suppression across batches;
- drift injection during a 10M-row profiled stream.

## Boundaries
This benchmark does NOT measure Kafka/Event Hubs/Kinesis network transport, broker acknowledgement, serialization/deserialization, TLS, cloud checkpoint stores, distributed coordination, or exactly-once guarantees of a managed engine. Those require live integration qualification.

The exact set-based dedupe helper is a correctness reference and is not suitable for unbounded production streams because its state grows with unique IDs. Production dedupe requires bounded/time-window state or an external/stateful stream engine.

Window values are currently retained until closure; high-cardinality/long windows can consume memory proportional to events in open windows. Aggregating window operators are required before claiming bounded memory for arbitrary window workloads.
