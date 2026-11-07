"""Exact W9-03 decimal additions to historical domain schema expectations."""

DECLARATIONS = {
    "retail": {
        ("address", "lat"): (9, 6),
        ("address", "lng"): (9, 6),
    },
    "healthcare": {
        ("facility", "lat"): (9, 6),
        ("facility", "lng"): (9, 6),
        ("procedure", "standard_charge"): (18, 2),
        ("claim", "total_amount"): (18, 2),
        ("claim", "allowed_amount"): (18, 2),
        ("claim", "paid_amount"): (18, 2),
        ("claim_line", "charge_amount"): (18, 2),
        ("claim_line", "allowed_amount"): (18, 2),
        ("claim_line", "paid_amount"): (18, 2),
        ("claim_line", "patient_copay"): (18, 2),
    },
}


def declare_expected_decimals(document: dict, domain: str, *, prefix: str = "") -> None:
    """Overlay only the named dimensions; every other historical field stays pinned."""
    for (table, name), (precision, scale) in DECLARATIONS.get(domain, {}).items():
        column = document["tables"][prefix + table]["columns"][name]
        assert column["type"] == "decimal"
        assert column["precision"] is None and column["scale"] is None
        column.update(precision=precision, scale=scale)
