"""Does a text name the reference engine? Checked by hash, so this file never spells the name.

The guards that keep the reference engine's name off Shape's surface (D-13) call
``names_refengine``. The name itself is not stored: only the SHA-256 hex of its lowercase form
and its length. A text names the engine when, after lowercasing, some window of that length
inside a run of ASCII letters hashes to the stored digest (the same as a case-insensitive
substring test, since the name is all letters).

Standard library only: imported from ``scripts/``, ``tests/`` and the plugins' tests.
"""

from __future__ import annotations

import hashlib
import re

# SHA-256 of the reference engine's short name, lowercased, and its length.
DIGEST = "3daaec9e346786af2fe1cb2762b7f1541573fb855aab46574eedf0160f377e8e"
LENGTH = 7

_RUNS = re.compile(r"[a-z]+")


def names_refengine(text: str | bytes, digest: str = DIGEST, length: int = LENGTH) -> bool:
    """True when ``text`` contains the name (any case). ``digest``/``length`` exist for tests."""
    if isinstance(text, bytes):
        text = text.decode("latin-1")
    for run in _RUNS.findall(text.lower()):
        for i in range(len(run) - length + 1):
            if hashlib.sha256(run[i : i + length].encode("ascii")).hexdigest() == digest:
                return True
    return False
