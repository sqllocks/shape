"""AUD-security2 #293: the tier-2 format patterns run in linear time on hostile values, and the
e-mail pattern accepts exactly what it accepted before."""

from __future__ import annotations

import itertools
import re
import time

from shape.fidelity.tier2 import FORMAT_PATTERNS

QUADRATIC_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def test_the_email_pattern_is_fast_on_a_long_dotted_value():
    value = "a@" + "." * 50_000 + "@"
    start = time.perf_counter()
    for pattern in FORMAT_PATTERNS.values():
        pattern.match(value)
    assert time.perf_counter() - start < 0.5


def test_the_email_pattern_accepts_what_it_accepted_before():
    alphabet = "a.@ \n"
    for n in range(1, 8):
        for chars in itertools.product(alphabet, repeat=n):
            value = "".join(chars)
            assert bool(FORMAT_PATTERNS["email"].match(value)) == bool(
                QUADRATIC_EMAIL.match(value)
            ), value
    for value in ("alice@example.com", "a@b.c\n", "x@.b", "x@..b", "x@b.", "a b@c.d"):
        assert bool(FORMAT_PATTERNS["email"].match(value)) == bool(QUADRATIC_EMAIL.match(value))
