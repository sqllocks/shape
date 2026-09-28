"""Multidimensional reconstruction fidelity certificates."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class FidelityDimension:
    name: str
    score: float
    weight: float
    details: dict


@dataclass(frozen=True, slots=True)
class FidelityCertificate:
    score: float
    dimensions: tuple[FidelityDimension, ...]
    degraded: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()

    def to_dict(self):
        return {
            "score": self.score,
            "dimensions": [asdict(x) for x in self.dimensions],
            "degraded": list(self.degraded),
            "unavailable": list(self.unavailable),
        }


def _rel(a, b):
    try:
        return min(1.0, abs(float(a) - float(b)) / max(abs(float(a)), 1e-12))
    except (TypeError, ValueError, OverflowError):
        return 1.0 if a != b else 0.0


def certify_shapes(target, observed, degraded=(), unavailable=()):
    dims = []
    tc = target.get("columns", {})
    oc = observed.get("columns", {})
    schema = len(set(tc) & set(oc)) / max(1, len(set(tc) | set(oc)))
    dims.append(FidelityDimension("schema", schema, 2, {}))
    vals = {"null_behavior": [], "cardinality": [], "numeric_distribution": []}
    for n, t in tc.items():
        o = oc.get(n)
        if not o:
            continue
        vals["null_behavior"].append(1 - _rel(t.get("null_count", 0), o.get("null_count", 0)))
        vals["cardinality"].append(
            1 - _rel(t.get("distinct_estimate", 0), o.get("distinct_estimate", 0))
        )
        if t.get("kind") == "numeric" and o.get("kind") == "numeric":
            es = [
                _rel(t.get(k), o.get(k))
                for k in ("mean", "variance_population", "min", "max")
                if t.get(k) is not None and o.get(k) is not None
            ]
            if es:
                vals["numeric_distribution"].append(1 - sum(es) / len(es))
    for n, x in vals.items():
        if x:
            dims.append(
                FidelityDimension(n, max(0, min(1, sum(x) / len(x))), 1, {"fields": len(x)})
            )
    total = sum(d.score * d.weight for d in dims) / sum(d.weight for d in dims)
    return FidelityCertificate(total, tuple(dims), tuple(degraded), tuple(unavailable))


@dataclass(frozen=True, slots=True)
class PlanItem:
    evidence: str
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReconstructionPlan:
    items: tuple[PlanItem, ...]

    @property
    def executable(self):
        return not any(x.status == "unavailable" for x in self.items)

    def to_dict(self):
        return {"executable": self.executable, "items": [asdict(x) for x in self.items]}


def plan_reconstruction(shape):
    items = []
    for n, c in shape.get("columns", {}).items():
        items.append(
            PlanItem(
                f"column:{n}",
                "preserved",
                "numeric marginal" if c.get("kind") == "numeric" else "categorical/text evidence",
            )
        )
    rel = shape.get("relationships", {})
    for kind in ("correlations", "foreign_keys", "conditionals"):
        for i, _ in enumerate(rel.get(kind, ())):
            items.append(PlanItem(f"{kind}:{i}", "preserved", "supported relationship evidence"))
    for kind in ("temporal", "geographic", "dependencies"):
        if rel.get(kind):
            items.append(
                PlanItem(
                    kind,
                    "unavailable",
                    "captured evidence has no universal reconstruction compiler yet",
                )
            )
    return ReconstructionPlan(tuple(items))


@dataclass(frozen=True, slots=True)
class FidelityResult:
    score: float
    passed: bool
    dimensions: dict


def evaluate_fidelity(reference, observed, tolerance=0.1):
    cert = certify_shapes(reference, observed)
    return FidelityResult(
        cert.score, cert.score >= 1 - float(tolerance), {d.name: d.score for d in cert.dimensions}
    )
