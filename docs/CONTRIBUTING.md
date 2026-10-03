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

Before proposing a feature, see [NOT_BUILDING.md](NOT_BUILDING.md) for what the project has decided not to build. The command line's stability promise is [CLI_STABILITY.md](CLI_STABILITY.md), and what 1.0 requires is [V1_DONE.md](V1_DONE.md).
