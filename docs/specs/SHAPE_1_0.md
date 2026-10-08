# Shape 1.0 Normative Contract

Shape 1.0 defines a portable behavioral-data contract. Normative keywords MUST, SHOULD and MAY follow RFC 2119 meanings.

A conforming implementation MUST:
1. reject unknown mandatory capabilities;
2. preserve declared sensitivity and MUST NOT implicitly downgrade derived evidence;
3. distinguish exact from approximate evidence and identify approximation algorithms/parameters;
4. reject corrupt/unsafe `.shape` containers before trusting payloads;
5. preserve deterministic seed/index semantics where a generator declares random-access determinism;
6. expose conformance results in machine-readable form;
7. treat source records as data, not metadata to retain inside a Shape unless an explicit extension says otherwise;
8. preserve provenance for externally sourced reference assets;
9. version incompatible semantics rather than silently changing their meaning.

The human-editable Shape-as-Code v1 document is defined by `src/shape/schemas/shape-v1.schema.json` (shipped in the wheel as `shape/schemas/`).
Heavy sketches/evidence MAY reside in the packaged `.shape` artifact rather than the human document.
