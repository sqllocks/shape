# Shape v0.1 Build Plan

## Objective
Prove the Shape kernel with a small, rigorous vertical slice.

## User-visible v0.1
shape capture <csv|parquet>
shape inspect <shape>
shape diff <shape> <shape|data>
shape validate <data> --against <shape>
shape generate <shape> --rows N --seed N

Python:
shape.capture(...)
Shape.save/load
Shape.compare(...)
Shape.generate(...)

## Milestones
M0 Repository constitution
- docs/spec set
- requirement registry
- ADR template
- devcontainer/toolchain
- CI skeleton
- license/provenance policy

M1 Arrow/type kernel
- logical type model
- RecordBatch contracts
- immutable Shape model
- builders
- error taxonomy

M2 Profiling primitives
- exact counters
- numeric moments
- quantile sketch ADR/implementation
- cardinality sketch
- top-K
- strings/categories
- datetime
- merge equivalence

M3 Artifact
- format ADR
- canonical manifest
- serialization
- checksum/corruption handling
- round-trip/cross-version fixtures

M4 Capture/inspect
- CSV/Parquet/Arrow sources
- bounded-memory capture
- CLI/API
- capability manifest

M5 Diff/validate
- structural and marginal comparison
- typed deltas
- basic policy/validation
- materiality hooks

M6 Generation
- basic independent marginals
- semantic basics
- deterministic seed
- constraints subset
- closed-loop fidelity tests

M7 History seed
- snapshot metadata
- tags/baseline concept
- no full service yet

M8 Hardening
- fuzzing
- benchmark baselines
- docs/examples
- packaging
- compatibility report

## Explicit v0.1 exclusions
No registry/cloud, no production streaming sinks, no full dependency graph, no distributed engine, no full Pack catalog, no strong DP claim, no ETL optimizer.

## Release criteria
- all v0.1 requirements traced to tests
- zero known artifact corruption vulnerabilities
- bounded-memory capture verified
- merge-equivalence tests pass
- deterministic generation contract documented
- benchmark baseline published
- generated-data fidelity report passes declared v0.1 contracts

## Security foundation added to M0-M1
Before v0.1 release:
- sensitivity label model
- classification propagation rules
- zero-network CI profile
- no-telemetry core
- log/secret leakage tests
- secure temp/spill policy
- artifact integrity checks
- SBOM/license/vulnerability scanning
- signed-release design
- security threat model
- malicious artifact/fuzz corpus
- explicit synthetic-output handling metadata

Enterprise KMS/HSM and classified-environment integration points are designed now; provider-specific integrations may ship after the kernel.
