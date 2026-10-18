# Shape 1.0 Compatibility Contract — Proposal

Status: experimental.


The 1.0 compatibility boundary consists of:
- versioned Shape-as-Code contract documents;
- `.shape` artifact safety and integrity semantics;
- deterministic generation seed/index behavior where declared deterministic;
- sensitivity/provenance propagation;
- event-time/watermark/checkpoint semantics;
- Pack manifests and reference-asset provenance;
- machine-readable conformance outcomes and CLI exit-code classes.

Incidental Python class layout is not part of the cross-language contract.

Exit codes of the stable CLI operations are the classes of `docs/CLI.md`, which is the source of truth and what the CLI uses: 0 ok; 1 a check failed (drift found, a signature or leak scan failed); 2 bad input (a missing or unreadable file, the wrong kind of file, a bad argument, or an expected error); 3 and above a command's own verdict (a certificate below its threshold, a failed contract, an incompatible change), which each command's `--help` documents. The stability promise for them is `docs/CLI_STABILITY.md`. An earlier draft proposed 64 for invalid invocation and separate codes for quality and fidelity gates; those are not used. Readers fail closed on unknown mandatory capabilities.
