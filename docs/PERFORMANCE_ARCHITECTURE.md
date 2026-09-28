# Performance Architecture

Shape uses a dual-path profiler.

**Streaming reference path** accepts arbitrary row iterables and keeps memory bounded by schema/sketch state. It is the semantic reference and fallback.

**Vectorized columnar path** accepts arrays/batches already natural to modern ETL engines. Numeric kernels use NumPy operations and exact vectorized quantiles/cardinality. This is the preferred high-throughput batch path.

Future Arrow/Rust kernels should implement the same evidence contract and conformance fixtures, not create a separate product semantic. A connector should hand Shape columnar batches without converting every value to Python objects whenever possible.

Performance claims MUST identify path, column mix, evidence enabled, row count and host.
