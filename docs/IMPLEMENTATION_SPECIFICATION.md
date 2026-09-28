# Shape Implementation Specification
Status: Draft 0.1 — Normative implementation blueprint

## 1. Architectural constraints
SHAPE-ARCH-001: Core processing MUST accept Apache Arrow RecordBatch as the canonical in-memory batch boundary.
SHAPE-ARCH-002: Capture MUST be bounded-memory by default.
SHAPE-ARCH-003: Profilers SHOULD implement update(batch), merge(other), finalize().
SHAPE-ARCH-004: Persisted Shapes MUST be immutable.
SHAPE-ARCH-005: Mutable builders MUST finalize into immutable logical models.
SHAPE-ARCH-006: External ecosystems MUST enter through adapters/plugins rather than core imports.
SHAPE-ARCH-007: The core statistical engine MUST NOT require an LLM.
SHAPE-ARCH-008: Credentials and secrets MUST NOT be serialized into Shape artifacts.

## 2. Runtime flow
Source -> Arrow RecordBatch -> ProfileBuilder -> ShapeBuilder -> finalize -> Immutable Shape -> Serializer.
Execution flow: Immutable Shape + purpose/config -> Shape Compiler -> ExecutionPlan -> runtime.

## 3. Shape internal logical sections
- Manifest
- Schema
- Evidence
  - exact counters
  - statistical summaries
  - sketches
  - observations
- Model
  - semantics
  - dependencies
  - derivations
  - relationships
  - cohorts
  - lifecycles
  - temporal
  - operational
- Provenance
- Capabilities
- Extensions

Raw samples MUST NOT be stored by default.

## 4. Type system
Required logical support:
boolean; signed/unsigned integers; float32/64; decimal(p,s); string; binary; date; time; timestamp with timezone semantics; duration; UUID; list<T>; struct; map<K,V>.
Nullability is independent of type.
Semantic types are annotations above logical types.

## 5. Numeric profiling v0.1
Exact: count, null_count, nan_count, +/-inf_count, min, max where meaningful.
Streaming moments: Welford/mergeable equivalent for mean/variance.
Quantiles: implementation MUST use a documented mergeable sketch; KLL is the current preferred candidate pending ADR.
Cardinality: HLL++ or equivalent documented mergeable sketch.
Top-K/frequency: SpaceSaving or equivalent; exact mode MAY be used below bounded cardinality threshold.
Every approximate metric MUST expose algorithm/configuration and an error/accuracy description.

## 6. Categorical/string profiling v0.1
Counts, null rate, approximate/exact cardinality, top-K, length distribution, pattern features, entropy estimate where useful.
Semantic detection MUST return confidence and provenance.
High-cardinality values MUST be handled without retaining arbitrary raw values by default.

## 7. Datetime profiling v0.1
Min/max, quantiles, timezone metadata, resolution, inter-arrival summaries where ordered event semantics exist, day/week/month/hour distributions when enabled.
Temporal profiling MUST distinguish event time from processing/capture time.

## 8. Dependencies
V0.1 dependencies are intentionally limited.
Architecture MUST support numeric-numeric, categorical-categorical and categorical-numeric dependency representations.
Candidate discovery MUST precede expensive pairwise analysis for wide data.
Dependency measures MUST retain method, strength, confidence/uncertainty where available, and provenance.
Causality MUST NOT be inferred from dependency alone.

## 9. Derivation model
Derivations are deterministic or near-deterministic relationships distinct from statistical dependency.
Required classes: formula, transformed field, lookup, aggregate/computed, conditional derivation.
Discovery MAY suggest derivations; user/Pack declaration can make them authoritative.

## 10. Relationships
Represent PK/FK candidates, composite keys, self-reference, cardinality and child-count distributions.
Declared and inferred relationships MUST remain distinguishable.

## 11. Constraints
Constraint kind: HARD | SOFT | LEARNED.
Constraint source/provenance required.
Generation MUST satisfy HARD constraints or return a typed unsatisfiable-constraint failure.

## 12. Cohorts/lifecycles
Architecture MUST permit cohort-specific submodels and entity state transitions.
Population evolution and entity evolution MUST be modeled separately.
Synthetic identity persistence MUST NOT require retaining source identities.

## 13. Artifact
The artifact is a versioned container with independently addressable components.
Candidate internal layout:
manifest.json
schema.arrow
evidence/*
model/*
provenance.json
capabilities.json
extensions/*
Exact binary container/codec requires ADR.
Content-addressed components are preferred for History deduplication.

## 14. Versioning
Independent:
- Shape Specification Version
- Artifact Format Version
- Engine Version
- Pack Version
- Policy Version
Compatibility/migration rules MUST be explicit.

## 15. Determinism
Same Shape + seed + generator version SHOULD yield reproducible results.
Where parallel floating point or sketches prevent byte identity, behavioral equivalence requirements MUST be documented.
Serialization MUST be canonical where practical.

## 16. History
Types: ShapeSnapshot, ShapeDelta, ShapeSeries, Checkpoint, Event, Baseline, RetentionPolicy.
Operations: append, checkout, diff, merge, compact, rollup, tag, promote-baseline.
Observed/interpolated/extrapolated historical states MUST be explicit.
History MUST support periodic full checkpoints even if deltas are used.

## 17. Generation
Compiler phases:
1. capability check
2. intent/fidelity resolution
3. entity/cohort planning
4. dependency/derivation planning
5. relationship planning
6. constraint planning
7. execution partitioning
8. generation
9. validation/fidelity feedback

Generation MUST degrade explicitly when capabilities are missing.
Generated output MUST be profileable and comparable to its source Shape.

## 18. Streaming
Native streaming MUST NOT materialize the complete generated dataset.
Required concepts: virtual clock, event scheduler, entity state, bounded async queue, backpressure, partitioning, event-time/processing-time distinction, ordering policy, late arrival, duplicates, retries/faults, cancellation and deterministic seed behavior.
Clocks: RealClock, VirtualClock, AcceleratedClock, ReplayClock.

## 19. Quality
Quality Compiler consumes Shape + Policy + optional Pack/History/Context.
Results MUST distinguish rule violation, statistical change, significance and materiality.
Baselines MUST be explicit objects; automatic baseline learning MUST NOT silently normalize incidents.

## 20. Privacy
Threat model MUST include rare categories, small cohorts, quasi-identifiers, extreme values, membership/reconstruction risk and dependency leakage.
sanitize() transforms Shape.
mask() transforms data.
Privacy transforms MUST report removed/generalized information and fidelity impact.

## 21. Import/reconciliation
Initial import architecture MUST support pluggable schema sources.
Priority targets: SQL DDL, JSON Schema, Avro, Protobuf, OpenAPI, dbt metadata, database/platform schema.
Imported facts are DECLARED.
Capture facts are OBSERVED/INFERRED.
Reconciliation reports mismatches.

## 22. Packs/reference assets
Packs MUST be engine-independent packages.
Reference assets MUST carry source, license, version/date and checksum.
Pack composition MUST define namespace, extend, override, alias and conflict behavior.

## 23. Transformations
Shape Transform consumes source Shape + transformation specification and emits target Shape.
Initial planned transformations: star/dimensional; CDM/semantic exports via plugins.
Transformation behavior MUST preserve/declare effects on semantics, relationships and expected distributions.

## 24. Connectors/plugins
Core Source and Sink contracts operate on RecordBatch.
Connectors isolate optional dependencies.
Plugin compatibility/versioning and trust model MUST be specified before third-party remote execution.

## 25. Error taxonomy
At minimum typed errors for:
artifact corruption; unsupported version; unsupported extension; missing/incompatible Pack; invalid Policy; source failure; sink failure; resource-budget exhaustion; unsatisfiable constraints; impossible fidelity target; privacy conflict; incomplete History; connector/auth failure.
No generic unclassified public errors for expected failure classes.

## 26. Security
Treat .shape as untrusted input.
Validate container paths, decompression limits, lengths/counts, checksums and extension payloads.
No credential persistence.
Reference/plugin supply-chain metadata required.
Artifact signing is planned.

## 27. Progressive capabilities
Shapes declare capabilities rather than a misleading universal level/score.
Examples: structural validation, marginal reconstruction, dependency reconstruction, relational reconstruction, temporal reconstruction, streaming replay, lifecycle reconstruction, operational replay.

## 28. Performance
Performance is contractual and benchmarked.
V0.1 MUST define hardware-normalized benchmarks for capture throughput, peak memory, artifact size, open latency, diff latency and generation throughput.
Performance regressions above agreed thresholds fail CI.
Rust optimization occurs only behind stable boundaries and after profiling demonstrates need.

## 29. V0.1 boundary
Implement:
- Arrow core
- CSV/Parquet/Arrow sources
- incremental numeric/categorical/string/datetime profiles
- immutable Shape model
- artifact read/write
- inspect
- diff
- basic validation
- deterministic basic generation
- basic History snapshot metadata
- CLI + Python API

Defer full:
- distributed execution
- advanced dependency graph
- full lifecycle model
- production streaming sinks
- continuous quality service
- registry/cloud
- advanced privacy guarantees
- broad Pack catalog
- ETL optimizer

## 30. Definition of build-ready
No module begins autonomous implementation until:
- normative requirements exist
- interfaces are frozen for the milestone
- ADRs for open architectural choices are accepted
- invariants and failure semantics are documented
- conformance tests exist or are specified
- benchmark target exists when performance-sensitive

## 31. Sensitive/classified execution
The implementation MUST support sensitivity labels, information-flow propagation, trust-zone policy, offline/air-gapped execution, enterprise key management hooks, sensitivity-safe logs, auditable policy decisions, secure temporary/spill behavior, supply-chain hardening and governed release.

No output may silently receive less restrictive handling requirements than its inputs. Synthetic output is not automatically declassified. Cross-domain transfer is not an ordinary Sink.

Normative requirements: `07_SENSITIVE_CLASSIFIED_SECURITY_SPEC.md`.
