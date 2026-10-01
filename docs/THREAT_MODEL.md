# Threat Model

Protected assets include source data, sensitive profile evidence, synthetic outputs, credentials, signing/encryption keys, history and reference assets.

Trust boundaries: input datasets; `.shape` artifacts; Packs/reference assets; plugins; connectors; filesystem; network services; CI/release pipeline.

Primary threats: raw-value leakage, re-identification, artifact traversal/bombs/corruption, malicious plugins, dependency compromise, secret exfiltration, tampered reference data, ambiguous geographic resolution, denial of service through cardinality/nesting, replay/checkpoint corruption, downgrade/format confusion and forged provenance.

Controls implemented in the reference today: artifact path/size/hash validation (the `.shape` reader fails closed), optional Ed25519 signing of artifacts (`shape sign`, `--sign`, `--verify`; see `docs/SIGNING.md`; a forged artifact with rewritten hashes fails verification, enforced by `tests/artifact/test_signing.py`), authenticated encryption, sensitivity labels, and secret scanning. Plugins are **trusted, in-process code** (see `docs/plugins/trust-model.md`). Release-policy enforcement, bounded profiling and signing of artifacts by default (signing is opt-in today) are in progress (see `docs/plans/COMPLETION_PLAN.md`).

Deployment controls still required: OS/container confinement of the whole process, secret manager/KMS/HSM, egress policy, RBAC, audit logging, dependency provenance, backup/recovery, target-service authentication and independent assessment.
