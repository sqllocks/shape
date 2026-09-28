# Shape Product Architecture
Status: BASELINED 1.0 — Changes require an Architecture Change Proposal (ACP) and owner approval

## 1. Product thesis
Shape establishes **Shape as Code**: data behavior becomes a versionable, executable artifact.

A Shape is a versionable, privacy-aware, executable model of data behavior that can be compared, validated, transformed, and used to reconstruct statistically and behaviorally representative data.

Shape History is an ordered behavioral record that allows those reconstructions to evolve through time.

Shape reconstructs behavior, never original records.

## 2. Primary primitives
### Pack — what the domain knows
Ontology, entities, semantic types, invariants, normal behaviors, scenarios, reference assets and reusable behavior models.

### Shape — what is
Observed and inferred structure, statistics, semantics, dependencies, relationships, derivations, cohorts, lifecycle, temporal behavior, operational behavior, provenance, uncertainty and capabilities.

### Policy — what should be
Hard requirements, acceptable ranges, materiality, quality gates, fidelity contracts, privacy policies and governance expectations.

### Scenario — what if
Explicit modifications to a Shape/ShapeSeries for simulation and counterfactual generation.

### History — what was
Immutable Shape snapshots, ShapeDelta, ShapeSeries, baselines, checkpoints, rollups, retention and correlated events.

### Context — circumstances
Time, geography, promotion, deployment, holiday, cohort or other circumstances used to evaluate conditional normality. Context is not a sixth primary artifact.

## 3. Core verbs
capture, import, reconcile, inspect, explore, project, condition, slice, merge, diff, validate, quality, generate, reconstruct, stream, simulate, transform, sanitize, mask, watch, history, checkout, compile.

## 4. Shape algebra
Capture(D)->S
Import(C)->S_declared
Reconcile(S_declared,S_observed)->Delta
Merge(S1,S2)->S3
Compare(S1,S2)->Delta
Validate(S,P)->Result
Generate(S,Intent,Seed)->D'
Simulate(S,Scenario)->S'
Transform(S,T)->S'
Sanitize(S,PrivacyPolicy)->S'
Project(S,E)->S'
Condition(S,C)->S'
Compile(S,Purpose)->ExecutionPlan

## 5. Shape hierarchy
Environment Shape
- Dataset Shape
  - Entity Shape
    - Field Shape
    - Dependency/Derivation Shape
  - Relationship Shape
- Temporal Shape
- Operational Shape

## 6. Logical vs physical Shape
Logical Shape models meaning and behavior independent of storage.
Physical Shape models encoding, partitioning, ordering, payload size, compression, arrival behavior, storage and transport characteristics.

## 7. Type hierarchy
Physical Type -> Logical Type -> Semantic Type -> Domain Meaning.

## 8. Evidence and interpretation
Every meaningful property records provenance class:
- OBSERVED: directly measured
- INFERRED: statistically inferred
- DECLARED: supplied by schema, Pack or user
- DERIVED: computed from other Shape properties
- INTERPOLATED: estimated between historical observations
- EXTRAPOLATED: estimated beyond observations

## 9. Quality
Quality is an operation, not another primary artifact:
Quality(Shape, Policy, Pack, History, Context).

Quality layers:
1. deterministic constraints
2. statistical quality
3. behavioral/dependency quality
4. relational quality
5. temporal/freshness/volume quality
6. operational quality

Change, statistical significance and business materiality are distinct concepts.

## 10. Reconstruction
Three modes:
- Snapshot reconstruction: representative data as of one Shape.
- Historical reconstruction: representative evolving data from ShapeSeries.
- Counterfactual reconstruction: historical/snapshot reconstruction modified by Scenario.

Closed-loop reconstruction:
Target Shape -> Generate -> Profile generated data -> Compare -> Adjust until Fidelity Contract is met or declared infeasible.

## 11. Shape Packs
Packs replace hard-coded domains.
A Pack contains:
- ontology/entities
- semantic types
- invariants
- behaviors
- scenarios
- reference assets
- behavior models

Packs compose and version independently. Locale Packs remain separate from industry Packs.

## 12. Behavior models
Reusable primitives:
- EventSequence
- StateMachine
- TelemetrySeries
- TransactionStream
- FileArrivalProcess
- EntityLifecycle

## 13. Derivations and constraints
Relationships are explicitly separated:
- statistical dependencies
- deterministic derivations
- structural relationships

Constraints are HARD, SOFT or LEARNED.

## 14. History
History stores behavior, not source rows.
Supports snapshots, deltas, series, checkpoints, events, baselines, rollups, tags, retention and time travel.
Multi-resolution retention is expected.
Pack and Policy histories are independent from Shape history.

## 15. Privacy
Two outputs:
- sanitize Shape -> lower-disclosure Shape
- mask Data -> privacy-transformed data
Privacy transformations MUST report fidelity loss and MUST NOT claim safety without a defined threat model.

## 16. Fidelity
No universal fidelity score.
Dimensions include structural, marginal, dependency, relational, temporal, lifecycle, operational and downstream fidelity.
Fidelity is evaluated relative to declared intent/workload.

## 17. Intent
Capture and reconstruction may declare intent:
development, analytics, testing, ML, streaming/load-test, quality, privacy-sharing.
Intent influences budget allocation, not truthfulness.

## 18. Shape Compiler
Shape is declarative. Purpose-specific compilers produce GenerationPlan, StreamingPlan, ValidationPlan, QualityPlan and TransformationPlan.
Principle: **Capture once. Compile for purpose.**

## 19. ETL destination
Shape-aware ETL is a later product layer:
Source -> input Shape -> Transform -> output Shape -> Policy/Quality -> Destination.
Shape may eventually inform partitioning, joins, sampling, capacity and cost estimation.

## 20. Non-goals
Shape is not a backup system, source-record recovery system, warehouse, catalog replacement, orchestration replacement, Kafka replacement, dbt replacement, BI tool, or general ML platform.

## 21. Spindle compatibility requirement
SHAPE-COMPAT-001: Every useful Spindle capability MUST receive a documented disposition: SUPPORTED, SUPERSEDED, PLANNED, or INTENTIONALLY-OMITTED.

Explicitly retained/generalized:
- calibrated domains -> Shape Packs
- 21 generation strategies -> generator/derivation/dependency/relationship primitives
- formula/derived/computed/lookup/conditional/correlated
- reference data and record anchoring
- FK/composite/self-reference integrity
- SCD2 -> temporal entity versioning
- schema inference -> capture
- DDL import -> generalized import
- compare -> diff/fidelity
- masking -> privacy subsystem
- incremental generation
- time travel -> History
- composite domains -> Pack composition
- star/CDM/semantic-model transforms -> Model Transformation/plugins
- chaos -> Data Chaos
- streaming/burst -> native streaming and Operational Shape
- Fabric/Eventhouse/Kafka/Event Hub -> connectors
- MCP -> Shape MCP
- simulation patterns -> Behavior Models
- scale tiers -> compiler-driven scale targets

## 22. Sensitive and classified information
Shape is designed to operate in environments containing PII, PHI, PCI, proprietary, regulated, export-controlled/CUI-style and classified information, including Secret/Top Secret when deployed within appropriately authorized/accredited systems.

A Shape is NOT assumed non-sensitive merely because it does not contain source rows. Shapes, deltas, diffs, History and synthetic outputs can disclose protected information and therefore carry explicit sensitivity/handling metadata.

Security is an execution constraint across the Shape algebra:
- labels propagate through operations
- lower-trust release is deny-by-default
- core operation supports offline/air-gapped deployment
- no mandatory cloud, telemetry or LLM dependency
- synthetic output is not automatically declassified
- cross-domain transfer is a separately governed Release/Transfer operation
- classification/release semantics require explicit human approval to change

Detailed normative requirements are in `07_SENSITIVE_CLASSIFIED_SECURITY_SPEC.md`.
