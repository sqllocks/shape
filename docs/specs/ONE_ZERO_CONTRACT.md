# Shape 1.0 Compatibility Contract — Proposal

The 1.0 compatibility boundary consists of:
- versioned Shape-as-Code contract documents;
- `.shape` artifact safety and integrity semantics;
- deterministic generation seed/index behavior where declared deterministic;
- sensitivity/provenance propagation;
- event-time/watermark/checkpoint semantics;
- Pack manifests and reference-asset provenance;
- machine-readable conformance outcomes and CLI exit-code classes.

Incidental Python class layout is not part of the cross-language contract.

Exit codes proposed for stable CLI operations: 0 success/pass; 1 operational/conformance failure; 2 quality gate failure; 3 fidelity gate failure; 64 invalid invocation/configuration. Readers fail closed on unknown mandatory capabilities.
