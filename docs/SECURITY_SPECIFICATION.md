# Shape Sensitive & Classified Data Security Specification

Status: available.

Status: Draft 0.1 — Normative implementation specification under baselined Product Architecture

## 1. Security thesis
Shape MUST be capable of operating on highly sensitive information without assuming that profiling, summarization, synthetic generation, or conversion into a `.shape` artifact removes sensitivity.

A Shape may itself contain sensitive or classified information. A Shape MUST inherit or derive handling requirements from the source information and the evidence/model retained within it.

Support for classified environments means Shape is architected so it can be deployed inside appropriately authorized/accredited environments. The project MUST NOT claim that Shape itself confers accreditation, authorization, declassification, or regulatory compliance.

## 2. Sensitivity classes
The engine MUST support extensible labels and policies rather than hard-code one jurisdiction's classification scheme.

Example policy labels include:
- PUBLIC
- INTERNAL
- CONFIDENTIAL
- RESTRICTED
- PII
- PHI
- PCI
- PROPRIETARY
- CUI / export-controlled categories
- SECRET
- TOP_SECRET
- organization-defined compartments, caveats, dissemination and handling controls

Labels MAY coexist. Classification and regulatory category are separate dimensions.

## 3. Label granularity
Labels and handling metadata MUST be attachable to:
- Environment
- Dataset
- Entity
- Field
- Evidence component/sketch
- Model component
- Reference Asset
- Shape artifact
- ShapeSeries/History
- Export/output

The artifact-level handling label MUST be conservatively derived from its contents and policy.

## 4. Information-flow tracking
SHAPE-SEC-001: Sensitive labels MUST propagate through operations.
Operations include capture, import, reconcile, merge, project, condition, slice, diff, validate, generate, reconstruct, transform, sanitize, mask, History, compile, export, plugins and connectors.

The default rule is monotonic: an output cannot silently receive a less restrictive handling classification than its inputs.

Downgrade/release requires an explicit authorized release policy and evidence that its requirements succeeded.

## 5. Trust zones
Execution environments and destinations MUST declare trust attributes.
A Policy MAY restrict:
- which sources may be read
- where Shapes may be written
- which sinks may receive generated/masked data
- which plugins may execute
- whether networking is permitted
- whether external models/services are permitted
- allowed cryptographic profile
- allowed operators/users/service identities

Flows crossing trust zones are evaluated before execution.

## 6. Offline and air-gapped operation
SHAPE-SEC-002: Core Shape functionality MUST operate with zero mandatory internet, SaaS, telemetry, cloud, or external LLM dependency.

Required offline capabilities:
- capture
- inspect
- diff
- validate
- quality
- generate
- History
- sanitize/mask
- local Packs/Reference Assets
- local semantic/sensitivity detection
- artifact verification

Network access MUST be optional and denyable.

## 7. AI/LLM boundary
Sensitive data MUST NOT be sent to an external LLM by default.
The statistical engine never requires an LLM.
AI-assisted semantics MAY use:
- deterministic/local detectors
- locally hosted approved models
- explicitly authorized external providers under Policy

The execution plan MUST reveal whether any operation can transmit content outside the local trust boundary before execution.

## 8. Authorization
The architecture MUST expose hooks for:
- RBAC
- ABAC
- purpose-based access
- classification/clearance attributes
- compartments/caveats
- source/destination trust
- tenant/project boundaries

Authorization controls are in progress and are not yet enforced by the reference implementation.

## 9. Cryptography
Sensitive deployment profiles MUST support:
- encryption in transit
- encryption at rest
- envelope encryption
- enterprise KMS/HSM integration
- key IDs without serialized key material
- rotation
- integrity/authentication
- artifact signing/verification

Cryptographic implementation MUST be provider-pluggable so deployments can require approved/FIPS-validated modules where applicable.

Shape MUST NOT claim FIPS compliance merely because it supports a FIPS-mode provider.

## 10. Secrets
Credentials, private keys, tokens and passwords MUST NOT be persisted in Shape artifacts, History, logs, errors or benchmark output.
Connectors receive secrets through approved runtime secret providers.
Secret scanning is a CI/release gate.

## 11. Temporary data and spill
Sensitive execution profiles MUST:
- avoid plaintext disk spill by default
- support encrypted spill where unavoidable
- use controlled temporary directories
- delete temporary artifacts on completion/failure where the platform permits
- document OS/runtime limitations on guaranteed memory erasure
- prevent sensitive payloads from crash dumps where configurable

No false guarantee of perfect RAM erasure is permitted.

## 12. Logging/telemetry
Logs, metrics, traces and errors are treated as potential exfiltration channels.
They MUST be safe-by-construction:
- no raw values by default
- no credentials
- bounded identifiers
- sensitivity-aware redaction
- configurable hashing/tokenization where justified
- auditable operator/action metadata
- no opt-out telemetry requirement

Diagnostic modes that expose protected values require explicit authorization and prominent handling labels.

## 13. Audit
Security-relevant operations SHOULD emit structured audit events:
- actor/service identity
- action
- source artifact/data identifier
- destination
- policy decision
- classification before/after
- release/sanitization decision
- engine/version
- timestamp
- integrity reference

Audit records MUST avoid copying protected source values.

## 14. Sanitization and release
sanitize() transforms a Shape.
mask()/tokenize()/redact()/generalize() transform data.

A lower-classification or externally releasable output MUST NOT be assumed merely because sanitization ran.

Release requires:
1. explicit release target/trust level
2. approved policy
3. successful required transformations
4. leakage/fidelity assessment
5. authorization
6. recorded release decision

Where policy requires human review, automation MUST stop before release.

## 15. Synthetic data
SHAPE-SEC-003: Synthetic data generated from sensitive/classified Shapes MUST NOT automatically be labeled PUBLIC or declassified.

Generated output receives a derived sensitivity assessment based on:
- source classification
- retained rare/extreme information
- conditional/dependency leakage
- reference assets
- membership/reconstruction risk
- release policy
- generation fidelity

Policy may permit a lower handling level only through an explicit release determination.

## 16. Shape privacy leakage
Threat modeling MUST include:
- rare categories
- small cohorts
- quasi-identifiers
- extrema/outliers
- high-cardinality top-K
- membership inference
- reconstruction attacks
- dependency leakage
- differencing attacks across Shape versions
- History/delta leakage
- reference-asset disclosure
- cross-Shape composition attacks

Privacy budgets/DP MAY be added where appropriate, but no universal privacy guarantee is implied.

## 17. History
Snapshots, deltas, checkpoints, indexes, backups and rollups inherit handling requirements.
A delta can reveal sensitive change even when individual snapshots appear innocuous.
Compaction MUST NOT weaken labels.
Retention/deletion policies MUST be enforceable by storage adapters.

## 18. Diff
Diff output can be more revealing than either input.
The Diff compiler MUST derive output sensitivity conservatively and support suppression/generalization rules for sensitive changes.

## 19. Packs and Reference Assets
Packs and Reference Assets carry:
- sensitivity/handling metadata
- license/provenance
- integrity checksum/signature where available
- permitted-use policy
- network requirement declaration

Untrusted Packs MUST NOT execute arbitrary code in core.

## 20. Plugins/connectors
Plugins execute within declared trust boundaries.
Plugin manifests MUST declare:
- network access
- filesystem access
- subprocess/native code use
- secret access
- data categories consumed/emitted
- supported trust profiles

High-sensitivity deployments SHOULD support allowlists and signed plugins.

## 21. Cross-domain transfer
Cross-domain transfer is NOT modeled as an ordinary sink.

A separate governed Release/Transfer operation MUST be used when information crosses a classification/trust boundary.
It MUST support policy enforcement, content inspection hooks, provenance, authorization and auditable release decisions.

Shape does not replace accredited cross-domain solutions; it integrates with approved transfer mechanisms.

## 22. Supply-chain security
Required project controls:
- SBOM
- pinned/locked dependencies
- dependency provenance
- vulnerability scanning
- license scanning
- signed release artifacts
- reproducible-build target
- protected release credentials
- CI provenance/attestations
- SLSA-style build controls
- malicious artifact fuzzing
- dependency/update review

## 23. Deployment security profiles
Define at least:
### Standard
Normal OSS/local use.

### Regulated
No mandatory telemetry; stronger audit, KMS integration, policy enforcement, hardened defaults.

### Restricted / Air-Gapped
Zero-network core, offline dependencies/Packs, local-only detection, signed offline artifacts, strict plugin allowlist.

### Classified-Capable
Architecture suitable for deployment within an appropriately accredited classified environment; organization-specific classification labels, clearance/compartment authorization hooks, approved crypto providers, offline operation, strict release boundaries and auditable controls.

These are technical profiles, not certifications.

## 24. Security testing
Required:
- parser/container fuzzing
- malicious Shape corpus
- decompression bombs
- path traversal
- integer/length overflow
- malformed Arrow payloads
- untrusted extension payloads
- log leakage tests
- secret leakage tests
- zero-network tests
- authorization-policy tests
- classification propagation property tests
- downgrade/release denial tests
- dependency/SBOM scanning

## 25. Autonomous engineering boundary
Agents MUST NOT autonomously change:
- classification semantics
- downgrade/declassification/release rules
- cryptographic trust boundaries
- external transmission defaults
- cross-domain transfer semantics
- sensitive logging defaults
- secret handling
- security profile guarantees

These require owner/security approval and an Architecture Change Proposal or security ADR as appropriate.

## 26. Core security invariants
SHAPE-SEC-010: No operation silently lowers classification/handling requirements.
SHAPE-SEC-011: No mandatory external service is required for core operation.
SHAPE-SEC-012: No secret material is persisted in a Shape.
SHAPE-SEC-013: Raw protected values are absent from logs by default.
SHAPE-SEC-014: Synthetic does not mean declassified.
SHAPE-SEC-015: Cross-domain transfer is an explicitly governed operation.
SHAPE-SEC-016: A Shape is treated as sensitive unless policy establishes otherwise.
SHAPE-SEC-017: Security claims distinguish technical capability from external certification/accreditation.
