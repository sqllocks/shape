"""``shape.detectors``: the profiler recognizes ICD-10, NDC, NPI, HCPCS, CPT and member ids.

Each detector looks at the first ``_SAMPLE`` non-null values (as strings) and reports the share
that match its pattern, as the confidence: a column of values that nearly all match is a
confident hit; a column where fewer than ``_MIN_RATE`` match is ``None``. Patterns that other
identifiers share (a 5-digit CPT code looks like a ZIP code, a bare 11-digit NDC like many ids)
only count when the column name says so (the name hints below).

* ICD-10 (WHO, CM, GM, AM): a letter, two digits, then up to four more characters, with or
  without the dot; a column of only 3-character codes is too ambiguous and is not reported.
* NDC: dashed 4-4-2, 5-3-2, 5-4-1 and 5-4-2, or 11 bare digits when the name says ``ndc``.
* NPI: ten digits whose last digit is the Luhn check digit with the 80840 prefix (CMS), first
  digit 1 or 2, or the synthetic range (:mod:`shape_healthcare_codes.npi`). A random column of
  ten-digit numbers passes the check one time in ten, so a column must pass in almost every row.
* HCPCS Level II: a letter A-V and four digits. CPT: 5 digits (needs a name hint), or four
  digits and F (category II) or T (category III).
* Member id: a Medicare Beneficiary Identifier (:class:`MbiDetector`), or a 2-4 letter prefix
  with 8-12 digits and an optional 2-digit dependent suffix (needs a name hint to reach 0.9).
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pyarrow as pa  # type: ignore[import-untyped]

from shape.plugins.api.v1 import Detection
from shape_healthcare_codes.npi import check_digit

SHAPE_API = "1.0"
_SAMPLE = 1000
_MIN_RATE = 0.9

_ICD10 = re.compile(r"^[A-Z][0-9][0-9A-Z](\.?[0-9A-Z]{1,4})?$")
_NDC = re.compile(
    r"^([0-9]{4}-[0-9]{4}-[0-9]{2}|[0-9]{5}-[0-9]{3}-[0-9]{2}|[0-9]{5}-[0-9]{4}-[0-9]{1,2})$"
)
_NDC11 = re.compile(r"^[0-9]{11}$")
_HCPCS = re.compile(r"^[A-V][0-9]{4}$")
_CPT5 = re.compile(r"^[0-9]{5}$")
_CPT23 = re.compile(r"^[0-9]{4}[FT]$")
_MEMBER = re.compile(r"^[A-Z]{2,4}[0-9]{8,12}(-?[0-9]{2})?$")
_ML = "AC-HJKMNP-RT-Y"  # MBI letters: A-Z without B, I, L, O, S, Z
_MBI = re.compile(rf"^[1-9][{_ML}][{_ML}0-9][0-9]-?[{_ML}][{_ML}0-9][0-9]-?[{_ML}]{{2}}[0-9]{{2}}$")
_ICD_NAME = re.compile(r"icd|diag|dx", re.I)
_NDC_NAME = re.compile(r"ndc|drug_code|product_code", re.I)
_CPT_NAME = re.compile(r"cpt|hcpcs|proc|procedure", re.I)
_MEMBER_NAME = re.compile(r"member|subscriber|mbr|insured|beneficiar|policy", re.I)
_NPI_NAME = re.compile(r"npi|provider|prescriber|rendering|billing", re.I)


def _values(arr: pa.Array) -> list[str]:
    out: list[str] = []
    for v in arr.slice(0, _SAMPLE * 2).to_pylist():
        if v is None:
            continue
        s = str(v).strip()
        if s:
            out.append(s)
        if len(out) >= _SAMPLE:
            break
    return out


def _rate(vals: list[str], ok: Callable[[str], bool]) -> float:
    return sum(1 for v in vals if ok(v)) / len(vals) if vals else 0.0


class _Base:
    name = ""

    def detect(self, values: pa.Array, column: str) -> Detection | None:
        vals = _values(values)
        if not vals:
            return None
        return self._detect(vals, column)

    def _detect(self, vals: list[str], column: str) -> Detection | None:
        raise NotImplementedError


class Icd10Detector(_Base):
    name = "icd10"

    def _detect(self, vals: list[str], column: str) -> Detection | None:
        up = [v.upper() for v in vals]
        rate = _rate(up, lambda v: bool(_ICD10.match(v)))
        if rate < _MIN_RATE:
            return None
        longer = _rate(up, lambda v: len(v.replace(".", "")) >= 4)
        hinted = bool(_ICD_NAME.search(column))
        if longer < 0.5 and not hinted:
            return None
        conf = rate * (0.99 if hinted else 0.9 if longer >= 0.9 else 0.7)
        return Detection(self.name, round(conf, 4))


class NdcDetector(_Base):
    name = "ndc"

    def _detect(self, vals: list[str], column: str) -> Detection | None:
        rate = _rate(vals, lambda v: bool(_NDC.match(v)))
        if rate >= _MIN_RATE:
            return Detection(self.name, round(rate * 0.99, 4))
        if _NDC_NAME.search(column) and _rate(vals, lambda v: bool(_NDC11.match(v))) >= _MIN_RATE:
            return Detection(self.name, 0.9)
        return None


def _npi_ok(v: str) -> bool:
    if not re.fullmatch(r"[0-9]{10}", v):
        return False
    return check_digit(v[:9]) == int(v[9]) and (v[0] in "12" or v.startswith("99"))


class NpiDetector(_Base):
    name = "npi"

    def _detect(self, vals: list[str], column: str) -> Detection | None:
        rate = _rate(vals, _npi_ok)
        if rate < 0.97:
            return None
        hinted = bool(_NPI_NAME.search(column))
        # Distinct ten-digit values that all pass the check are overwhelmingly NPIs; a handful
        # of repeated values proves little.
        distinct = len(set(vals))
        conf = rate * (0.99 if hinted else 0.95 if distinct >= 20 else 0.6)
        return Detection(self.name, round(conf, 4))


class HcpcsDetector(_Base):
    name = "hcpcs"

    def _detect(self, vals: list[str], column: str) -> Detection | None:
        rate = _rate([v.upper() for v in vals], lambda v: bool(_HCPCS.match(v)))
        if rate < _MIN_RATE:
            return None
        return Detection(self.name, round(rate * (0.99 if _CPT_NAME.search(column) else 0.85), 4))


class CptDetector(_Base):
    name = "cpt"

    def _detect(self, vals: list[str], column: str) -> Detection | None:
        hinted = bool(_CPT_NAME.search(column))
        up = [v.upper() for v in vals]
        r23 = _rate(up, lambda v: bool(_CPT23.match(v)))
        if r23 >= _MIN_RATE:
            return Detection(self.name, round(r23 * 0.95, 4))
        if hinted:
            r5 = _rate(up, lambda v: bool(_CPT5.match(v)) or bool(_CPT23.match(v)))
            if r5 >= _MIN_RATE:
                return Detection(self.name, round(r5 * 0.95, 4))
        return None


class MbiDetector(_Base):
    name = "mbi"

    def _detect(self, vals: list[str], column: str) -> Detection | None:
        rate = _rate([v.upper() for v in vals], lambda v: bool(_MBI.match(v)))
        return Detection(self.name, round(rate * 0.97, 4)) if rate >= _MIN_RATE else None


class MemberIdDetector(_Base):
    name = "member_id"

    def _detect(self, vals: list[str], column: str) -> Detection | None:
        rate = _rate([v.upper() for v in vals], lambda v: bool(_MEMBER.match(v)))
        if rate < _MIN_RATE:
            return None
        hinted = bool(_MEMBER_NAME.search(column))
        return Detection(self.name, round(rate * (0.95 if hinted else 0.6), 4))
