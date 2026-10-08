"""Small dependency-free first-party Person Pack."""

from __future__ import annotations

import random

FIRST = ("Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Avery", "Cameron")
LAST = ("Smith", "Johnson", "Brown", "Davis", "Wilson", "Miller", "Moore", "Taylor")


def _rng(row, seed):
    if seed >= 0 and 0 <= row < 1 << 64:
        return random.Random((seed << 64) ^ row)
    # random.Random seeds with |n| for an int, so -s and s would share a stream; text seeds
    # (SHA-512 of the bytes) keep every other (row, seed) apart, the same in every process.
    return random.Random(f"shape.packs.person:{seed}:{row}")


def generate_person(row, seed=0):
    rng = _rng(row, seed)
    first = rng.choice(FIRST)
    last = rng.choice(LAST)
    return {
        "first_name": first,
        "last_name": last,
        "full_name": f"{first} {last}",
        "email": f"{first}.{last}.{row}@example.invalid".lower(),
    }
