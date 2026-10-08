"""Evidence-release policy for classified Shape artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cells import columns_of, suppress_column_cells, suppress_joint
from .classification import LEVELS as LEVELS

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
        "placeholders",
    }
)


def _release_joint(
    joint: dict[str, Any], hidden: set[str], k: int, removed: list[str]
) -> dict[str, Any]:
    """The ``joint`` block of a profile under the policy.

    Its conditional tables and dependency violations carry real labels with counts: an entry that
    names a column above the target is dropped, and a cohort or cell of fewer than ``k`` rows is
    withheld, as for a column's own value counts. Associations hold names and measures only."""
    out = dict(joint)
    conditionals: list[Any] = []
    for i, c in enumerate(joint.get("conditionals") or []):
        if not isinstance(c, dict):
            removed.append(f"joint.conditionals.{i}")
            continue
        if c.get("given") in hidden or c.get("target") in hidden:
            removed.append(f"joint.conditionals.{c.get('given')}->{c.get('target')}")
            continue
        table: dict[str, Any] = {}
        for label, row in (c.get("table") or {}).items():
            n = int(row.get("n", 0) or 0)
            p = {t: v for t, v in (row.get("p") or {}).items() if round(float(v) * n) >= k}
            if n >= k and p:
                table[label] = {**row, "p": p}
                if p != (row.get("p") or {}):
                    removed.append(f"joint.conditionals.{i}.table.{label}.p")
            else:
                removed.append(f"joint.conditionals.{i}.table.{label}")
        if table:
            conditionals.append({**c, "table": table})
        else:
            removed.append(f"joint.conditionals.{i}")
    dependencies: list[Any] = []
    for d in joint.get("dependencies") or []:
        if not isinstance(d, dict):
            continue
        entry = dict(d)
        touches = hidden & {*(d.get("determinant") or []), d.get("dependent")}
        groups: list[Any] = []
        for j, v in enumerate(d.get("violations") or []):
            where = f"joint.dependencies.{len(dependencies)}.violations.{j}"
            if touches or int(v.get("rows", 0) or 0) < k:
                removed.append(where)
                continue
            counts = {a: b for a, b in (v.get("dependent_values") or {}).items() if b >= k}
            if counts != (v.get("dependent_values") or {}):
                removed.append(where)
            if counts:
                groups.append({**v, "dependent_values": counts})
        if d.get("violations"):
            entry["violations"] = groups
        dependencies.append(entry)
    out["conditionals"] = conditionals
    out["dependencies"] = dependencies
    return out


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
    removed: list[str] = []
    out = {
        k: v for k, v in shape.items() if k not in {"columns", "raw_values", "samples", "examples"}
    }
    out["columns"] = {}
    hidden = {
        name
        for name, c in columns_of(shape).items()
        if LEVELS.get(str(classifications.get(name, c.get("classification", "PUBLIC"))).upper(), 0)
        > LEVELS[target]
    }
    for name, c in columns_of(shape).items():
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
        else:
            x, _, gone = suppress_column_cells(x, minimum_cohort, count)
            removed.extend(f"columns.{name}.{k}" for k in gone)
        out["columns"][name] = x
    if isinstance(out.get("joint"), dict):
        covered = {str(c) for c in out["joint"].get("columns") or ()}
        if hidden and covered and covered <= hidden:
            # every column the joint analysis covers is above the target: nothing in the block
            # can be released (#650, G7-eval), so it is withheld whole
            del out["joint"]
            removed.append("joint")
        else:
            joint = _release_joint(out["joint"], hidden, minimum_cohort, removed)
            # then the minimum cell count on what is left, and the unchecked entries (#650)
            out["joint"], gone = suppress_joint(joint, minimum_cohort)
            removed.extend(gone)
    elif out.get("joint") is not None:
        del out["joint"]  # not a joint block this policy can read: withheld
        removed.append("joint")
    out["release_policy"] = {
        "target": target,
        "minimum_cohort": minimum_cohort,
        "source_classification": source_classification,
    }
    return ReleaseDecision(True, out, tuple(sorted(set(removed))), "policy_applied")
