"""Suite-wide collection rules."""

import importlib.util

# Tests marked `sign` import the optional `cryptography` package ([sign] extra) at module
# level. Without it they cannot be collected, so `pytest -m "not sign"` must not collect them.
collect_ignore = []
if importlib.util.find_spec("cryptography") is None:
    collect_ignore += [
        "security/test_crypto.py",
        "artifact/test_secure.py",
        "security/test_ga_security_extended.py",
    ]
