"""Constrained AI assistance: produces proposals; deterministic validators remain authoritative."""


def explain(shape):
    return {
        "rows": shape.get("rows", 0),
        "columns": len(shape.get("columns", {})),
        "message": "Shape evidence summary; validate decisions against deterministic contracts.",
    }


def contract_proposal(shape):
    return {
        "columns": {
            k: {"required": v.get("null_count", 0) == 0, "kind": v.get("kind")}
            for k, v in shape.get("columns", {}).items()
        },
        "status": "proposal",
    }
