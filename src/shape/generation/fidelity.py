"""Multidimensional reconstruction fidelity certificates, and the reconstruction plan.

:func:`plan_reconstruction` says, for every piece of evidence it is given, whether data generated
from it keeps that evidence. For a profile (``shape.profile`` output) the plan comes from the
schema ``shape generate --from`` fits (:func:`shape.generation.fit.fit_schema`); for portable
evidence documents (``shape capture`` output) each item is checked against what the generator can
build from it, so nothing is reported as preserved without being checked.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FidelityDimension:
    name: str
    score: float
    weight: float
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FidelityCertificate:
    score: float
    dimensions: tuple[FidelityDimension, ...]
    degraded: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "dimensions": [asdict(x) for x in self.dimensions],
            "degraded": list(self.degraded),
            "unavailable": list(self.unavailable),
        }


def _rel(a: Any, b: Any) -> float:
    try:
        return min(1.0, abs(float(a) - float(b)) / max(abs(float(a)), 1e-12))
    except (TypeError, ValueError, OverflowError):
        return 1.0 if a != b else 0.0


def certify_shapes(
    target: Mapping[str, Any],
    observed: Mapping[str, Any],
    degraded: tuple[str, ...] = (),
    unavailable: tuple[str, ...] = (),
) -> FidelityCertificate:
    dims = []
    tc = target.get("columns", {})
    oc = observed.get("columns", {})
    schema = len(set(tc) & set(oc)) / max(1, len(set(tc) | set(oc)))
    dims.append(FidelityDimension("schema", schema, 2, {}))
    vals: dict[str, list[float]] = {
        "null_behavior": [],
        "cardinality": [],
        "numeric_distribution": [],
    }
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
    """One piece of evidence. ``status`` is ``preserved`` (generated data keeps it, to sampling
    error), ``approximate`` (kept only roughly: the reason says how), ``not_modelled`` (generation
    ignores it; the reason says what the data does instead) or ``unavailable`` (cannot be
    generated at all)."""

    evidence: str
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReconstructionPlan:
    items: tuple[PlanItem, ...]

    @property
    def executable(self) -> bool:
        """Whether data can be generated at all (nothing is ``unavailable``)."""
        return not any(x.status == "unavailable" for x in self.items)

    def by_status(self, status: str) -> tuple[PlanItem, ...]:
        return tuple(x for x in self.items if x.status == status)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for x in self.items:
            out[x.status] = out.get(x.status, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "executable": self.executable,
            "counts": self.counts(),
            "items": [asdict(x) for x in self.items],
        }


def _is_profile(shape: Any) -> bool:
    if hasattr(shape, "to_dict") and type(shape).__module__.startswith("shape.profile"):
        return True
    if not isinstance(shape, Mapping):
        return False
    if isinstance(shape.get("tables"), Mapping) and shape["tables"]:
        return all(isinstance(t, Mapping) and "row_count" in t for t in shape["tables"].values())
    cols = shape.get("columns")
    return (
        "row_count" in shape
        and isinstance(cols, Mapping)
        and any(isinstance(c, Mapping) and "dtype" in c for c in cols.values())
    )


def _numeric_status(name: str, col: Mapping[str, Any]) -> PlanItem:
    missing = [k for k in ("mean", "variance_population") if col.get(k) is None]
    if missing:
        return PlanItem(
            f"column:{name}",
            "approximate",
            f"numeric evidence without {' and '.join(missing)}: a constant is generated",
        )
    return PlanItem(
        f"column:{name}",
        "approximate",
        "numeric marginal: a normal with the evidence's mean and variance, clipped to its range "
        "(the shape of the distribution is not kept)",
    )


def _text_status(name: str, col: Mapping[str, Any]) -> PlanItem:
    top = col.get("topk") or []
    if not any(isinstance(x, (list, tuple)) and len(x) >= 2 for x in top):
        return PlanItem(
            f"column:{name}",
            "not_modelled",
            "no top values in the evidence: placeholder values `value_<n>` are generated",
        )
    return PlanItem(
        f"column:{name}",
        "approximate",
        "categorical: the evidence's top values with their frequencies (values outside them are "
        "not generated)",
    )


def _legacy_plan(shape: Mapping[str, Any]) -> ReconstructionPlan:
    columns: Mapping[str, Any] = shape.get("columns", {})
    items: list[PlanItem] = []
    for n, c in columns.items():
        items.append(_numeric_status(n, c) if c.get("kind") == "numeric" else _text_status(n, c))
        if int(c.get("null_count") or 0) and not c.get("count"):
            items.append(
                PlanItem(f"column:{n}.nulls", "approximate", "no value count: all rows assumed")
            )
    rel = shape.get("relationships", {}) or {}
    for i, c in enumerate(rel.get("correlations", ())):
        a, b = c.get("source"), c.get("target")
        ok = a in columns and b in columns and columns[a].get("kind") == columns[b].get("kind")
        numeric = ok and columns[b].get("kind") == "numeric"
        if numeric:
            items.append(
                PlanItem(
                    f"correlations:{i}",
                    "approximate",
                    f"linear dependence of {b} on {a} with rho {c.get('rho')} (a Gaussian "
                    "shortcut; the marginal of the target becomes normal)",
                )
            )
        else:
            items.append(
                PlanItem(
                    f"correlations:{i}",
                    "unavailable",
                    f"{a!r} and {b!r} must both be numeric columns of the evidence",
                )
            )
    for i, f in enumerate(rel.get("foreign_keys", ())):
        if f.get("field") and int(f.get("parent_count") or 0) > 0:
            items.append(
                PlanItem(
                    f"foreign_keys:{i}",
                    "approximate",
                    "keys drawn uniformly from 0..parent_count-1; the fan-out is not modelled",
                )
            )
        else:
            items.append(
                PlanItem(f"foreign_keys:{i}", "unavailable", "needs `field` and `parent_count`")
            )
    for i, f in enumerate(rel.get("conditionals", ())):
        if f.get("when") in columns and f.get("field") in columns:
            items.append(
                PlanItem(
                    f"conditionals:{i}",
                    "approximate",
                    "a normal with the rule's mean and stddev where the condition holds",
                )
            )
        else:
            items.append(
                PlanItem(
                    f"conditionals:{i}", "unavailable", "its `when` and `field` must be columns"
                )
            )
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


def plan_reconstruction(shape: Any) -> ReconstructionPlan:
    """What data generated from ``shape`` keeps and what it does not (a profile, its dictionary, or
    a portable evidence document)."""
    if _is_profile(shape):
        from shape.generation.fit import fit_schema

        return fit_schema(shape).plan
    return _legacy_plan(shape)


@dataclass(frozen=True, slots=True)
class FidelityResult:
    score: float
    passed: bool
    dimensions: dict[str, float]


def evaluate_fidelity(
    reference: Mapping[str, Any], observed: Mapping[str, Any], tolerance: float = 0.1
) -> FidelityResult:
    cert = certify_shapes(reference, observed)
    return FidelityResult(
        cert.score, cert.score >= 1 - float(tolerance), {d.name: d.score for d in cert.dimensions}
    )
