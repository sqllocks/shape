"""Test data shared by the vault tests."""

from __future__ import annotations

PLANTED = {
    "rare": "ZQXRARE-CATEGORY-91",
    "email": "planted.person@example.invalid",
    "extreme": "987654321.123",
}

COLUMNS = {
    "orders.status": ("categories", {"categories": [["paid", 70], ["new", 25], ["void", 5]]}),
    "orders.amount": ("extremes", {"min": {"float": "1.5"}, "max": {"float": "987654321.123"}}),
    "orders.email": (
        "all",
        {
            "categories": [[PLANTED["email"], 1], [PLANTED["rare"], 1]],
            "min": PLANTED["email"],
            "max": PLANTED["rare"],
        },
    ),
}
