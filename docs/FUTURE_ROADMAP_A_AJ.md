# Shape post-1.0 roadmap A–AJ

This document is the implementation map for the A–AJ roadmap. Items are separated into **implemented core**, **integration/product work**, and **external/certification work** so roadmap status is not overstated.

| ID | Program | Repository status |
|---|---|---|
| A | Production Shape Hub | Hub protocol/server exists; enterprise auth/audit/quota primitives added. Durable HA cloud control plane remains deployment/product work. |
| B | Federated Shape | Federated nodes, attestations, aggregate/compare core implemented. Secure multi-party/TEE deployments remain external research/infrastructure. |
| C | Enterprise/classified security | Classification, encryption, hardening plus RBAC primitives, key ring, tamper-evident audit chain. HSM/KMS integrations and government accreditation remain external. |
| D | Privacy engineering | Existing privacy system plus Laplace DP primitive, k-anonymity suppression and re-identification-risk primitive. Formal privacy accounting remains future research. |
| E | Distributed execution | Existing distributed profiling plus execution-plan worker core. Production cluster scheduler/autoscaling remains infrastructure work. |
| F | Streaming platform | Existing streaming/checkpoint/keyed/window/failure stack and connector reliability qualification. Broker-specific exactly-once transactions remain live integration work. |
| G | Connector ecosystem | Kafka/Event Hubs/files/DBAPI plus ETL integration contracts exist. Additional vendor connectors are additive ecosystem work. |
| H | Domain packages | Domain package store plus marketplace/catalog core implemented. Large curated catalog remains content/ecosystem work. |
| I | Reference ecosystem | Versioned immutable reference assets implemented. Large licensed datasets remain acquisition/licensing work. |
| J | Geographic generation | LocationScope, address/lat-long and geospatial core implemented; arbitrary global reference coverage depends on reference datasets. |
| K | Synthetic fidelity | Joint/conditional/relational fidelity plus copula and missingness primitives implemented. Learned-model research remains optional. |
| L | Semantic/text | Semantic profiling and template reconstruction primitives added; LLM synthesis requires model/provider integration. |
| M | Temporal | Existing temporal engine plus multiple seasonality/regime helpers. |
| N | Graph/network | Graph profile/generation core added. |
| O | Geospatial Shape | Existing geospatial core retained; advanced topology/trajectory work remains additive. |
| P | Shape Query | Safe query engine exists. Full SQL-like language is a future language-design project and intentionally not silently added to frozen 1.x. |
| Q | Contracts/policy | Contracts, policy and quality subsystems exist; richer policy packs remain additive. |
| R | Drift/anomaly | Drift/policy/history subsystems exist; ML root-cause intelligence remains future research. |
| S | Lineage | DAG/blast-radius exists; vendor lineage ingestion is connector work. |
| T | Automated testing | Test generation, fuzz/adversarial/conformance suites exist. |
| U | Scenario/digital twin | Scenario mutation/generation exists; causal simulation requires explicit causal models. |
| V | Observability | Metrics/events/webhooks exist; vendor exporters are integrations. |
| W | Shape Studio | Functional web/API exists; polished interactive commercial UI remains frontend product work. |
| X | Developer experience | Python package/CLI/specs exist; additional language SDKs/IDE extensions remain separate products. |
| Y | CLI | Existing CLI retained; future Hub/admin commands depend on deployed Hub APIs. |
| Z | AI-assisted Shape | Deterministic-safe proposal/explanation core added; external LLM provider integration remains optional. |
| AA | Governance/provenance | Attestation and approval-gate primitives added; existing signed/encrypted artifacts retained. |
| AB | Reproducibility | Reproducibility manifest/digest primitive added; existing deterministic seeds/content IDs retained. |
| AC | Performance | Existing vectorized paths and >1M/sec qualification retained; native/GPU kernels remain hardware-specific optimization. |
| AD | Massive-scale qualification | Harnesses exist; 100M/1B and multi-day runs require release hardware/runtime. |
| AE | Packaging/deployment | PyPI/CI/release workflows exist; OCI/Helm/Terraform deployment assets remain target-environment work. |
| AF | Enterprise administration | Quota core added; billing/licensing/support systems are commercial operations. |
| AG | Interoperability | JSON Schema/Arrow/OpenAPI-oriented boundaries exist; standards integrations remain ecosystem work. |
| AH | Migration/compatibility | Artifact/spec migration modules already exist; future migrations are version-triggered. |
| AI | Documentation/education | Architecture/spec/install/quickstart/security/benchmark docs exist; courses/certification are content programs. |
| AJ | Commercial operations | Engineering can provide metering/entitlement primitives; pricing, SLAs, support organization and legal/commercial decisions require human business inputs. |

## Completion rule

A roadmap item is **code-complete** only where the repository can implement it without external services, licensed datasets, hardware, certification authorities, commercial decisions, or credentials. External portions are represented by interfaces/gates rather than fabricated as completed evidence.
