# Shape — Twelve post-RC platform capabilities

All twelve capabilities are implemented as local-first cores with dependency-light interfaces so the engine remains usable without a hosted control plane.

1. **Shape Hub / remote registry** — HTTP client, authenticated HTTP server, immutable content IDs, version log, pull/push and ref promotion. The transport is replaceable.
2. **Domain packages** — immutable content-addressed domain publication, semantic version discovery/resolution and installation. This is the package substrate for community domains.
3. **ETL-native runtime** — captures Shape evidence before/after every transform and can enforce contracts/drift gates. Fast stable-schema columnar capture is used automatically. Integration contracts are included for Airflow, Dagster, dbt, ADF, Fabric, Spark and Flink.
4. **Distributed Shape** — partition profiling and deterministic merge. Stable-schema partitions automatically use the NumPy columnar path; lineage-safe merge semantics remain explicit.
5. **Joint reconstruction** — vectorized fitted multivariate numeric model with correlation preservation and positive-semidefinite repair, complementing existing conditionals/FKs/marginals.
6. **Temporal behavior** — fit/replay arrival intervals, trend, periodicity and seasonal value behavior.
7. **Reference assets** — immutable versioned, content-addressed reference datasets with provenance and indexed lookup.
8. **Lineage** — DAG, cycle prevention, downstream traversal and blast-radius analysis; iterative algorithms support deep graphs without recursion limits.
9. **Shape test generation** — valid and adversarial boundary datasets from Shape + contract.
10. **Scenarios** — immutable controlled Shape mutations including relative percentage changes, then synthetic generation from the scenario Shape.
11. **Observability** — thread-safe counters/gauges, Prometheus exposition, event bus, JSONL and generic webhook sinks, alert routing and Slack/Teams/PagerDuty payload formatters.
12. **Web/API** — local dependency-free JSON API and HTML dashboard with optional bearer-token protection. The service layer is separate from HTTP so a production framework can wrap it.

## Performance qualification

`rq/platform12_perf.py` contains explicit performance gates for all twelve. It measures protocol operations, package publication, ETL evidence capture, distributed profiling, joint generation, temporal generation, reference indexing, lineage traversal, test generation, scenario generation, metrics and live HTTP API requests. The qualification fails its process exit code if any gate misses its minimum.

Performance results are machine-specific and must be re-run on release hardware. They are evidence, not universal promises.

## External qualification boundary

The implementations that talk to external ecosystems are testable locally at their protocol/adapter boundaries. Actual production qualification against hosted Shape Hub deployment, Spark/Flink clusters, Airflow/Dagster installations, Microsoft Fabric/ADF tenants, Slack/Teams/PagerDuty endpoints and other external services requires those environments and credentials. Shape does not mark those external environments qualified merely because the adapters exist.
