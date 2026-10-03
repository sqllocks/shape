"""Release controls: cell suppression, differencing risk and value redaction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .cells import suppress_column_cells
from .classification import DEFAULT_TAXONOMY
from .policy import VALUE_KEYS


@dataclass(frozen=True, slots=True)
class SuppressionPolicy:
    """``min_count`` is the smallest cohort a column may have; ``suppress_topk_below`` is the
    smallest cell (a value count, an enum or histogram bin) that may be released."""

    min_count: int = 5
    suppress_topk_below: int = 5


DEFAULT_POLICY = SuppressionPolicy()


def suppress_shape(
    shape: Mapping[str, Any], policy: SuppressionPolicy = DEFAULT_POLICY
) -> dict[str, Any]:
    """Withhold every column below ``min_count`` rows and every cell below the minimum cell."""
    rows = shape.get("rows", 0)
    out: dict[str, Any] = {"rows": rows, "columns": {}}
    cells = 0
    for name, c in shape.get("columns", {}).items():
        count = c.get("count", rows)
        if count < policy.min_count:
            out["columns"][name] = {
                "kind": c.get("kind"),
                "suppressed": True,
                "reason": "cohort_below_min_count",
            }
            continue
        x, n, _ = suppress_column_cells(c, policy.suppress_topk_below, int(count))
        cells += n
        out["columns"][name] = x
    out["privacy"] = {
        "suppression": {
            "min_count": policy.min_count,
            "topk_min_count": policy.suppress_topk_below,
            "cells_suppressed": cells,
        }
    }
    return out


def differencing_risk(
    before: Mapping[str, Any], after: Mapping[str, Any], min_delta: int = 5
) -> dict[str, Any]:
    d = int(after.get("rows", 0)) - int(before.get("rows", 0))
    return {"risky": 0 < abs(d) < min_delta, "row_delta": d, "threshold": min_delta}


def redact_sensitive(
    shape: Mapping[str, Any],
    classifications: Mapping[str, str],
    redact_at: tuple[str, ...] = ("PII", "SENSITIVE", "SECRET", "TOP_SECRET"),
) -> dict[str, Any]:
    """Remove value-bearing evidence for classified columns before artifact release.

    A column is redacted when its label ranks at or above the lowest label in ``redact_at``:
    every key that can carry an original value or a tight bound (``policy.VALUE_KEYS``, the set
    ``release_for`` strips) is removed.
    """
    out: dict[str, Any] = {"rows": shape.get("rows", 0), "columns": {}}
    floor = min(DEFAULT_TAXONOMY.rank(x) for x in redact_at)
    for name, c in shape.get("columns", {}).items():
        x = dict(c)
        label = str(classifications.get(name, "PUBLIC")).upper()
        if DEFAULT_TAXONOMY.rank(label) >= floor:
            for k in VALUE_KEYS:
                x.pop(k, None)
            x["value_evidence_redacted"] = True
            x["classification"] = label
        out["columns"][name] = x
    out["privacy"] = {"sensitive_value_redaction": True}
    return out
