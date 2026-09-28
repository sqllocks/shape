from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SuppressionPolicy:
    min_count: int = 5
    suppress_topk_below: int = 5


DEFAULT_POLICY = SuppressionPolicy()


def suppress_shape(shape, policy=DEFAULT_POLICY):
    out = {"rows": shape.get("rows", 0), "columns": {}}
    for name, c in shape.get("columns", {}).items():
        x = dict(c)
        if x.get("count", shape.get("rows", 0)) < policy.min_count:
            out["columns"][name] = {
                "kind": x.get("kind"),
                "suppressed": True,
                "reason": "cohort_below_min_count",
            }
            continue
        if "topk" in x:
            x["topk"] = [i for i in x["topk"] if len(i) > 1 and i[1] >= policy.suppress_topk_below]
        out["columns"][name] = x
    out["privacy"] = {
        "suppression": {"min_count": policy.min_count, "topk_min_count": policy.suppress_topk_below}
    }
    return out


def differencing_risk(before, after, min_delta=5):
    d = int(after.get("rows", 0)) - int(before.get("rows", 0))
    return {"risky": 0 < abs(d) < min_delta, "row_delta": d, "threshold": min_delta}


def redact_sensitive(
    shape, classifications, redact_at=("PII", "SENSITIVE", "SECRET", "TOP_SECRET")
):
    """Remove value-bearing evidence for classified columns before artifact release."""
    out = {"rows": shape.get("rows", 0), "columns": {}}
    blocked = set(redact_at)
    for name, c in shape.get("columns", {}).items():
        x = dict(c)
        label = str(classifications.get(name, "PUBLIC")).upper()
        if label in blocked:
            x.pop("topk", None)
            x.pop("examples", None)
            x["value_evidence_redacted"] = True
            x["classification"] = label
        out["columns"][name] = x
    out["privacy"] = {"sensitive_value_redaction": True}
    return out
