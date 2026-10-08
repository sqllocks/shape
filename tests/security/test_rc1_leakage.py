import json

from shape.privacy import redact_sensitive
from shape.streaming import TextEvidence


def test_secure_release_removes_sensitive_value_evidence():
    e = TextEvidence()
    for _ in range(10):
        e.update("secret@example.com")
    shape = {"rows": 10, "columns": {"email": {"kind": "text", **e.summary()}}}
    released = redact_sensitive(shape, {"email": "PII"})
    raw = json.dumps(released)
    assert "secret@example.com" not in raw
    assert released["columns"]["email"]["value_evidence_redacted"]
