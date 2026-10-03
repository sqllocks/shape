"""IBAN validation (W3-12).

An IBAN is valid when, after plain spaces are removed and letters are upper-cased, it is ASCII
letters and digits, starts with a known country code, has that country's length (the table is
the shipped ``iban-lengths`` pack, from the SWIFT IBAN Registry) and its ISO 7064 MOD 97-10
check equals 1. Nothing else is checked: a letter replaced by a digit (or the reverse) in the
account part can still pass the checksum, so the check does not claim to catch it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

__all__ = ["IBAN_LENGTHS", "iban_problem", "is_valid_iban"]

_PACK = Path(__file__).resolve().parent.parent / "reference" / "data" / "iban-lengths"
_lengths: dict[str, int] | None = None


def _table() -> dict[str, int]:
    global _lengths
    if _lengths is None:
        from shape.reference.packs import Pack, read_manifest

        table = Pack(_PACK, read_manifest(_PACK), "shipped").table("iban_lengths")
        _lengths = dict(
            zip(
                table.column("country").to_pylist(), table.column("length").to_pylist(), strict=True
            )
        )
    return _lengths


class _Lengths(Mapping[str, int]):
    """Country code to IBAN length, read from the shipped pack on first use."""

    def __getitem__(self, key: str) -> int:
        return _table()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(_table())

    def __len__(self) -> int:
        return len(_table())


IBAN_LENGTHS: Mapping[str, int] = _Lengths()


def _mod97(compact: str) -> int:
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97


def iban_problem(value: Any) -> str | None:
    """Why ``value`` is not an IBAN (``None`` when it is one)."""
    if not isinstance(value, str):
        return f"not text ({type(value).__name__})"
    compact = value.replace(" ", "")
    if not compact:
        return "empty"
    if not (compact.isascii() and compact.isalnum()):
        return "characters other than letters and digits"
    compact = compact.upper()
    country = compact[:2]
    if not country.isalpha() or country not in IBAN_LENGTHS:
        return f"unknown country code {country!r}"
    if len(compact) != IBAN_LENGTHS[country]:
        return f"length {len(compact)}, but {country} IBANs have {IBAN_LENGTHS[country]}"
    if not compact[2:4].isdigit():
        return "check digits are not digits"
    if _mod97(compact) != 1:
        return "checksum (ISO 7064 MOD 97-10) is not 1"
    return None


def is_valid_iban(value: Any) -> bool:
    """True when ``value`` is a valid IBAN (:func:`iban_problem` says why not)."""
    return iban_problem(value) is None
