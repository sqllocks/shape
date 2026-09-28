# RC-1 External Qualification Runbook

## Build/install
1. Use clean CI runners for supported Python versions on Linux, macOS and Windows.
2. Install build tooling, run `python -m build`, then install the produced wheel into a fresh environment.
3. Run the complete test suite against the installed wheel, not `src/`.
4. Validate wheel/sdist metadata, console entry point and optional extras.

## PyArrow
Install each supported PyArrow version and run all kernel/type tests. Exercise Table, RecordBatch, chunked arrays, dictionary arrays, nulls, decimals, timestamps/timezones, large strings, Parquet and zero-copy paths.

## Kafka
Provision a non-production multi-partition topic. Run the connector conformance fixture, then inject consumer death, broker interruption, rebalance, duplicate/redelivery, poison records and partition reordering. Record evidence equivalence, offsets, recovery time, p50/p95/p99 latency, memory and sustained throughput. Test TLS/SASL fail-closed behavior.

## Azure Event Hubs
Provision a non-production hub and consumer group. Repeat the same logical fixture and failure campaign. Exercise Entra/connection authentication as applicable, partition ownership, checkpoint store behavior, throttling and reconnect.

## Supply chain/security
Generate CycloneDX/SPDX SBOM, run dependency vulnerability audit, static analysis, type checking and secret scanning. Verify credentials and raw classified values do not occur in logs, `.shape`, history or checkpoints. Sign and provenance-attest release artifacts.

## Final publish gate
Publish `1.0.0rc1` only when every mandatory external gate has recorded evidence. Never convert an unavailable test into a pass.
