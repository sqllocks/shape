"""The bring-your-own specification layout: a JSON document the licensed user supplies.

The document says which transactions exist, which segments and fields each has, where every
field's value comes from and how it is formatted. This package never ships those facts.

Document shape (all keys shown; ``?`` marks an optional key)::

    {
      "layout_format": 1,
      "name": "my layout",
      "separators?": {"segment": "1E", "group": "1D", "field": "1C"},   # hex bytes
      "segment_id_field": "XX",             # 2-character identifier of the segment-id field
      "header": [ {"name": ..., "length": 6, "format": {...}, "role?": ..., <source>} ],
      "transactions": {
        "<code>": {
          "kind": "request" | "response", "name?": ...,
          "segments": [ {"id": ..., "name": ..., "usage?": "required" | "situational",
                         "fields": [ {"id": "AB", "name": ..., "usage?": "required" |
                                      "optional" | "situational", "format": {...},
                                      "map?": {...}, <source>} ] } ] } }
    }

A ``<source>`` is exactly one of ``"column": "<alias>.<column>"`` (aliases: ``pharmacy_claim``,
``member``, ``pharmacy``, ``prescriber``, ``drug_reference``), ``"constant": "<text>"`` or
``"expr": "<key>"`` (``transaction_code``, ``transaction_count``, ``row_number``).

A ``format`` is ``{"type": "alphanumeric" | "numeric" | "date" | "amount", "max_length": n}``
plus, for ``numeric``/``amount``: ``decimals`` (digits after the point; default 0 / 2),
``implied_decimal`` (default true: write the digits without a point), ``overpunch`` (default
false: carry the sign in the last digit, COBOL style) and ``zero_pad`` (default false);
for ``alphanumeric``: ``uppercase`` (default false). Dates are written as ``CCYYMMDD``.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TypeGuard

from .. import contract

LAYOUT_FORMAT = 1
DEFAULT_SEPARATORS = {"segment": 0x1E, "group": 0x1D, "field": 0x1C}
TYPES = ("alphanumeric", "numeric", "date", "amount")
USAGES = ("required", "optional", "situational")
ROLES = ("transaction_code", "transaction_count")
EXPRS = ("transaction_code", "transaction_count", "row_number")

# Table aliases usable in a "column" source, and the contract table each one reads.
ALIASES: dict[str, str] = {
    "pharmacy_claim": "pharmacy_claim",
    "member": "member",
    "pharmacy": "provider",
    "prescriber": "provider",
    "drug_reference": "drug_reference",
}

_POS_OVERPUNCH = "{ABCDEFGHI"
_NEG_OVERPUNCH = "}JKLMNOPQR"


class LayoutError(ValueError):
    """A layout document is invalid, or a bring-your-own layout was not supplied."""


@dataclass(frozen=True, slots=True)
class FieldFormat:
    """How one field value is written as text and read back."""

    type: str
    max_length: int
    decimals: int = 0
    implied_decimal: bool = True
    overpunch: bool = False
    zero_pad: bool = False
    uppercase: bool = False

    def encode(self, value: Any) -> str:
        """The wire text of ``value``. Raises ``ValueError`` when it does not fit."""
        if self.type == "date":
            if isinstance(value, dt.datetime):
                value = value.date()
            if not isinstance(value, dt.date):
                raise ValueError(f"expected a date, got {value!r}")
            text = value.strftime("%Y%m%d")
        elif self.type in ("numeric", "amount"):
            text = self._encode_number(value)
        else:
            text = str(value)
            if self.uppercase:
                text = text.upper()
        if len(text) > self.max_length:
            raise ValueError(f"value {text!r} is longer than max_length {self.max_length}")
        if self.zero_pad and self.type in ("numeric", "amount"):
            text = self._zero_pad(text)
        return text

    def _zero_pad(self, text: str) -> str:
        sign = text[0] if text[:1] == "-" else ""
        return sign + text[len(sign) :].rjust(self.max_length - len(sign), "0")

    def _encode_number(self, value: Any) -> str:
        if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
            raise ValueError(f"expected a number, got {value!r}")
        quantum = Decimal(1).scaleb(-self.decimals)
        d = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_EVEN)
        negative = d < 0
        d = abs(d)
        if self.implied_decimal:
            digits = str(int(d.scaleb(self.decimals)))
        else:
            digits = format(d, "f")
        if negative and d != 0:
            if self.overpunch:
                return digits[:-1] + _NEG_OVERPUNCH[int(digits[-1])]
            return "-" + digits
        if self.overpunch:
            return digits[:-1] + _POS_OVERPUNCH[int(digits[-1])]
        return digits

    def decode(self, text: str) -> Any:
        """The Python value of wire ``text`` (date, Decimal or str); raises ``ValueError``."""
        if self.type == "date":
            return dt.datetime.strptime(text, "%Y%m%d").date()
        if self.type in ("numeric", "amount"):
            return self._decode_number(text)
        return text

    def _decode_number(self, text: str) -> Decimal:
        negative = text.startswith("-")
        body = text[1:] if negative else text
        if self.overpunch and body:
            last = body[-1]
            if last in _POS_OVERPUNCH:
                body = body[:-1] + str(_POS_OVERPUNCH.index(last))
            elif last in _NEG_OVERPUNCH:
                body = body[:-1] + str(_NEG_OVERPUNCH.index(last))
                negative = True
        try:
            d = Decimal(body)
        except InvalidOperation:
            raise ValueError(f"not a number: {text!r}") from None
        if self.implied_decimal:
            d = d.scaleb(-self.decimals)
        return -d if negative else d


@dataclass(frozen=True, slots=True)
class Source:
    """Where a field's value comes from: exactly one of column, constant, expr is set."""

    column: tuple[str, str] | None = None  # (alias, column)
    constant: str | None = None
    expr: str | None = None


@dataclass(frozen=True, slots=True)
class HeaderField:
    name: str
    length: int
    format: FieldFormat
    source: Source
    role: str | None = None


@dataclass(frozen=True, slots=True)
class Field:
    id: str
    name: str
    usage: str
    format: FieldFormat
    source: Source
    value_map: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class Segment:
    id: str
    name: str
    usage: str
    fields: tuple[Field, ...]


@dataclass(frozen=True, slots=True)
class TransactionDef:
    code: str
    kind: str
    name: str
    segments: tuple[Segment, ...]


@dataclass(frozen=True, slots=True)
class Layout:
    """A validated layout. Build one with :meth:`from_dict` or :meth:`load`."""

    name: str
    segment_id_field: str
    separators: Mapping[str, int]
    header: tuple[HeaderField, ...]
    transactions: Mapping[str, TransactionDef]

    @property
    def header_length(self) -> int:
        return sum(f.length for f in self.header)

    def transaction(self, code: str | None = None) -> TransactionDef:
        """The transaction ``code``; ``None`` is the first ``request`` transaction."""
        if code is None:
            for t in self.transactions.values():
                if t.kind == "request":
                    return t
            raise LayoutError("the layout defines no request transaction")
        try:
            return self.transactions[code]
        except KeyError:
            raise LayoutError(
                f"layout has no transaction {code!r}; it defines {list(self.transactions)}"
            ) from None

    def header_role(self, role: str) -> HeaderField | None:
        return next((f for f in self.header if f.role == role), None)

    @classmethod
    def load(cls, path: str | Path) -> Layout:
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            raise LayoutError(f"cannot read layout file {str(path)!r}: {exc}") from None
        except json.JSONDecodeError as exc:
            raise LayoutError(f"layout file {str(path)!r} is not valid JSON: {exc}") from None
        return cls.from_dict(doc)

    @classmethod
    def from_dict(cls, doc: Any) -> Layout:
        """Validate ``doc`` by hand; every problem found is reported in one ``LayoutError``."""
        errors: list[str] = []
        layout = _build(doc, errors)
        if errors or layout is None:
            raise LayoutError(
                "invalid NCPDP layout ("
                + str(len(errors))
                + " problem(s)):\n  - "
                + "\n  - ".join(errors)
            )
        return layout


def _is_int(v: Any) -> TypeGuard[int]:
    return isinstance(v, int) and not isinstance(v, bool)


def _text(obj: Mapping[str, Any], key: str, path: str, errors: list[str]) -> str:
    v = obj.get(key)
    if not isinstance(v, str) or not v:
        errors.append(f"{path}.{key}: required, a non-empty string")
        return ""
    return v


def _unknown(obj: Mapping[str, Any], known: tuple[str, ...], path: str, errors: list[str]) -> None:
    for k in obj:
        if k not in known:
            errors.append(f"{path}: unknown key {k!r}; allowed: {list(known)}")


def _separators(doc: Any, path: str, errors: list[str]) -> dict[str, int]:
    out = dict(DEFAULT_SEPARATORS)
    if doc is None:
        return out
    if not isinstance(doc, dict):
        errors.append(f"{path}: must be an object")
        return out
    _unknown(doc, tuple(DEFAULT_SEPARATORS), path, errors)
    for k, v in doc.items():
        if k not in DEFAULT_SEPARATORS:
            continue
        if not isinstance(v, str) or not re.fullmatch(r"[0-9A-Fa-f]{2}", v):
            errors.append(f"{path}.{k}: must be two hex digits such as '1C'")
        else:
            out[k] = int(v, 16)
    if len(set(out.values())) != 3:
        errors.append(f"{path}: the three separators must be different bytes")
    return out


def _format(obj: Any, path: str, errors: list[str]) -> FieldFormat | None:
    if not isinstance(obj, dict):
        errors.append(
            f"{path}: required, an object like {{'type': 'alphanumeric', 'max_length': 5}}"
        )
        return None
    known = (
        "type",
        "max_length",
        "decimals",
        "implied_decimal",
        "overpunch",
        "zero_pad",
        "uppercase",
    )
    _unknown(obj, known, path, errors)
    start = len(errors)
    typ = obj.get("type")
    if typ not in TYPES:
        errors.append(f"{path}.type: must be one of {list(TYPES)}, got {typ!r}")
    ml = obj.get("max_length")
    if not _is_int(ml) or ml < 1:
        errors.append(f"{path}.max_length: required, a positive integer")
    for flag in ("implied_decimal", "overpunch", "zero_pad", "uppercase"):
        if flag in obj and not isinstance(obj[flag], bool):
            errors.append(f"{path}.{flag}: must be true or false")
    dec = obj.get("decimals", 2 if typ == "amount" else 0)
    if not _is_int(dec) or dec < 0 or dec > 9:
        errors.append(f"{path}.decimals: must be an integer 0-9")
    if typ == "date" and ml != 8 and _is_int(ml):
        errors.append(f"{path}.max_length: a CCYYMMDD date has max_length 8")
    if typ in ("alphanumeric", "date"):
        for k in ("decimals", "implied_decimal", "overpunch", "zero_pad"):
            if k in obj:
                errors.append(f"{path}.{k}: only applies to numeric and amount formats")
    if typ != "alphanumeric" and "uppercase" in obj:
        errors.append(f"{path}.uppercase: only applies to the alphanumeric format")
    if len(errors) > start:
        return None
    assert _is_int(ml)
    return FieldFormat(
        type=str(typ),
        max_length=int(ml),
        decimals=int(dec),
        implied_decimal=bool(obj.get("implied_decimal", True)),
        overpunch=bool(obj.get("overpunch", False)),
        zero_pad=bool(obj.get("zero_pad", False)),
        uppercase=bool(obj.get("uppercase", False)),
    )


def _source(obj: Mapping[str, Any], path: str, errors: list[str]) -> Source | None:
    given = [k for k in ("column", "constant", "expr") if k in obj]
    if len(given) != 1:
        errors.append(f"{path}: give exactly one of column, constant, expr (found {given})")
        return None
    key = given[0]
    v = obj[key]
    if not isinstance(v, str) or (not v and key != "constant"):
        errors.append(f"{path}.{key}: must be a string")
        return None
    if key == "constant":
        return Source(constant=v)
    if key == "expr":
        if v not in EXPRS:
            errors.append(f"{path}.expr: unknown key {v!r}; known: {list(EXPRS)}")
            return None
        return Source(expr=v)
    alias, _, col = v.partition(".")
    table = ALIASES.get(alias)
    if table is None or not col:
        errors.append(
            f"{path}.column: {v!r} must be '<alias>.<column>' with alias in {list(ALIASES)}"
        )
        return None
    if col not in {c.name for c in contract.CONTRACT[table]}:
        errors.append(f"{path}.column: {col!r} is not a column of the contract table {table!r}")
        return None
    return Source(column=(alias, col))


def _id2(obj: Mapping[str, Any], key: str, path: str, errors: list[str], seps: set[int]) -> str:
    v = obj.get(key)
    if not isinstance(v, str) or len(v) != 2 or not v.isascii() or not v.isprintable():
        errors.append(f"{path}.{key}: required, exactly 2 printable ASCII characters")
        return ""
    if ord(v[0]) in seps or ord(v[1]) in seps:
        errors.append(f"{path}.{key}: contains a separator byte")
    return v


def _header_field(obj: Any, path: str, errors: list[str]) -> HeaderField | None:
    if not isinstance(obj, dict):
        errors.append(f"{path}: must be an object")
        return None
    _unknown(obj, ("name", "length", "format", "role", "column", "constant", "expr"), path, errors)
    start = len(errors)
    name = _text(obj, "name", path, errors)
    length = obj.get("length")
    if not _is_int(length) or length < 1:
        errors.append(f"{path}.length: required, a positive integer")
    fmt = _format(obj.get("format"), f"{path}.format", errors)
    if fmt is not None and _is_int(length) and fmt.max_length != length:
        errors.append(f"{path}.format.max_length: a header field is fixed width; must equal length")
    role = obj.get("role")
    if role is not None and role not in ROLES:
        errors.append(f"{path}.role: must be one of {list(ROLES)}")
    src = _source(obj, path, errors)
    if len(errors) > start or fmt is None or src is None:
        return None
    assert _is_int(length)
    return HeaderField(name, length, fmt, src, role)


def _field(obj: Any, path: str, errors: list[str], seps: set[int]) -> Field | None:
    if not isinstance(obj, dict):
        errors.append(f"{path}: must be an object")
        return None
    _unknown(
        obj, ("id", "name", "usage", "format", "map", "column", "constant", "expr"), path, errors
    )
    start = len(errors)
    fid = _id2(obj, "id", path, errors, seps)
    name = _text(obj, "name", path, errors)
    usage = obj.get("usage", "required")
    if usage not in USAGES:
        errors.append(f"{path}.usage: must be one of {list(USAGES)}")
    fmt = _format(obj.get("format"), f"{path}.format", errors)
    src = _source(obj, path, errors)
    vmap = obj.get("map")
    if vmap is not None:
        if not isinstance(vmap, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in vmap.items()
        ):
            errors.append(f"{path}.map: must map text to text")
            vmap = None
        elif fmt is not None and fmt.type not in ("alphanumeric", "numeric"):
            errors.append(f"{path}.map: only for alphanumeric or numeric formats")
        elif src is not None and src.constant is not None:
            errors.append(f"{path}.map: a constant needs no map")
    if len(errors) > start or fmt is None or src is None:
        return None
    return Field(fid, name, str(usage), fmt, src, vmap)


def _segment(obj: Any, path: str, errors: list[str], seps: set[int]) -> Segment | None:
    if not isinstance(obj, dict):
        errors.append(f"{path}: must be an object")
        return None
    _unknown(obj, ("id", "name", "usage", "fields"), path, errors)
    start = len(errors)
    sid = obj.get("id")
    if not isinstance(sid, str) or not sid or not sid.isascii() or not sid.isprintable():
        errors.append(f"{path}.id: required, a non-empty printable ASCII string")
    elif any(ord(ch) in seps for ch in sid):
        errors.append(f"{path}.id: contains a separator byte")
    name = _text(obj, "name", path, errors)
    usage = obj.get("usage", "required")
    if usage not in ("required", "situational"):
        errors.append(f"{path}.usage: must be 'required' or 'situational'")
    raw = obj.get("fields")
    fields: list[Field] = []
    if not isinstance(raw, list) or not raw:
        errors.append(f"{path}.fields: required, a non-empty list")
    else:
        seen: set[str] = set()
        for i, fo in enumerate(raw):
            f = _field(fo, f"{path}.fields[{i}]", errors, seps)
            if f is not None:
                if f.id in seen:
                    errors.append(f"{path}.fields[{i}].id: duplicate field id {f.id!r}")
                seen.add(f.id)
                fields.append(f)
    if len(errors) > start:
        return None
    return Segment(str(sid), name, str(usage), tuple(fields))


def _build(doc: Any, errors: list[str]) -> Layout | None:
    if not isinstance(doc, dict):
        errors.append("$: the layout must be a JSON object")
        return None
    _unknown(
        doc,
        (
            "layout_format",
            "name",
            "licence_note",
            "separators",
            "segment_id_field",
            "header",
            "transactions",
        ),
        "$",
        errors,
    )
    if doc.get("layout_format") != LAYOUT_FORMAT:
        errors.append(f"$.layout_format: must be {LAYOUT_FORMAT}")
    name = _text(doc, "name", "$", errors)
    seps = _separators(doc.get("separators"), "$.separators", errors)
    sepset = set(seps.values())
    sid_field = _id2(doc, "segment_id_field", "$", errors, sepset)

    header: list[HeaderField] = []
    raw_h = doc.get("header")
    if not isinstance(raw_h, list) or not raw_h:
        errors.append("$.header: required, a non-empty list of fixed-width fields")
    else:
        for i, ho in enumerate(raw_h):
            h = _header_field(ho, f"$.header[{i}]", errors)
            if h is not None:
                header.append(h)
        roles = [h.role for h in header if h.role]
        if "transaction_code" not in roles:
            errors.append("$.header: one field needs role 'transaction_code'")
        if len(roles) != len(set(roles)):
            errors.append("$.header: a role may be used by one field only")

    transactions: dict[str, TransactionDef] = {}
    raw_t = doc.get("transactions")
    if not isinstance(raw_t, dict) or not raw_t:
        errors.append("$.transactions: required, an object keyed by transaction code")
    else:
        code_len = next((h.length for h in header if h.role == "transaction_code"), None)
        for code, to in raw_t.items():
            path = f"$.transactions[{code!r}]"
            if code_len is not None and len(code) != code_len:
                errors.append(f"{path}: code must be {code_len} characters (the header's width)")
            if not isinstance(to, dict):
                errors.append(f"{path}: must be an object")
                continue
            _unknown(to, ("kind", "name", "segments"), path, errors)
            kind = to.get("kind")
            if kind not in ("request", "response"):
                errors.append(f"{path}.kind: must be 'request' or 'response'")
            tname = to.get("name", code)
            raw_s = to.get("segments")
            segs: list[Segment] = []
            if not isinstance(raw_s, list) or not raw_s:
                errors.append(f"{path}.segments: required, a non-empty list")
            else:
                for i, so in enumerate(raw_s):
                    s = _segment(so, f"{path}.segments[{i}]", errors, sepset)
                    if s is not None:
                        segs.append(s)
                ids = [s.id for s in segs]
                if len(ids) != len(set(ids)):
                    errors.append(f"{path}.segments: a segment id repeats")
            if kind in ("request", "response") and isinstance(tname, str):
                transactions[code] = TransactionDef(code, str(kind), tname, tuple(segs))
    if errors:
        return None
    return Layout(name, sid_field, seps, tuple(header), transactions)
