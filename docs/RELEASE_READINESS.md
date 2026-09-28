# Release Readiness — Expanded Reference 0.5

Shape now has an executable reference surface spanning profiling, generation, relational/temporal generation, artifacts, crypto, History, quality, privacy, streaming, Location/Geo, Packs, ETL, connectors, plugins, CLI, adversarial corpus, specifications and benchmark tooling.

Current local evidence:
- 48 tests passed, 0 failed.
- 38 requirement-registry entries pass integrity checks.
- Basic secret scan passes.
- 200k-row local baseline: ~58k generated rows/sec and ~414k candidate-key-profile rows/sec on this execution host.

Not represented as locally validated:
- Arrow-specific tests (PyArrow unavailable).
- production broker/database integration without target systems/credentials.
- independent security/privacy assessment.
- customer/government authorization/accreditation.
- FIPS validation of the crypto provider selected by a deployment.
- cross-language interoperability until an independent implementation exists.
- production-hardware scale claims.

These are evidence boundaries, not reasons to stop implementing unrelated code.
