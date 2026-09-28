# Threat Model

Protected assets include source data, sensitive profile evidence, synthetic outputs, credentials, signing/encryption keys, history and reference assets.

Trust boundaries: input datasets; `.shape` artifacts; Packs/reference assets; plugins; connectors; filesystem; network services; CI/release pipeline.

Primary threats: raw-value leakage, re-identification, artifact traversal/bombs/corruption, malicious plugins, dependency compromise, secret exfiltration, tampered reference data, ambiguous geographic resolution, denial of service through cardinality/nesting, replay/checkpoint corruption, downgrade/format confusion and forged provenance.

Controls implemented in the reference: bounded profiling paths, privacy measurements/detection, sensitivity taxonomy, authenticated encryption/signatures, artifact path/size/hash validation, checksummed reference assets, deny-by-default plugin capabilities, process isolation/timeouts, explicit connector registry, deterministic checkpoints, secret scanning and executable conformance.

Deployment controls still required: OS/container sandboxing, secret manager/KMS/HSM, egress policy, RBAC, audit logging, dependency provenance, backup/recovery, target-service authentication and independent assessment.
