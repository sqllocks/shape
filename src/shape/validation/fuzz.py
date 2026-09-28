"""Deterministic malformed artifact corpus."""

from __future__ import annotations

import io
import zipfile


def artifact_cases():
    cases = {}
    cases["not_zip"] = b"not a zip"
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("../x", b"x")
        z.writestr("manifest.json", '{"content_hashes":{}}')
    cases["traversal"] = b.getvalue()
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("manifest.json", '{"content_hashes":{"x":"deadbeef"}}')
        z.writestr("x", b"x")
    cases["bad_hash"] = b.getvalue()
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("manifest.json", b"{")
    cases["bad_json"] = b.getvalue()
    return cases
