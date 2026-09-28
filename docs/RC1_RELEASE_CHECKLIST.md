# RC-1 Release Checklist

## Must pass before publishing the RC artifact
- [x] RC public API/version freeze
- [x] `.shape` v1 format and corruption checks
- [x] bounded streaming evidence across current Shape evidence categories
- [x] >1M rows/sec text/semantic local engine target
- [x] >1M rows/sec mixed full-evidence local engine target
- [x] deterministic bounded partitioned keyed state
- [x] privacy redaction regression
- [x] unit/conformance/property qualification locally
- [ ] build wheel + sdist in clean supported Python environments
- [ ] install wheel in clean Linux/macOS/Windows jobs
- [ ] run PyArrow/kernel/type test paths with declared dependencies installed
- [ ] live Kafka qualification suite
- [ ] live Azure Event Hubs qualification suite
- [ ] dependency audit/SBOM in networked CI
- [ ] sign/provenance-attest release artifacts

No unchecked item may be represented as having passed.
