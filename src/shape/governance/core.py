import hashlib
import json
import time


def attest(subject, provenance, approvals=()):
    body = {
        "subject": subject,
        "provenance": provenance,
        "approvals": list(approvals),
        "created_at": time.time(),
    }
    body["id"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return body


class ApprovalGate:
    def __init__(self, required=2):
        self.required = required
        self.approvals = set()

    def approve(self, principal):
        self.approvals.add(principal)

    @property
    def passed(self):
        return len(self.approvals) >= self.required
