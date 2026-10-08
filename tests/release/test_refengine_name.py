"""scripts/refengine_name.py: the hash check every name guard uses flags a text that names it.

The positive case is built from ``REFENGINE_NAME`` when it is set (checked against the stored
digest), and always from a test-only digest of a dummy word, so the test never skips and this
file never spells the name.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from refengine_name import DIGEST, LENGTH, names_refengine  # noqa: E402

DUMMY = "zebrafy"
DUMMY_DIGEST = hashlib.sha256(DUMMY.encode()).hexdigest()


def test_flags_a_text_that_names_it_in_any_case():
    def flags(text: str | bytes) -> bool:
        return names_refengine(text, DUMMY_DIGEST, len(DUMMY))

    assert flags("a ZEBRAFY here") and flags("x-Zebrafy_y") and flags(b"the zebrafyer")
    assert flags("unzebrafy")  # inside a longer run of letters, like a substring test
    assert not flags("zebra fy") and not flags("zebrafx") and not flags("") and not flags("zebr")

    # the stored digest: never the neutral stand-in; the real name when REFENGINE_NAME is set
    name = os.environ.get("REFENGINE_NAME")
    assert len(DIGEST) == 64 and LENGTH > 0
    assert not names_refengine("nothing to see here; refengine is the neutral stand-in")
    if name:
        assert len(name) == LENGTH
        assert hashlib.sha256(name.lower().encode()).hexdigest() == DIGEST
        assert names_refengine(f"made by {name.title()} v3") and names_refengine(name.upper())
        assert names_refengine(f"x{name}y".encode())
