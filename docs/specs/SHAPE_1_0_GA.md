# Shape by SQLLocks — Normative Contract (draft)

Status: **FROZEN for 1.x compatibility**.

Shape 1.0 defines a portable behavioral-data contract. RFC 2119 MUST/SHOULD/MAY meanings apply.

## Compatibility boundary

The following are governed 1.x interfaces and MUST NOT change incompatibly without a new major specification version:

- human-editable Shape-as-Code document version `1`;
- `.shape` artifact `format=shape`, `format_version=1`;
- mandatory vs optional capability semantics;
- classification non-downgrade semantics and sanitized-derivative rules;
- deterministic seed/index semantics when random-access determinism is declared;
- contract compatibility modes: backward, forward, full;
- Location and LocationScope semantic meaning;
- DomainDefinition and reference-asset identity/version semantics;
- safe Shape Query grammar and fail-closed behavior;
- generation relationship semantics for primary, composite and foreign keys;
- artifact content identity and checksum verification;
- conformance result machine-readability.

## Evolution rules

1. Readers MUST reject unknown mandatory capabilities.
2. Readers MUST tolerate unknown optional capabilities/extensions when safe to ignore.
3. Existing fields MUST NOT silently change meaning in 1.x.
4. New optional fields MAY be added if old readers can safely ignore them.
5. Incompatible semantics require Shape 2.0 or a new explicitly versioned capability.
6. Classification MUST never be implicitly downgraded.
7. Artifact readers MUST verify integrity before trusting payload content.
8. Original source rows MUST NOT be retained unless an explicit extension declares that behavior.
9. Reference assets MUST preserve name, version and content identity/provenance.
10. Approximate evidence MUST identify itself as approximate and preserve algorithm/parameter identity where required for interpretation.

## Stability levels

`shape.artifact`, `shape.contracts`, `shape.location`, `shape.packs`, `shape.query`, `shape.generation` public 1.0 contracts, `shape.registry`, and `shape.validation` conformance interfaces are **Stable**.

Modules explicitly documented as `experimental` remain outside the 1.x compatibility promise.

## Change control

Any proposed 1.x change to a frozen interface requires:
- a compatibility test demonstrating old 1.0 artifacts/contracts remain valid;
- a schema/conformance update where applicable;
- a changelog entry;
- explicit classification/security review when evidence or serialization changes.
