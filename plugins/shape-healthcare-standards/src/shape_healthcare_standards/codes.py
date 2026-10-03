"""Shape checks for the code values the writers copy through.

The code sets themselves (ICD-10, NDC, NPI, CARC/RARC, place of service, taxonomy) come from
the codes lane. The writers only need to know a value *can* be written into its standard
field, so this module checks shape, not membership: lengths and character classes.
"""

from __future__ import annotations

import re

_ICD10 = re.compile(r"^[A-TV-Z][0-9][0-9A-Z]{1,5}$")
_ICD10PCS = re.compile(r"^[0-9A-HJ-NP-Z]{7}$")
_NDC11 = re.compile(r"^[0-9]{11}$")
_NPI = re.compile(r"^[0-9]{10}$")
_STATUS_CATEGORY = re.compile(r"^[A-Z][A-Z0-9]$")
_STATUS_CODE = re.compile(r"^[0-9]{1,5}$")
_ENTITY_ID = re.compile(r"^[A-Z0-9]{2,3}$")


def is_icd10cm(code: str) -> bool:
    """3 to 7 characters, no decimal point: a letter, a digit, then letters or digits."""
    return bool(_ICD10.match(code))


def is_icd10pcs(code: str) -> bool:
    """Seven characters; ICD-10-PCS never uses the letters I or O."""
    return bool(_ICD10PCS.match(code))


def is_ndc11(code: str) -> bool:
    return bool(_NDC11.match(code))


def npi_check_digit_ok(npi: str) -> bool:
    """The NPI check: Luhn over the 10 digits with the constant prefix 80840."""
    if not _NPI.match(npi):
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + npi)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def luhn_npi(first_nine: str) -> str:
    """Complete nine digits to a 10-digit NPI with a valid check digit (test fixtures)."""
    for check in "0123456789":
        if npi_check_digit_ok(first_nine + check):
            return first_nine + check
    raise ValueError("unreachable")


def is_claim_status_category(code: str) -> bool:
    """Two characters: an uppercase letter, then an uppercase letter or a digit (``A2``)."""
    return bool(_STATUS_CATEGORY.match(code))


def is_claim_status_code(code: str) -> bool:
    """One to five digits."""
    return bool(_STATUS_CODE.match(code))


def is_entity_identifier(code: str) -> bool:
    """Two or three uppercase letters or digits (``PR``, ``QC``, ``1P``, ``MSC``)."""
    return bool(_ENTITY_ID.match(code))
