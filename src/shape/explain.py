"""Human/machine explanation of captured Shape evidence."""


def explain_shape(shape):
    out = {"rows": shape.get("rows", 0), "fields": [], "warnings": []}
    for name, c in sorted(shape.get("columns", {}).items()):
        f = {
            "name": name,
            "kind": c.get("kind"),
            "nulls": c.get("null_count", 0),
            "distinct_estimate": c.get("distinct_estimate"),
        }
        if c.get("distinct_estimate", 0) >= max(shape.get("rows", 0) * 0.999, 1):
            f["candidate_identifier"] = True
        out["fields"].append(f)
        if c.get("null_count", 0) > shape.get("rows", 0) * 0.5:
            out["warnings"].append(f"{name}: majority null")
    return out
