"""National Provider Identifier: check digit, format, and a synthetic never-assigned range.

Source of the rules (read 2026-10-02): "Requirements for National Provider Identifier (NPI) and
NPI Check Digit", CMS, January 23, 2004,
https://www.cms.gov/regulations-and-guidance/administrative-simplification/nationalprovidentstand/downloads/npicheckdigit.pdf

* an NPI is 9 digits plus one check digit;
* the check digit is the Luhn (mod 10 double-add-double) digit, always calculated as if the
  prefix ``80840`` (80 health applications, 840 United States) were present, which the CMS
  document does by adding the constant 24 for an unprefixed NPI;
* "NPIs will initially be issued with the first digit = 1 or 2. These digits will not be used as
  the first digits for other card issuer identifiers. Use of other first digits for the NPI
  must be coordinated with the use of first digits by the standard health plan identifier."

So every NPI that exists begins with 1 or 2, and CMS reserves no other first digit for NPIs.
The synthetic range here is the NPIs that begin with ``99``: they pass the Luhn check, so
anything that only checks the check digit accepts them, and they cannot be a real NPI while
real NPIs begin with 1 or 2. ``[VERIFY]``: CMS says other first digits may be used later "when
coordinated"; if NPPES ever issues a first digit of 9, the range must move (the constant
:data:`SYNTHETIC_PREFIX` is the only place to change). A validator that insists on a first
digit of 1 or 2 rejects synthetic NPIs on purpose: that is the safety property.
"""

from __future__ import annotations

import random
import re

PREFIX = "80840"
REAL_FIRST_DIGITS = "12"
SYNTHETIC_PREFIX = "99"

_NPI = re.compile(r"^[0-9]{10}$")


def _luhn_sum(digits: str) -> int:
    """Sum of digits with every second digit from the right (starting at the rightmost of
    ``digits``) doubled and its digits added."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return total


def check_digit(first9: str) -> int:
    """The NPI check digit for the 9 leading digits, with the 80840 prefix applied."""
    if not re.fullmatch(r"[0-9]{9}", first9):
        raise ValueError("an NPI body is exactly 9 digits")
    # The check digit sits to the right of the body, so the body's rightmost digit is doubled.
    total = _luhn_sum(PREFIX + first9)
    return (10 - total % 10) % 10


def luhn_valid(npi: str) -> bool:
    """True for ten digits whose last digit is the check digit of the first nine."""
    return bool(_NPI.match(npi)) and check_digit(npi[:9]) == int(npi[9])


def is_real_format_npi(npi: str) -> bool:
    """A valid check digit and a first digit of 1 or 2: the shape of an assignable real NPI.
    This says nothing about whether the NPI has been assigned (only NPPES knows that)."""
    return luhn_valid(npi) and npi[0] in REAL_FIRST_DIGITS


def is_synthetic_npi(npi: str) -> bool:
    """True for an NPI in the synthetic never-assigned range."""
    return luhn_valid(npi) and npi.startswith(SYNTHETIC_PREFIX)


def generate_synthetic_npi(rng: random.Random) -> str:
    """One synthetic NPI: ``99`` + seven random digits + the check digit."""
    body = SYNTHETIC_PREFIX + "".join(str(rng.randrange(10)) for _ in range(7))
    return body + str(check_digit(body))


def generate_synthetic_npis(n: int, seed: int = 0) -> list[str]:
    """``n`` distinct synthetic NPIs, deterministic for ``seed`` (``n`` up to 10**7)."""
    if not 0 <= n <= 10**7:
        raise ValueError("the synthetic range holds 10**7 NPIs")
    rng = random.Random(seed)
    out: set[str] = set()
    ordered: list[str] = []
    while len(ordered) < n:
        v = generate_synthetic_npi(rng)
        if v not in out:
            out.add(v)
            ordered.append(v)
    return ordered
