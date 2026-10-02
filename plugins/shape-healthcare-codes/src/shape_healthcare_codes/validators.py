"""Validators a domain (or a user) calls on generated or real values.

Each takes the loaded reference it needs, so nothing here reads files by itself:

* :func:`icd10cm_valid_billable`: an ICD-10-CM code exists and can be reported on a date of
  service (the code set changes every October 1; validity is kept per fiscal year);
* :func:`ndc_marketed`: an NDC exists in the FDA directory and was marketed on a fill date;
* NPI helpers are in :mod:`shape_healthcare_codes.npi` and re-exported here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pyarrow as pa  # type: ignore[import-untyped]

from shape_healthcare_codes.model import CodeSet, normalize_code
from shape_healthcare_codes.ndc import AmbiguousNdc, normalize_ndc
from shape_healthcare_codes.npi import (
    generate_synthetic_npi,
    generate_synthetic_npis,
    is_real_format_npi,
    is_synthetic_npi,
    luhn_valid,
)

__all__ = [
    "EditIndex",
    "NdcIndex",
    "age_sex_violations",
    "generate_synthetic_npi",
    "generate_synthetic_npis",
    "icd10cm_valid_billable",
    "is_real_format_npi",
    "is_synthetic_npi",
    "luhn_valid",
    "ndc_marketed",
]


def icd10cm_valid_billable(codes: CodeSet, code: str, date_of_service: dt.date) -> bool:
    """True when ``code`` is an ICD-10-CM code that is valid *and billable* (a leaf) on
    ``date_of_service``. Dotted or undotted, any case. A header (non-leaf) code, an unknown
    code, and a code outside its fiscal years all return False."""
    return codes.is_valid(normalize_code(code), date_of_service, leaf_only=True)


@dataclass(frozen=True, slots=True)
class NdcIndex:
    """The FDA NDC directory keyed by 11-digit NDC, for :func:`ndc_marketed`.

    Built from the ``ndc`` asset (one row per package; ``code`` is the 11-digit NDC,
    ``valid_from`` and ``valid_to`` are the marketing start and end dates). A package with
    no marketing end date is still marketed. ``[VERIFY]`` the FDA directory lists current and
    recently delisted products only, so for a fill date long past an NDC may be absent although
    it was real.
    """

    start: dict[str, dt.date | None]
    end: dict[str, dt.date | None]

    @classmethod
    def from_table(cls, table: pa.Table) -> NdcIndex:
        codes = table.column("code").to_pylist()
        starts = table.column("valid_from").to_pylist()
        ends = table.column("valid_to").to_pylist()
        return cls(dict(zip(codes, starts, strict=True)), dict(zip(codes, ends, strict=True)))

    def __contains__(self, ndc: object) -> bool:
        if not isinstance(ndc, str):
            return False
        try:
            return normalize_ndc(ndc) in self.start
        except AmbiguousNdc:
            return False


def ndc_marketed(index: NdcIndex, ndc: str, fill_date: dt.date) -> bool:
    """True when ``ndc`` (dashed 10- or 11-digit, or 11 bare digits) is in the directory and
    ``marketing_start <= fill_date <= marketing_end`` (an empty bound is open)."""
    try:
        n = normalize_ndc(ndc)
    except AmbiguousNdc:
        return False
    if n is None or n not in index.start:
        return False
    lo, hi = index.start[n], index.end[n]
    return (lo is None or fill_date >= lo) and (hi is None or fill_date <= hi)


@dataclass(frozen=True, slots=True)
class EditIndex:
    """Age and sex edits from CMS's Medicare Code Editor lists (the ``mce_edits`` asset).

    ``age`` maps ``(system, code)`` to ``(category, min_age, max_age)`` constraints (a code can
    be in more than one list); ``sex`` maps ``(system, code)`` to the one sex it is limited to
    (``"F"`` or ``"M"``). A code on no list has no edit. Sex edits come from the last CMS
    version that published them (v41.1); see :mod:`shape_healthcare_codes.builders.mce`.
    """

    age: dict[tuple[str, str], list[tuple[str, int, int]]]
    sex: dict[tuple[str, str], str]

    @classmethod
    def from_table(cls, table: pa.Table) -> EditIndex:
        age: dict[tuple[str, str], list[tuple[str, int, int]]] = {}
        sex: dict[tuple[str, str], str] = {}
        for r in table.to_pylist():
            key = (r["system"], r["code"])
            if r["edit"] == "age":
                age.setdefault(key, []).append((r["value"], r["age_min"], r["age_max"]))
            else:
                sex[key] = r["value"]
        return cls(age, sex)


def age_sex_violations(
    edits: EditIndex, code: str, age_years: float, sex: str | None, system: str = "icd10cm"
) -> list[str]:
    """The edits ``code`` breaks for a patient of ``age_years`` and ``sex`` (``"F"``, ``"M"`` or
    their words; None or anything else is not checked): ``"age:adult"``, ``"sex:F"``, ...
    Empty means the combination is clinically plausible by CMS's lists."""
    key = (system, normalize_code(code))
    out = [
        f"age:{cat}" for cat, lo, hi in edits.age.get(key, ()) if not (lo <= int(age_years) <= hi)
    ]
    want = edits.sex.get(key)
    if want is not None and sex is not None and sex.strip()[:1].upper() in ("F", "M"):
        if sex.strip()[:1].upper() != want:
            out.append(f"sex:{want}")
    return out
