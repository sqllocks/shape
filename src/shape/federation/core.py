import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FederationPolicy:
    minimum_cohort: int = 5
    allowed_classification: str = "INTERNAL"
    epsilon: float | None = None


DEFAULT_POLICY = FederationPolicy()


class FederatedNode:
    def __init__(self, name, shape, policy=DEFAULT_POLICY):
        self.name = name
        self.shape = shape
        self.policy = policy

    def attest(self):
        raw = json.dumps(self.shape, sort_keys=True, default=str).encode()
        return {
            "node": self.name,
            "shape_id": hashlib.sha256(raw).hexdigest(),
            "rows": self.shape.get("rows", 0),
        }


def aggregate(nodes):
    rows = sum(n.shape.get("rows", 0) for n in nodes)
    cols = {}
    for n in nodes:
        for k, v in n.shape.get("columns", {}).items():
            c = cols.setdefault(k, {"count": 0, "weighted_mean": 0.0})
            count = v.get("count", 0) or 0
            c["count"] += count
            c["weighted_mean"] += float(v.get("mean", 0) or 0) * count
    for c in cols.values():
        c["mean"] = c.pop("weighted_mean") / c["count"] if c["count"] else None
    return {"rows": rows, "columns": cols, "attestations": [n.attest() for n in nodes]}


def compare(a, b):
    A = aggregate(a)
    B = aggregate(b)
    return {
        "row_delta": B["rows"] - A["rows"],
        "column_mean_delta": {
            k: B["columns"].get(k, {}).get("mean", 0) - A["columns"].get(k, {}).get("mean", 0)
            for k in set(A["columns"]) | set(B["columns"])
        },
    }
