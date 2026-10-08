"""Small dependency-free first-party Person Pack."""

from __future__ import annotations

import random

FIRST = ("Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Avery", "Cameron")
LAST = ("Smith", "Johnson", "Brown", "Davis", "Wilson", "Miller", "Moore", "Taylor")


def generate_person(row, seed=0):
    rng = random.Random((seed << 64) ^ row)
    first = rng.choice(FIRST)
    last = rng.choice(LAST)
    return {
        "first_name": first,
        "last_name": last,
        "full_name": f"{first} {last}",
        "email": f"{first}.{last}.{row}@example.invalid".lower(),
    }
