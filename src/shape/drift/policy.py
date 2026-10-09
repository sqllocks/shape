from dataclasses import dataclass

from .core import compare


@dataclass(frozen=True, slots=True)
class DriftGate:
    passed: bool
    violations: tuple[dict, ...]

    def to_dict(self):
        return {"passed": self.passed, "violations": list(self.violations)}


def gate(before, after, default_max=0.25, overrides=None):
    overrides = overrides or {}
    bad = []
    for d in compare(before, after):
        if d.kind in ("measure_change", "role_change"):
            continue
        limit = float(overrides.get(d.path, default_max))
        if d.score > limit:
            bad.append({"path": d.path, "score": d.score, "limit": limit, "reason": d.reason})
    return DriftGate(not bad, tuple(bad))
