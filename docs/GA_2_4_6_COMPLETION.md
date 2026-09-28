# GA completion — workstreams 2, 4, 5 and 6

## 2. Real connector matrix

Completed locally:
- Kafka/Event Hubs decode equivalence;
- partition-aware checkpoints;
- at-least-once replay suppression and message-id deduplication;
- commit-after-handler-success;
- stale/out-of-order offset rejection;
- bounded reconnect semantics;
- 1.02M-delivery resilience qualification across eight partitions.

Live qualification entry points are implemented for Kafka and Azure Event Hubs and wired to the external connector workflow. They intentionally require explicit opt-in and real services/credentials. A live service is never marked qualified merely because the adapter or simulator passed.

## 4. Release matrix

Implemented:
- Linux/macOS/Windows × Python 3.11/3.12/3.13 workflow;
- full test, requirement, secret and compile gates;
- dedicated PyArrow matrix;
- wheel/sdist build and metadata validation;
- clean virtual-environment wheel installation and CLI doctor;
- release workflow with dependency audit, CycloneDX SBOM, SHA-256 checksums, Sigstore signing and GitHub build provenance.

This local runtime has no package-network access and does not contain PyArrow/build/twine/pip-audit/cyclonedx. Those external executions are therefore represented as runnable release gates, not fabricated local evidence.

## 5. Shape 1.0 specification freeze

Completed:
- `SHAPE_1_0_GA.md` freezes governed interfaces and evolution rules;
- immutable GA schema snapshot `shape-v1-ga.schema.json`;
- API stability promoted from beta to stable for named 1.0 surfaces;
- conformance tests for GA schema/artifact compatibility;
- package version advanced to `1.0.0`.

## 6. Public distribution

Completed:
- installation/offline-classified installation guide;
- five-minute quickstart;
- benchmark methodology;
- contribution rules;
- GA release process;
- `shape version` reports package/spec/artifact versions;
- release artifacts workflow creates wheel/sdist, SBOM, checksums, Sigstore bundle and provenance.

Publishing to PyPI/GitHub is deliberately not performed because this environment has no release identity/credentials and publishing is an external irreversible action.
