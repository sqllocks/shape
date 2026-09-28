# Shape 1.0.0rc1 — Release Candidate

## Frozen RC surface
- Package: `sqllocks-shape`
- Runtime version: `1.0.0rc1`
- `.shape` format version: 1
- Stable convenience API: `shape.profile`, `shape.save`, `shape.load`, `shape.diff`
- CLI primary flow: `shape profile`, `shape inspect`, `shape validate`, `shape diff`, `shape generate`
- Kafka and Azure Event Hubs adapters remain optional extras.

## RC engineering gates completed in this environment
- Bounded/mergeable numeric and text evidence.
- Bounded missingness, covariance, hashed dependency, temporal, relational and fixed-grid geographic evidence.
- Deterministic partitioned keyed state with TTL/capacity and snapshot/restore.
- Parallel full-evidence columnar engine.
- Sensitive value-bearing evidence redaction for classified fields.
- Canonical `.shape` ZIP artifact with checksums, content identity, format version and corruption rejection.
- CLI profile -> `.shape` -> inspect smoke flow.
- 100 randomized streaming batch-boundary trials.
- Existing generation, streaming, artifact, privacy, quality, history and conformance suites.

## Performance qualification
On this host:
- dictionary-encoded text + semantic evidence: ~1.70M rows/sec median (1M-row fixture, 3 measured runs after warmup);
- mixed full-evidence engine: ~1.42M rows/sec over 5M rows, including numeric profiling, text evidence, missingness, covariance, hashed dependency, temporal, relational, geographic evidence, and an atomic local checkpoint per 250k rows.

Fixture construction is excluded from the full-evidence timing. These are local engine measurements, not broker/network claims.

## External gates that cannot be truthfully completed here
1. Live Kafka broker qualification: no broker endpoint/credentials and Kafka SDK is not installed.
2. Live Azure Event Hubs qualification: no namespace/hub/credentials and Event Hubs SDK is not installed.
3. Wheel/sdist build: the build backend (`hatchling`) is not installed and this runtime cannot reach the package index to install it.
4. Full PyArrow-specific test paths: PyArrow is declared as a runtime dependency but is not installed in this execution environment.

These are release-environment/infrastructure gates, not silently waived gates. The source candidate is RC-versioned, but publication should wait until these gates pass in CI/release infrastructure.
