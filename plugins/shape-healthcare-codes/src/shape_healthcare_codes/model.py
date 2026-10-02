"""The common code model: one shape for every code system, so a domain can switch systems.

Every code system, US or international, loads as an Arrow table with these base columns
(:data:`BASE_COLUMNS`); a system may add its own (ICD-10-CM adds the chapter, block, category
and per-fiscal-year validity masks). A code is stored *without* punctuation and in upper case
(``E11.9`` is ``E119``); :func:`normalize_code` converts either way.

The diagnosis systems (ICD-10-CM, ICD-10-GM, ICD-10-AM, WHO ICD-10) and the procedure systems
(ICD-10-PCS, HCPCS Level II) are interchangeable behind :class:`CodeSet`: a domain asks a
``CodeSet`` for the codes valid on a date, never a specific file format.
"""

from __future__ import annotations

import datetime as dt
import enum
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

FY_BASE = 2016
"""Fiscal year of bit 0 in the per-fiscal-year masks: FY2016 began 2015-10-01, the first
fiscal year of ICD-10-CM in US use."""

BASE_COLUMNS = ("code", "short_desc", "long_desc", "leaf", "valid_from", "valid_to")
"""Columns every code-set table has. ``leaf`` is true for a code that can be reported
(billable for ICD-10-CM and ICD-10-PCS); ``valid_from`` and ``valid_to`` bound the dates the
code was in the release range this build covers (``valid_to`` null: still current)."""


class CodeSystem(enum.StrEnum):
    """The code systems of this plugin. The value is the asset id and the file stem."""

    ICD10CM = "icd10cm"
    ICD10PCS = "icd10pcs"
    HCPCS2 = "hcpcs2"
    NDC = "ndc"
    RXNORM = "rxnorm"
    POS = "pos"
    HCC = "hcc"
    CCSR = "ccsr"
    TAXONOMY = "nucc_taxonomy"
    CARC = "carc"
    RARC = "rarc"
    ICD10_WHO = "icd10_who"
    ICD10GM = "icd10gm"
    ICD10AM = "icd10am"
    CPT = "cpt"
    SNOMED = "snomed"
    REVENUE = "revenue_codes"
    TYPE_OF_BILL = "type_of_bill"


DIAGNOSIS_SYSTEMS = (
    CodeSystem.ICD10CM,
    CodeSystem.ICD10GM,
    CodeSystem.ICD10AM,
    CodeSystem.ICD10_WHO,
)
PROCEDURE_SYSTEMS = (CodeSystem.ICD10PCS, CodeSystem.HCPCS2, CodeSystem.CPT)

_PUNCT = re.compile(r"[\s.\-]")


def normalize_code(code: str) -> str:
    """Upper-case and strip dots, dashes and blanks (``e11.9`` -> ``E119``)."""
    return _PUNCT.sub("", code).upper()


def format_icd10(code: str) -> str:
    """The dotted display form: a dot after the third character when the code is longer."""
    c = normalize_code(code)
    return c if len(c) <= 3 else f"{c[:3]}.{c[3:]}"


def fiscal_year(day: dt.date) -> int:
    """The US federal fiscal year of ``day`` (October 1 starts the next one)."""
    return day.year + 1 if day.month >= 10 else day.year


def fy_start(fy: int) -> dt.date:
    """First day of fiscal year ``fy`` (``fy_start(2027)`` is 2026-10-01)."""
    return dt.date(fy - 1, 10, 1)


def fy_end(fy: int) -> dt.date:
    """Last day of fiscal year ``fy``."""
    return dt.date(fy, 9, 30)


def fy_bit(fy: int) -> int:
    """The mask bit of fiscal year ``fy``."""
    if fy < FY_BASE:
        raise ValueError(f"fiscal year {fy} is before FY{FY_BASE}")
    return 1 << (fy - FY_BASE)


@dataclass(frozen=True, slots=True)
class CodeRecord:
    """One code, as :meth:`CodeSet.get` returns it."""

    system: str
    code: str
    short_desc: str
    long_desc: str
    leaf: bool
    valid_from: dt.date | None
    valid_to: dt.date | None
    attrs: dict[str, Any]


class CodeSet:
    """A loaded code system with date-aware lookups.

    ``table`` carries :data:`BASE_COLUMNS` and may carry ``fy_valid_mask`` and ``fy_leaf_mask``
    (int32, bit ``fy - FY_BASE``): when they are present validity is exact per fiscal year,
    otherwise it is the ``valid_from`` / ``valid_to`` range.
    """

    def __init__(self, system: str, table: pa.Table, release: str = "") -> None:
        missing = [c for c in BASE_COLUMNS if c not in table.column_names]
        if missing:
            raise ValueError(f"{system}: table lacks base columns {missing}")
        self.system = system
        self.table = table
        self.release = release
        self._index: dict[str, int] | None = None

    def __len__(self) -> int:
        return int(self.table.num_rows)

    @property
    def index(self) -> dict[str, int]:
        if self._index is None:
            self._index = {c: i for i, c in enumerate(self.table.column("code").to_pylist())}
        return self._index

    def __contains__(self, code: object) -> bool:
        return isinstance(code, str) and normalize_code(code) in self.index

    def get(self, code: str) -> CodeRecord | None:
        i = self.index.get(normalize_code(code))
        if i is None:
            return None
        row = self.table.slice(i, 1).to_pylist()[0]
        attrs = {k: v for k, v in row.items() if k not in BASE_COLUMNS}
        return CodeRecord(
            self.system,
            row["code"],
            row["short_desc"] or "",
            row["long_desc"] or "",
            bool(row["leaf"]),
            row["valid_from"],
            row["valid_to"],
            attrs,
        )

    def is_valid(self, code: str, on: dt.date, *, leaf_only: bool = False) -> bool:
        """True when ``code`` exists on ``on`` (and, with ``leaf_only``, can be reported)."""
        i = self.index.get(normalize_code(code))
        if i is None:
            return False
        names = self.table.column_names
        if "fy_valid_mask" in names:
            fy = fiscal_year(on)
            if fy < FY_BASE:
                return False
            col = "fy_leaf_mask" if leaf_only and "fy_leaf_mask" in names else "fy_valid_mask"
            mask = self.table.column(col)[i].as_py()
            ok = bool(mask & fy_bit(fy))
            if leaf_only and col == "fy_valid_mask":
                ok = ok and bool(self.table.column("leaf")[i].as_py())
            return ok
        lo = self.table.column("valid_from")[i].as_py()
        hi = self.table.column("valid_to")[i].as_py()
        if (lo is not None and on < lo) or (hi is not None and on > hi):
            return False
        return bool(self.table.column("leaf")[i].as_py()) if leaf_only else True

    def codes_on(self, on: dt.date, *, leaf_only: bool = True) -> pa.Array:
        """Every code valid on ``on`` (reportable ones only by default), in table order."""
        names = self.table.column_names
        if "fy_valid_mask" in names:
            fy = fiscal_year(on)
            if fy < FY_BASE:
                return self.table.column("code").slice(0, 0).combine_chunks()
            col = "fy_leaf_mask" if leaf_only and "fy_leaf_mask" in names else "fy_valid_mask"
            keep = pc.not_equal(pc.bit_wise_and(self.table.column(col), fy_bit(fy)), 0)
        else:
            lo, hi = self.table.column("valid_from"), self.table.column("valid_to")
            day = pa.scalar(on, pa.date32())
            keep = pc.and_(
                pc.or_(pc.is_null(lo), pc.less_equal(lo, day)),
                pc.or_(pc.is_null(hi), pc.greater_equal(hi, day)),
            )
            if leaf_only:
                keep = pc.and_(keep, self.table.column("leaf"))
        if "fy_valid_mask" in names and leaf_only and "fy_leaf_mask" not in names:
            keep = pc.and_(keep, self.table.column("leaf"))
        out = self.table.column("code").filter(keep)
        return out.combine_chunks() if isinstance(out, pa.ChunkedArray) else out

    def column(self, name: str) -> pa.ChunkedArray:
        return self.table.column(name)

    def select(self, codes: Sequence[str]) -> pa.Table:
        """The rows of ``codes`` (unknown codes are skipped), in the order given."""
        idx = [self.index[c] for c in map(normalize_code, codes) if c in self.index]
        return self.table.take(pa.array(idx, pa.int64()))


@dataclass(frozen=True, slots=True)
class LicenceNote:
    """What a user must know before using an asset (the short form of its notice entry)."""

    asset: str
    licence: str
    mode: str
    url: str
