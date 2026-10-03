"""The generic delimited wire structure, independent of any licensed field table.

A transmission is a fixed-width header (layout-driven, no separators inside), then for each
transaction a group separator followed by its segments. Each segment is a segment separator,
the segment-id field (2-character identifier plus the segment id) and then, for every field,
a field separator, the 2-character field id and the value.

The separator bytes 0x1E (segment), 0x1D (group) and 0x1C (field) are what public payer
documents for the version D telecommunication standard state; the layout may override them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..common import TableSet
from .layout import Layout
from .mapping import MappedTransmission, map_claims


class WireError(ValueError):
    """Bytes that are not a transmission of the layout."""


@dataclass(frozen=True, slots=True)
class ParsedSegment:
    id: str
    fields: tuple[tuple[str, str], ...]  # (field id, raw text), in wire order

    def get(self, field_id: str) -> str | None:
        return next((v for k, v in self.fields if k == field_id), None)


@dataclass(frozen=True, slots=True)
class ParsedTransaction:
    segments: tuple[ParsedSegment, ...]

    def segment(self, segment_id: str) -> ParsedSegment | None:
        return next((s for s in self.segments if s.id == segment_id), None)

    def decoded(self, layout: Layout, code: str) -> dict[str, dict[str, Any]]:
        """Segment id -> {field name -> decoded value}, using the layout's formats."""
        tdef = layout.transaction(code)
        defs = {s.id: {f.id: f for f in s.fields} for s in tdef.segments}
        out: dict[str, dict[str, Any]] = {}
        for seg in self.segments:
            fdefs = defs.get(seg.id, {})
            vals: dict[str, Any] = {}
            for fid, raw in seg.fields:
                fd = fdefs.get(fid)
                try:
                    vals[fd.name if fd else fid] = fd.format.decode(raw) if fd else raw
                except ValueError as exc:
                    raise WireError(f"segment {seg.id} field {fid}: {exc}") from None
            out[seg.id] = vals
        return out


@dataclass(frozen=True, slots=True)
class ParsedTransmission:
    transaction_code: str
    header: Mapping[str, str]  # header field name -> raw fixed-width text
    transactions: tuple[ParsedTransaction, ...]


def write_transaction(
    rows: Sequence[Mapping[str, Any]],
    layout: Layout,
    transaction_code: str | None = None,
    *,
    tables: TableSet | None = None,
) -> bytes:
    """``pharmacy_claim`` rows to one transmission (one transaction per row)."""
    return serialize(map_claims(rows, layout, transaction_code, tables=tables), layout)


def serialize(mapped: MappedTransmission, layout: Layout) -> bytes:
    seg_sep = bytes([layout.separators["segment"]])
    grp_sep = bytes([layout.separators["group"]])
    fld_sep = bytes([layout.separators["field"]])
    parts: list[bytes] = [_latin1("".join(text for _, text in mapped.header), "header")]
    for tx in mapped.transactions:
        parts.append(grp_sep)
        for seg in tx.segments:
            parts.append(seg_sep)
            parts.append(_latin1(layout.segment_id_field + seg.id, "segment id"))
            for fid, text in seg.fields:
                parts.append(fld_sep + _latin1(fid + text, f"field {fid}"))
    return b"".join(parts)


def _latin1(text: str, what: str) -> bytes:
    try:
        return text.encode("latin-1")
    except UnicodeEncodeError:
        raise WireError(f"{what} has characters outside Latin-1") from None


def parse_transaction(data: bytes, layout: Layout) -> ParsedTransmission:
    """Bytes to the header, and per transaction the segments with their (id, value) fields."""
    seg_sep = layout.separators["segment"]
    grp_sep = layout.separators["group"]
    fld_sep = layout.separators["field"]
    hlen = layout.header_length
    chunks = data.split(bytes([grp_sep]))
    head = chunks[0]
    if len(head) != hlen:
        raise WireError(f"header is {len(head)} bytes; the layout's header is {hlen}")
    text = head.decode("latin-1")
    header: dict[str, str] = {}
    pos = 0
    for h in layout.header:
        header[h.name] = text[pos : pos + h.length]
        pos += h.length
    code_field = layout.header_role("transaction_code")
    assert code_field is not None
    code = header[code_field.name].strip()
    if code not in layout.transactions:
        raise WireError(f"transaction code {code!r} is not defined by the layout")
    txs: list[ParsedTransaction] = []
    for chunk in chunks[1:]:
        segs: list[ParsedSegment] = []
        pieces = chunk.split(bytes([seg_sep]))
        if pieces[0]:
            raise WireError("bytes between a group separator and the first segment separator")
        for piece in pieces[1:]:
            fields = piece.split(bytes([fld_sep]))
            ident = fields[0].decode("latin-1")
            prefix = layout.segment_id_field
            if not ident.startswith(prefix) or len(ident) == len(prefix):
                raise WireError(f"segment does not start with the id field {prefix!r}: {ident!r}")
            pairs: list[tuple[str, str]] = []
            for raw in fields[1:]:
                s = raw.decode("latin-1")
                if len(s) < 2:
                    raise WireError(f"field shorter than a 2-character id: {s!r}")
                pairs.append((s[:2], s[2:]))
            segs.append(ParsedSegment(ident[len(prefix) :], tuple(pairs)))
        txs.append(ParsedTransaction(tuple(segs)))
    return ParsedTransmission(code, header, tuple(txs))
