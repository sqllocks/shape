# Contributing

Changes must preserve the frozen Shape 1.0 compatibility contract.

Before submitting:
```bash
python -m pytest -q
python scripts/check_requirements.py
python scripts/check_secrets.py
python -m compileall -q src/shape
```

Changes to artifacts, evidence, generation, classification or query parsing require corresponding conformance/security tests. Performance-sensitive changes require before/after evidence from the relevant `rq/` benchmark. Do not commit credentials, production data or proprietary reference assets.

A new call to a Fabric or OneLake API, a new Fabric item type or a new Fabric runtime version needs its row in `docs/FABRIC_PLATFORM.md` first (status, Microsoft Learn source, date). `tests/docs/test_fabric_platform.py` fails on an unlisted surface, and on one listed as preview or retired.
