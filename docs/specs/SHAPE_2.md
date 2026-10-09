# Shape by SQLLocks — Normative Contract, version 2

Status: experimental.


Status: draft for 2.0. Replaces the 1.x contract (`SHAPE_1_0.md`). The model and file format
are described in `SHAPE_MODEL_V2.md`; the schema is `src/shape/schemas/shape-v2.schema.json`.

Every normative statement below is one bullet with an identifier and carries exactly one
capitalised normative keyword (the must and must-not forms of RFC 2119). `shape conformance` runs one test for each identifier and
reports the results as JSON; `scripts/check_conformance_coverage.py` counts the statements in
this file and fails unless every one of them has a test and every test has a statement.
SHOULD and MAY text, where it appears, is advice and is not tested.

## The model

- **SH2-001** A Shape document MUST declare `schema_version` 2.
- **SH2-002** A reader MUST reject a Shape document that does not conform to
  `shape-v2.schema.json`.
- **SH2-003** A v1 capture MUST be migrated to v2 on read with every field it carried kept.

## Evidence

- **SH2-004** A figure that is an estimate MUST identify itself as approximate.
- **SH2-005** An estimate MUST carry an error model naming its algorithm and error bound.
- **SH2-006** Exact mode MUST report exact distinct counts.
- **SH2-007** A Shape MUST NOT retain original source rows.
- **SH2-032** Profiling the same input in exact mode MUST produce the same document every time.

## The `.shape` artifact

- **SH2-008** A writer MUST write `.shape` format version 2.
- **SH2-009** A writer MUST NOT write `.shape` format version 1.
- **SH2-010** A reader MUST verify the content hash of every component before using it.
- **SH2-011** A reader MUST reject an archive member whose path is unsafe.
- **SH2-012** A reader MUST reject an archive whose content identity differs from its manifest.
- **SH2-013** A reader MUST report every defect of a file as an `ArtifactError`.
- **SH2-014** NaN, infinity and tuples MUST round-trip through a `.shape` file unchanged.
- **SH2-015** A writer MUST NOT accept credential material.
- **SH2-016** A writer MUST give the same content identity to the same model.

## Capabilities and classification

- **SH2-017** A reader MUST reject an unknown mandatory capability.
- **SH2-018** A classification MUST NOT be downgraded implicitly.

## Contracts and compatibility

- **SH2-019** A `unique` check MUST use the exact distinct count when the evidence is exact.
- **SH2-020** A `unique` check MUST NOT fail a column whose estimate, widened by its error
  bound, reaches the number of non-null rows.
- **SH2-021** The null rate of a table with zero rows MUST be 0.
- **SH2-022** Backward compatibility MUST allow an added column.
- **SH2-023** Backward compatibility MUST NOT allow a removed column.
- **SH2-024** A change of type family MUST be reported as incompatible.

## Drift

- **SH2-025** An added or a removed column MUST be reported with drift score 1.

## Query

- **SH2-026** `relationship(a, b)` MUST match the relationship whose endpoints are a and b.
- **SH2-027** `relationship(x, x)` MUST NOT match a relationship that only mentions x.
- **SH2-028** An unsupported query MUST fail closed with `ShapeQueryError`.
- **SH2-029** A query MUST NOT evaluate code.

## Conformance

- **SH2-030** Conformance results MUST be machine-readable.
- **SH2-031** `shape conformance` MUST run one test for each normative statement of this file.

## Compatibility boundary

Within 2.x, the following interfaces are governed and change only with a new major version: the
model and its schema; the `.shape` format and its migrations; the Shape Query grammar; the
contract compatibility modes (backward, forward, full); the classification rules; and the
machine-readable conformance output. Existing fields keep their meaning; new optional fields may
be added, because readers ignore what they do not know. Incompatible semantics need a new major
version or a new, explicitly versioned capability.

## Stability levels

`shape.artifact`, `shape.spec`, `shape.contracts`, `shape.query`, `shape.drift`, `shape.diff`,
`shape.quality`, `shape.registry` and the conformance interfaces of `shape.validation` are
stable. Modules documented as experimental are outside the promise.

## Change control

A change to a governed interface needs a compatibility test showing that older artifacts and
contracts still read, a schema or conformance update where it applies, a changelog entry, and a
classification and security review when evidence or serialization changes.
