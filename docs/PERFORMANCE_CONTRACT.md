# Performance Contract
Core profiling algorithms intended for unbounded inputs MUST have memory bounded by configured sketch/state sizes plus schema width, not row count. Exact dependency/key/privacy operations are explicitly bounded/reference operations unless their API states otherwise. Benchmarks are evidence for a specific host, not portable guarantees.
