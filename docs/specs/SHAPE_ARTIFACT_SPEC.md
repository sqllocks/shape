# Shape Artifact Specification — Draft 1

A `.shape` artifact is a safe, bounded container for executable evidence about data behavior.

Normative rules:
1. Readers MUST reject duplicate member names, path traversal, unknown mandatory capabilities, checksum mismatch, and resource-limit violations.
2. Manifest/schema/evidence JSON MUST be UTF-8. Canonical signing input MUST be deterministic and independent of ZIP member order/timestamps.
3. Evidence MUST carry provenance and algorithm/version parameters when approximate.
4. Sensitive evidence MUST retain sensitivity metadata through serialization, diff, history and release.
5. Encryption is envelope-oriented: payload encryption uses authenticated encryption; key management is supplied by a deployment provider.
6. Signatures cover the canonical manifest plus member digests.
7. Readers MUST be forward-compatible with optional extensions and MUST fail closed on unknown required extensions.
8. History identifiers are content-addressed; mutation creates a new artifact/version.
