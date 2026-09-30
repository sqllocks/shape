"""Evidence-release policy for classified Shape artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
        "q25",
        "q50",
        "q75",
        "enum_values",
        "value_counts_ext",
        "pattern_examples",
        "distribution_params",
    }
)


@dataclass(frozen=True, slots=True)
class ReleaseDecision:
    allowed: bool
    shape: dict[str, Any]
    removed: tuple[str, ...]
    reason: str


def release_for(
    shape: dict[str, Any],
    classifications: dict[str, str],
    target: str = "PUBLIC",
    minimum_cohort: int = 5,
    source_classification: str | None = None,
) -> ReleaseDecision:
    """Apply the release policy: column redaction, or an artifact-level denial.

    A source classified above ``target`` is denied (``source_exceeds_target``), as is an
    artifact with fewer rows than ``minimum_cohort`` (``cohort_below_minimum``).
    """
    target = str(target).upper()
    if target not in LEVELS:
        raise ValueError("unknown target classification")
    if source_classification is not None:
        source_classification = str(source_classification).upper()
        if source_classification not in LEVELS:
            raise ValueError("unknown source classification")
    if source_classification is not None and LEVELS[source_classification] > LEVELS[target]:
        return ReleaseDecision(False, {}, ("shape",), "source_exceeds_target")
    rows = shape.get("rows")
    if isinstance(rows, int) and not isinstance(rows, bool) and rows < minimum_cohort:
        return ReleaseDecision(False, {}, ("shape",), "cohort_below_minimum")
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
    out["release_policy"] = {
        "target": target,
        "minimum_cohort": minimum_cohort,
        "source_classification": source_classification,
    }
    return ReleaseDecision(True, out, tuple(sorted(set(removed))), "policy_applied")
