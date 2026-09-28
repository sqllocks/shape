# Shape GA security qualification — local evidence

## Threat model exercised
The local GA security gate treats `.shape` files, manifests, query expressions, package metadata and classification transitions as untrusted input. It explicitly tests credential leakage, archive traversal, duplicate/unexpected members, checksum tampering, compression/resource attacks, malformed hashes/manifests, oversized/deep structures, non-finite numeric payloads, query injection, classification downgrade, sanitized derivative handling, and authenticated-encryption/signature tampering.

## Controls
- Strict archive member allow-list from manifest hashes.
- Cross-platform path traversal rejection, including backslash/drive/NUL variants.
- Per-member, aggregate-size, member-count and compression-ratio limits.
- SHA-256 component identity verification.
- Canonical JSON rejects NaN/Infinity; structure validator also rejects non-finite values.
- Structure depth/string/container limits.
- Secret scanning covers private keys, AWS-style access keys, GitHub tokens, Azure connection strings, bearer tokens and common password/API-key assignments.
- Artifact writer scans both Shape content and metadata for credential material.
- Artifact classification is validated against the supported taxonomy.
- Classification-aware release strips value-bearing evidence and tight numeric bounds for higher-classification fields before downgrade.
- AES-256-GCM authenticated encryption and Ed25519 signatures are tamper-tested.
- Shape Query remains a constrained parser rather than arbitrary Python evaluation.

## Local results
- Dedicated security/artifact suite: 49 passed.
- Full regression suite after hardening: 199 passed.
- Deterministic security fuzz smoke: 500 structured-input mutations + 50 malformed archive mutations, 0 harness failures.
- Existing streaming fuzz regression: 100 trials, 0 failures.
- Repository secret scan: passed after excluding deliberate synthetic security fixtures.
- Python compile gate: passed.

## External evidence not claimable here
A current third-party CVE audit/SBOM, independent penetration test, OS sandbox/ACL verification, HSM/KMS integration, FIPS-validated cryptographic-module claims, classified-network accreditation, and production identity/RBAC testing require their real environments or independent assessors. This repository does not claim those certifications from local tests.
