"""Apply a layout to pharmacy claim rows (and their member, provider, drug companions).

Source columns are the contract columns only. A claim row is a transaction; the layout decides
whether it is written as a request (billing) or a response (status, reject code, plan paid)
by the transaction code you pick.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..common import TableSet
from .layout import Field, FieldFormat, HeaderField, Layout, Segment, Source


class MappingError(ValueError):
    """A claim cannot be written with the layout (missing required value, bad value)."""


@dataclass(frozen=True, slots=True)
class MappedSegment:
    id: str
    name: str
    fields: tuple[tuple[str, str], ...]  # (field id, wire text), in layout order


@dataclass(frozen=True, slots=True)
class MappedTransaction:
    claim_id: str
    segments: tuple[MappedSegment, ...]


@dataclass(frozen=True, slots=True)
class MappedTransmission:
    transaction_code: str
    header: tuple[tuple[str, str], ...]  # (header field name, fixed-width text)
    transactions: tuple[MappedTransaction, ...]


def map_claims(
    rows: Sequence[Mapping[str, Any]],
    layout: Layout,
    transaction_code: str | None = None,
    *,
    tables: TableSet | None = None,
) -> MappedTransmission:
    """One transmission: the header (from the first row) and one transaction per row.

    ``rows`` are ``pharmacy_claim`` dict rows. ``tables`` supplies the companions
    (``member``, ``provider``, ``drug_reference``) that "column" sources may name.
    """
    if not rows:
        raise MappingError("no pharmacy claim rows to map")
    tdef = layout.transaction(transaction_code)
    companions = tables if tables is not None else TableSet({})
    count = len(rows)
    header = tuple(
        (h.name, _header_text(h, rows[0], companions, tdef.code, count)) for h in layout.header
    )
    out: list[MappedTransaction] = []
    for n, row in enumerate(rows, start=1):
        claim_id = str(row.get("rx_claim_id"))
        segs: list[MappedSegment] = []
        for seg in tdef.segments:
            mapped = _segment(seg, row, companions, tdef.code, count, n, claim_id)
            if mapped is not None:
                segs.append(mapped)
        out.append(MappedTransaction(claim_id, tuple(segs)))
    return MappedTransmission(tdef.code, header, tuple(out))


def _header_text(
    h: HeaderField, row: Mapping[str, Any], tables: TableSet, code: str, count: int
) -> str:
    claim_id = str(row.get("rx_claim_id"))
    value = _value(h.source, row, tables, code, count, 1)
    if value is None or value == "":
        raise MappingError(f"claim {claim_id}: header field {h.name!r} has no value")
    where = f"claim {claim_id}: header field {h.name!r}"
    text = value if h.source.constant is not None else _format(h.format, value, where)
    if len(text) > h.length:
        raise MappingError(f"claim {claim_id}: header field {h.name!r} is longer than {h.length}")
    if h.format.type == "alphanumeric":
        return text.ljust(h.length)
    return text.rjust(h.length, "0")


def _segment(
    seg: Segment,
    row: Mapping[str, Any],
    tables: TableSet,
    code: str,
    count: int,
    n: int,
    claim_id: str,
) -> MappedSegment | None:
    fields: list[tuple[str, str]] = []
    data_driven = False
    for f in seg.fields:
        text = _field_text(f, row, tables, code, count, n, claim_id, seg.id)
        if text is None:
            continue
        if f.source.constant is None:
            data_driven = True
        fields.append((f.id, text))
    if seg.usage == "situational" and not data_driven:
        return None
    return MappedSegment(seg.id, seg.name, tuple(fields))


def _field_text(
    f: Field,
    row: Mapping[str, Any],
    tables: TableSet,
    code: str,
    count: int,
    n: int,
    claim_id: str,
    seg_id: str,
) -> str | None:
    where = f"claim {claim_id}: segment {seg_id} field {f.id} ({f.name})"
    value = _value(f.source, row, tables, code, count, n)
    if f.source.constant is not None:  # a constant is written exactly as given
        if len(value) > f.format.max_length:
            raise MappingError(f"{where}: constant is longer than max_length")
        return str(value)
    if value is None or value == "":
        if f.usage == "required":
            raise MappingError(f"{where}: required, but the source has no value")
        return None
    if f.value_map is not None:
        key = ("true" if value else "false") if isinstance(value, bool) else str(value)
        if key in f.value_map:
            value = f.value_map[key]
        elif "*" in f.value_map:
            value = f.value_map["*"]
        else:
            raise MappingError(f"{where}: value {key!r} is not in the field's map")
    elif isinstance(value, bool):
        raise MappingError(f"{where}: a true/false source needs a 'map' to text")
    return _format(f.format, value, where)


def _format(fmt: FieldFormat, value: Any, where: str) -> str:
    try:
        text = fmt.encode(value)
    except ValueError as exc:
        raise MappingError(f"{where}: {exc}") from None
    if any(ord(ch) < 0x20 or ord(ch) > 0xFF for ch in text):
        raise MappingError(f"{where}: value has control or non-Latin-1 characters")
    return text


def _value(
    src: Source, row: Mapping[str, Any], tables: TableSet, code: str, count: int, n: int
) -> Any:
    if src.constant is not None:
        return src.constant
    if src.expr is not None:
        return {"transaction_code": code, "transaction_count": count, "row_number": n}[src.expr]
    assert src.column is not None
    alias, col = src.column
    return _companion(alias, row, tables).get(col)


def _companion(alias: str, row: Mapping[str, Any], tables: TableSet) -> Mapping[str, Any]:
    if alias == "pharmacy_claim":
        return row
    if alias == "member":
        found = tables.index("member", "member_id").get(row.get("member_id"))
    elif alias == "pharmacy":
        found = tables.index("provider", "npi").get(row.get("pharmacy_npi"))
    elif alias == "prescriber":
        found = tables.index("provider", "npi").get(row.get("prescriber_npi"))
    else:
        found = tables.index("drug_reference", "ndc").get(row.get("ndc"))
    return found or {}
