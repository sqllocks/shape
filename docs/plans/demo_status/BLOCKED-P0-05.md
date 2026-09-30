# BLOCKED: P0-05 (one deliverable left)

Done and verified in this session: §8.3 deletions (no path exists), T-07/T-08
dependency and extras changes in `pyproject.toml`, sdist `include` cleanup, claims grep
(returns nothing), fresh venv without `cryptography` imports `shape`, and
`pytest -m "not emulator and not live"` (788 passed).

Not done: marking the cryptography-dependent tests with `@pytest.mark.sign`. The
permission guard refused the edit to security test files. The lead needs to add
`pytestmark = pytest.mark.sign` after the imports of:

- `tests/security/test_crypto.py`
- `tests/artifact/test_secure.py`
- `tests/security/test_ga_security_extended.py`

These import `cryptography` (or `shape.security.crypto`) at module top, so in a venv
without it they fail at collection. The lead should also add a `tests/conftest.py`
`collect_ignore` for those three files when `cryptography` is missing, so that
`pytest -m "not sign"` collects cleanly. Then check
`grep -rl "cryptography\|security.crypto\|artifact.secure" tests --include=*.py` for
others, and run `pytest -m "not sign"` in a venv without `cryptography`. Then P0-05 can
be set to `done`. P0-06 and P0-07 depend on P0-05, so I have not started them.
