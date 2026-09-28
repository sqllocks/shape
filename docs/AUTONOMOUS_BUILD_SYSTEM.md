# Autonomous Build System for Shape
Status: Draft 0.1

## Goal
Enable AI coding agents to implement Shape with high autonomy while preventing architectural drift, statistical sloppiness, silent requirement invention and benchmark regressions.

Autonomy does NOT mean allowing agents to make product/architecture decisions silently. It means agents can plan, implement, test, benchmark, review and prepare merge-ready changes inside pre-approved contracts.

## 1. Required control plane
The repository needs a machine-readable engineering control plane:

/specs/requirements.yaml
/specs/interfaces/
/specs/schemas/
/adrs/
/tests/conformance/
/tests/golden/
/benchmarks/
/agent/
/plans/

Every implementation task references requirement IDs.

## 2. Requirement lifecycle
Product Requirement
-> Architecture Requirement
-> ADR
-> Interface Contract
-> Acceptance/Conformance Tests
-> Benchmark
-> Implementation
-> Review Evidence

No orphan implementation.

## 3. Agent roles
Use logically separate agents, even if implemented by the same underlying model:
- Planner: decomposes approved milestone into dependency-aware work packets.
- Implementer: changes code only within assigned packet.
- Test Author: writes/extends conformance/property tests independently.
- Statistical Reviewer: validates algorithm/math claims and approximation behavior.
- Performance Reviewer: runs benchmarks and allocation/memory checks.
- Security Reviewer: fuzz/untrusted artifact/secret/plugin review.
- Compatibility Reviewer: checks artifact/API/version and Spindle disposition.
- Documentation Reviewer: verifies public behavior matches docs.
- Release Agent: assembles changelog, compatibility matrix and evidence.

An implementer MUST NOT be the sole approver of its own work.

## 4. Work packet contract
Every autonomous task contains:
- objective
- requirement IDs
- files/modules allowed to change
- dependencies
- interfaces consumed/produced
- invariants
- forbidden changes
- tests required
- benchmark budget
- failure semantics
- documentation updates
- completion evidence

## 5. Decision escalation
Agents MAY decide local implementation details when:
- no public behavior changes
- no artifact/API/schema compatibility impact
- no privacy/security implication
- no algorithmic fidelity tradeoff
- no dependency/license change
- no performance budget change

Agents MUST stop and propose an ADR when any condition above is violated.

## 6. Architecture enforcement
CI checks:
- import/dependency boundaries
- public API snapshots
- artifact schema compatibility
- requirement-to-test traceability
- ADR references for architecture-sensitive changes
- optional dependency isolation
- deterministic fixtures
- no forbidden core dependencies

## 7. Testing pyramid
Unit tests
Property-based tests
Merge-equivalence tests
Streaming/batch equivalence tests
Golden Shape corpus
Artifact round-trip tests
Cross-version compatibility tests
Statistical convergence tests
Generation fidelity tests
Connector contract tests
Security/fuzz tests
Performance regression tests
End-to-end CLI/API tests

## 8. Golden corpus
Curated deterministic datasets:
numeric, categorical, string, datetime, null-heavy, constant, high-cardinality, heavy-tail, multimodal, Unicode, decimal, NaN/Inf, nested Arrow, correlated, conditional, relational, composite keys, self-reference, temporal, lifecycle, dirty, malformed.

Each fixture includes expected facts and tolerance-based expectations.

## 9. Statistical oracle
Do not assert approximate algorithms with brittle exact snapshots.
Tests define:
- invariants
- confidence/tolerance bounds
- merge equivalence
- monotonic properties
- convergence with sample size
- seeded reproducibility expectations

## 10. Generator closed-loop gate
For applicable fixtures:
Target Shape -> Generate -> Capture generated output -> Fidelity Report.
Release gates use Fidelity Contracts, not visual plausibility.

## 11. Benchmark harness
Fixed benchmark datasets and documented reference hardware/container.
Track:
- rows/sec
- values/sec
- peak RSS
- allocations where available
- artifact bytes
- open/serialize latency
- diff latency
- generation throughput
- streaming throughput/backpressure behavior
Store historical benchmark results in CI artifacts.

## 12. Reproducible dev environment
Pin:
- Python versions
- lockfile
- Arrow version range
- compiler/toolchain versions when Rust begins
- lint/type/test tools
- container/devcontainer
- pre-commit hooks

One command should bootstrap the environment.

## 13. Quality gates
A PR cannot merge if:
- tests fail
- requirement mapping absent
- public behavior undocumented
- benchmark regression exceeds threshold
- artifact compatibility unexpectedly changes
- new dependency lacks license/security review
- generated code contains unresolved TODO architecture decisions
- secrets/sample PII appear
- coverage for changed critical path is inadequate

## 14. Coding-agent context
Agents receive small authoritative context bundles, not the entire repository blindly:
- task packet
- relevant specs/ADRs
- public interfaces
- neighboring module docs
- tests
- benchmark contract

This reduces context drift and contradictory invention.

## 15. Memory/decision log
Persistent decisions live in repo docs, never only in agent/chat memory.
Every accepted design change updates:
- ADR if architectural
- relevant normative spec
- requirement registry
- compatibility notes
- tests

## 16. Issue generation
The Planner may autonomously generate implementation issues only from approved milestone specs.
It may not expand scope.
Each issue is sized for one coherent reviewable change.

## 17. Branch strategy
Agents work in isolated branches/worktrees.
Small PRs.
Dependency-aware merge order.
No mega-PR implementing an entire subsystem.

## 18. Autonomous loop
1. select next unblocked work packet
2. load authoritative context
3. implement
4. run focused tests
5. run full relevant conformance
6. benchmark if required
7. independent review agents
8. fix findings
9. produce evidence report
10. merge only if gates pass
11. update dependency graph
12. select next packet

## 19. Human approval boundaries
Human/owner approval required for:
- Product Architecture changes
- new/changed public primitive
- Shape Spec or artifact compatibility break
- privacy/security threat-model change
- license change
- new network/cloud dependency in core
- major algorithm replacement affecting fidelity
- baseline performance budget relaxation
- v1 public API commitment
- release signing/publishing credentials

Everything else can become progressively autonomous.

## 20. Repository telemetry for agents
Maintain machine-readable:
- requirements coverage
- test coverage by requirement
- benchmark trends
- known debt
- open ADRs
- compatibility matrix
- current milestone DAG
- flaky test registry

## 21. Definition of autonomous-ready
The repository is autonomous-ready when an agent can take any unblocked work packet and determine, without asking architecture questions:
- what to build
- what not to change
- how correctness is measured
- how performance is measured
- how failure behaves
- what compatibility must remain
- what evidence proves completion

## 22. Sensitive-data autonomous controls
Security-sensitive work packets MUST include classification-flow tests, zero-network tests where applicable, log/secret leakage checks and security-review evidence.

Autonomous agents cannot approve changes to classification semantics, declassification/release rules, crypto/security boundaries, cross-domain transfer, external transmission defaults or sensitive logging behavior.
