import hashlib
import hmac
import json
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant: str
    roles: tuple[str, ...] = ()


class Authorizer:
    def __init__(self, rules=None):
        self.rules = rules or {"admin": {"*"}, "reader": {"read"}, "writer": {"read", "write"}}

    def allowed(self, p, action):
        return any(
            "*" in self.rules.get(r, set()) or action in self.rules.get(r, set()) for r in p.roles
        )


class KeyRing:
    def __init__(self):
        self.keys = {}
        self.active = None

    def rotate(self, key_id, key):
        self.keys[key_id] = bytes(key)
        self.active = key_id
        return key_id

    def revoke(self, key_id):
        self.keys.pop(key_id, None)
        self.active = None if self.active == key_id else self.active

    def get(self, key_id=None):
        return self.keys[key_id or self.active]


class AuditChain:
    def __init__(self, key=b"shape-audit"):
        self.key = key
        self.events = []
        self.head = "0" * 64

    def append(self, event):
        body = json.dumps(
            {"prev": self.head, "time": time.time(), "event": event},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        sig = hmac.new(self.key, body, hashlib.sha256).hexdigest()
        self.head = hashlib.sha256(body + sig.encode()).hexdigest()
        self.events.append((body, sig, self.head))
        return self.head

    def verify(self):
        prev = "0" * 64
        for body, sig, _head in self.events:
            if not hmac.compare_digest(sig, hmac.new(self.key, body, hashlib.sha256).hexdigest()):
                return False
            if json.loads(body)["prev"] != prev:
                return False
            prev = hashlib.sha256(body + sig.encode()).hexdigest()
        return prev == self.head
