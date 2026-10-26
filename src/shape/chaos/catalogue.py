"""Read-only metadata for the targeted ``--corrupt`` surface."""

from __future__ import annotations

from typing import Any

from shape.chaos.groundtruth import CORRUPTIONS

_COUNT = (
    "Select round(current table rows * rate) rows per target column, capped at eligible rows; "
    "Python round uses ties to even."
)
_TARGETS = {
    "duplicates": "Whole rows; copies are appended to the table.",
    "orphan_keys": "Declared foreign keys, else non-primary *_id columns; integer, float or text.",
    "date_shift": "Every date or timestamp column; only non-null rows are eligible.",
    "negative_amounts": "Signed number columns excluding keys; only positive rows are eligible.",
    "case_whitespace": "Non-key text with at most 50 distinct values; only changing variants.",
    "pii_fill": "An explicit text column, including null cells.",
    "type_change": "An explicit non-text column, converted in full to text.",
    "null_creep": "An explicit column of any type; only non-null rows are eligible.",
}
_OPTIONS: dict[str, dict[str, str]] = {
    "duplicates": {"fuzz": "Number 0 to 1, default 0: damage probability per copied non-key cell."},
    "date_shift": {
        "days": "Integer at least 1, default 7: maximum absolute shift in days.",
        "direction": "both (default), late or early.",
    },
    "pii_fill": {"pii": "ssn (default), email or phone."},
    "null_creep": {"step": "Number at least 0, default 0: rate added per batch after from."},
}


def catalogue() -> dict[str, Any]:
    """Describe all targeted kinds without reading data, generating rows or writing files."""
    kinds = []
    for kind in CORRUPTIONS:
        rate = _COUNT
        if kind == "duplicates":
            rate = (
                "Append round(current table rows * rate) copied rows; "
                "Python round uses ties to even."
            )
        elif kind == "type_change":
            rate = "Rate is ignored: the entire column is converted, even when rate is 0."
        elif kind == "null_creep":
            rate = "Effective rate = min(1, rate + step * (batch - from)); " + _COUNT
        kinds.append(
            {
                "kind": kind,
                "column": (
                    "forbidden"
                    if kind == "duplicates"
                    else "required"
                    if kind in ("pii_fill", "type_change", "null_creep")
                    else "optional"
                ),
                "targeting": _TARGETS[kind],
                "default_rate": 0.02,
                "rate_min": 0,
                "rate_max": 1,
                "rate_semantics": rate,
                "options": {
                    "from": "Integer at least 0, default 0: first active batch (inclusive).",
                    "to": "Integer at least from, default no end: last active batch (inclusive).",
                    **_OPTIONS.get(kind, {}),
                },
            }
        )
    return {
        "surface": "targeted_corruptions",
        "syntax": "KIND[=RATE][@TABLE[.COLUMN]][:OPT=V,...]",
        "kinds": kinds,
    }
