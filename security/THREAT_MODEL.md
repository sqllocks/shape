# Initial Threat Model
Protected assets: source data, Shape evidence/model, labels, History/deltas, generated data, credentials, keys, reference assets and audit records.

Threats: reconstruction/membership inference; rare-value leakage; malicious artifacts; tampering; unauthorized downgrade/export; log/plugin exfiltration; credential theft; supply-chain compromise; resource exhaustion.

Trust boundaries: Source→Core, Core→Artifact, Core→Plugin, Core→Sink, Protected Zone→Release/Transfer.

Mitigations are specified as testable requirements; deployment accreditation remains external.
