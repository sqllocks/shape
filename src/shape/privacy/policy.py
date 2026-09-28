"""Evidence-release policy for classified Shape artifacts."""

from __future__ import annotations

from dataclasses import dataclass

LEVELS = {"PUBLIC": 0, "INTERNAL": 1, "SENSITIVE": 2, "PII": 2, "SECRET": 3, "TOP_SECRET": 4}
# Any evidence capable of carrying original values or tight value bounds is stripped on downgrade.
VALUE_KEYS = frozenset(
    {
        "topk",
        "examples",
        "samples",
        "values",
        "sample_values",
        "min_value",
        "max_value",
        "min",
        "max",
        "quantiles",
        "histogram",
        "frequent_items",
    }
)


@dataclass(frozen=True, slots=True)
class ReleaseDecision:
    allowed: bool
    shape: dict
    removed: tuple[str, ...]
    reason: str


def release_for(
    shape, classifications, target="PUBLIC", minimum_cohort=5, source_classification=None
):
    target = str(target).upper()
    if target not in LEVELS:
        raise ValueError("unknown target classification")
    if source_classification is not None:
        source_classification = str(source_classification).upper()
        if source_classification not in LEVELS:
            raise ValueError("unknown source classification")
    removed = []
    out = {
        k: v for k, v in shape.items() if k not in {"columns", "raw_values", "samples", "examples"}
    }
    out["columns"] = {}
    for name, c in shape.get("columns", {}).items():
        label = str(classifications.get(name, c.get("classification", "PUBLIC"))).upper()
        if label not in LEVELS:
            raise ValueError(f"unknown classification {label}")
        x = dict(c)
        x["classification"] = label
        if LEVELS[label] > LEVELS[target]:
            for k in tuple(x):
                if k in VALUE_KEYS:
                    x.pop(k, None)
                    removed.append(f"columns.{name}.{k}")
            x["value_evidence_redacted"] = True
        count = int(x.get("count", shape.get("rows", 0)) or 0)
        if count < minimum_cohort:
            keep = {
                "kind": x.get("kind"),
                "classification": label,
                "suppressed": True,
                "reason": "cohort_below_minimum",
            }
            removed.extend(f"columns.{name}.{k}" for k in x if k not in keep)
            x = keep
        out["columns"][name] = x
    sanitized = bool(source_classification and LEVELS[source_classification] > LEVELS[target])
    out["release_policy"] = {
        "target": target,
        "minimum_cohort": minimum_cohort,
        "source_classification": source_classification,
        "sanitized_derivative": sanitized,
    }
    return ReleaseDecision(True, out, tuple(sorted(set(removed))), "policy_applied")
