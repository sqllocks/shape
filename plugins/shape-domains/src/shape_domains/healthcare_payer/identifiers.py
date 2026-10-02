"""Synthetic identifiers that can never belong to a real person.

* NPI: 10 digits, Luhn check digit over the ``80840`` prefix, leading ``99`` (CMS issued NPIs with a
  first digit of 1 or 2, so a ``99`` NPI is never assigned; the same rule as the codes lane's
  ``generate_synthetic_npis``).  The ``NpiSource`` seam lets another generator replace this one.
* Member and subscriber ids carry the letters ``SYN``: no payer issues them.
* SSN: area ``900``-``999`` (never issued), group and serial non-zero.
* E-mail: ``example.com`` / ``example.org`` / ``example.net`` (reserved by RFC 2606).
* Phone: ``555-01xx`` (reserved for fiction).
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

NPI_PREFIX = "80840"
SYNTHETIC_NPI_LEAD = "99"
EMAIL_DOMAINS = ("example.com", "example.org", "example.net")


def npi_check_digit(first_nine: str) -> int:
    """Luhn check digit of the 9-digit NPI body, computed over the ``80840`` prefix."""
    digits = [int(c) for c in NPI_PREFIX + first_nine]
    total = 0
    for pos, d in enumerate(reversed(digits)):
        if pos % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10


def npi_is_valid(npi: str) -> bool:
    """Format and check digit (not a registry lookup)."""
    return len(npi) == 10 and npi.isdigit() and npi_check_digit(npi[:9]) == int(npi[9])


def npi_is_never_assigned(npi: str) -> bool:
    return npi_is_valid(npi) and npi.startswith(SYNTHETIC_NPI_LEAD)


class NpiSource(Protocol):
    def npi(self, n: int) -> str: ...


class SyntheticNpis:
    """Never-assigned NPIs: ``9`` + 8 digits drawn without repeats + check digit."""

    def npi(self, n: int) -> str:
        body = SYNTHETIC_NPI_LEAD + f"{(n * 7919 + 1_000_003) % 10_000_000:07d}"
        return body + str(npi_check_digit(body))


def member_id(n: int) -> str:
    return f"SYN{n:09d}"


def subscriber_id(n: int) -> str:
    return f"SYS{n:09d}"


def pharmacy_ncpdp_id(n: int) -> str:
    """7-digit NCPDP provider id; the leading ``9`` is never issued."""
    return f"9{n:06d}"


def ssn(rng: np.random.Generator) -> str:
    area = int(rng.integers(900, 1000))
    return f"{area:03d}-{int(rng.integers(1, 100)):02d}-{int(rng.integers(1, 10000)):04d}"


def is_never_issued_ssn(value: str) -> bool:
    return len(value) == 11 and value[:3].isdigit() and 900 <= int(value[:3]) <= 999


def email(first: str, last: str, n: int) -> str:
    return f"{first}.{last}{n}@{EMAIL_DOMAINS[n % 3]}".lower()


def phone(rng: np.random.Generator) -> str:
    return f"{int(rng.integers(201, 990))}-555-{int(rng.integers(100, 200)):04d}"
