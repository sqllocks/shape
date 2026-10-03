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

## Stable Python modules

`shape.generation.spec_edit` and `shape.generation.spec_schema` are Stable (`docs/API_STABILITY.md`, "Stable Python modules"). `python scripts/stable_api_compat.py --check` (run by `tests/api/test_stable_api_compat.py` and so by `make check`) compares them with `tests/api/stable_api_baseline.json`.

- After an **additive** change (a new exported name, a new method, a new keyword parameter with a default after the existing ones, a new optional dataclass field with a default at the end), run `python scripts/stable_api_compat.py --write` and commit the baseline. `--write` refuses a breaking change.
- A **breaking change** (anything `--check` reports as `BREAKING`) needs a new major version. Do not edit the baseline to make the check pass; deprecate the old member instead (it keeps working with a `DeprecationWarning` until the next major version).
- A new exported name also goes into the table in `docs/API_STABILITY.md` and into the module's `__all__`.
