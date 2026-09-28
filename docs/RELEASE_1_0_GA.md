# Shape by SQLLocks 1.0 GA release process

GA is releasable only when:
- Linux/macOS/Windows × Python 3.11/3.12/3.13 CI is green;
- wheel and sdist build, metadata check and clean-wheel install pass;
- PyArrow kernel/type tests pass;
- security, secret scan and dependency audit pass;
- CycloneDX SBOM is generated;
- connector simulation passes and live Kafka/Event Hubs evidence is attached when those transports are claimed qualified;
- 1.0 frozen conformance tests pass;
- release artifacts have SHA-256 checksums and Sigstore provenance/signatures in the protected release workflow.

External-service gates are evidence requirements. They must not be marked passed without their real service/runtime.
