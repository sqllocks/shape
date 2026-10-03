# 1.0 Release Policy
Semantic Versioning applies to the normative Shape contract and stable CLI exit classes. Patch releases may fix defects without weakening safety. Minor releases may add optional capabilities. New mandatory semantics require an explicitly versioned capability and may require a major release. Deprecations require a migration path.

Persisted files, and what a release must keep reading, are governed by [the state and compatibility policy](specs/STATE_AND_COMPATIBILITY.md): a format version is dropped only in a new major release, announced a release cycle ahead in the changelog under "Deprecated", and only if the offline `shape-migrate` path still reads it.
