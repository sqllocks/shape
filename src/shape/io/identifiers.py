"""Identifier columns in CSV files: digits that name something and are not numbers.

A ZIP code, an NDC, an NPI or a member number is written with digits, and Arrow's CSV type
inference reads such a column as an integer: ``02134`` becomes 2134 and the placeholder ``00000``
becomes 0. The profile then describes numbers that never existed, and anything generated from it
drops the zeros. Reading a column as text keeps the value; this module decides which integer
columns are really identifiers.

The rule (kept narrow, because a wrong "text" costs the statistics of a real number):

* **leading zeros**: some value is two or more digits and starts with ``0`` (``02134``, ``00000``).
  A number is not written that way, so the column is text whatever its name;
* **fixed width and a name**: every value is digits only, all with the same width of five or more
  (a ZIP, an NPI, an NDC without dashes, a member number), and the column name says it is an
  identifier (``zip``, ``postal``, ``npi``, ``ndc``, ``id``, ``number``, ``code``, ...);
* **explicit**: the caller names the column (``string_columns``) or gives its type.

An integer column that is only *suspicious* (a fixed width of five or more digits without a
telling name, or a telling name without a fixed width) stays an integer, and ``suspects`` says why,
so the profile can warn and name the option that keeps it as text.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]

# Words that mean "this is a label, not a quantity". A column name is split into words (on
# separators and at camelCase changes) and each word is matched whole.
_NAME_WORDS = frozenset(
    {
        "zip",
        "zipcode",
        "postal",
        "postcode",
        "npi",
        "ndc",
        "mrn",
        "ssn",
        "ein",
        "tin",
        "isbn",
        "upc",
        "ean",
        "gtin",
        "cusip",
        "iban",
        "id",
        "ids",
        "number",
        "num",
        "no",
        "nbr",
        "code",
        "key",
        "ref",
        "account",
        "acct",
        "member",
        "patient",
        "provider",
        "claim",
        "policy",
        "phone",
        "fax",
        "barcode",
        "sku",
    }
)
# Words that, alone, make an integer column worth a warning (``id`` and ``number`` also name
# plain counters and keys, which are integers).
_STRONG_WORDS = frozenset(
    _NAME_WORDS - {"id", "ids", "number", "num", "no", "nbr", "code", "key", "ref"}
)
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
MIN_FIXED_WIDTH = 5


def _words(name: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(name)]


def _is_word(word: str, vocabulary: frozenset[str]) -> bool:
    if word in vocabulary:
        return True
    # memberid, patientno: a known word followed by id / no / num / nbr / number
    return any(
        word.endswith(tail) and word[: -len(tail)] in vocabulary
        for tail in ("id", "no", "num", "nbr", "number")
    )


def name_suggests_identifier(name: str) -> bool:
    """True when a word of ``name`` says the column holds identifiers (``zip``, ``member_id``,
    ``NPI``, ``ndcCode``)."""
    return any(_is_word(w, _NAME_WORDS) for w in _words(name))


def name_strongly_suggests_identifier(name: str) -> bool:
    """True when ``name`` names a kind of identifier that is never a quantity (``zip``, ``npi``,
    ``ndc``, ``mrn``, ``phone``), as opposed to ``id`` or ``code``."""
    return any(_is_word(w, _STRONG_WORDS) for w in _words(name))


@dataclass
class DigitStats:
    """What the text of one column says about whether it holds identifiers."""

    values: int = 0
    leading_zero: bool = False
    all_digits: bool = True
    min_width: int | None = None
    max_width: int | None = None

    def add(self, text: Any) -> None:
        """Take a batch of the column's text (an Arrow string array or chunked array)."""
        col = text.drop_null()
        n = len(col)
        if not n:
            return
        self.values += n
        if not self.leading_zero:
            self.leading_zero = bool(pc.any(pc.match_substring_regex(col, r"^[+-]?0[0-9]")).as_py())
        if self.all_digits:
            self.all_digits = bool(pc.all(pc.match_substring_regex(col, r"^[0-9]+$")).as_py())
        lengths = pc.utf8_length(col)
        lo, hi = int(pc.min(lengths).as_py()), int(pc.max(lengths).as_py())
        self.min_width = lo if self.min_width is None else min(self.min_width, lo)
        self.max_width = hi if self.max_width is None else max(self.max_width, hi)

    @property
    def fixed_width(self) -> int | None:
        """The width every value shares, when they are all digits of one width."""
        if self.values and self.all_digits and self.min_width == self.max_width:
            return self.min_width
        return None


def judge(name: str, stats: DigitStats) -> str | None:
    """Why the integer column ``name`` is an identifier (so it should be read as text), or
    ``None`` when it is a number."""
    if stats.leading_zero:
        return "values with leading zeros"
    width = stats.fixed_width
    if width is not None and width >= MIN_FIXED_WIDTH and name_suggests_identifier(name):
        return f"a fixed width of {width} digits and an identifier name"
    return None


def suspect(name: str, stats: DigitStats) -> str | None:
    """Why the integer column ``name`` might still be an identifier although ``judge`` kept it a
    number (the profile warns), or ``None``."""
    width = stats.fixed_width
    if width is not None and width >= MIN_FIXED_WIDTH:
        return f"every value has {width} digits"
    if name_strongly_suggests_identifier(name):
        return "its name says it holds identifiers"
    return None


@dataclass(frozen=True)
class Suspect:
    column: str
    reason: str


def _is_integer_type(t: pa.DataType) -> bool:
    return bool(pa.types.is_integer(t) or pa.types.is_decimal(t))


def integer_columns(schema: pa.Schema) -> list[str]:
    """The columns of ``schema`` Arrow typed as integers (the only ones identifiers hide in)."""
    return [f.name for f in schema if _is_integer_type(f.type)]


def scan_text(
    path: str | Path,
    columns: Iterable[str],
    *,
    read_options: pacsv.ReadOptions,
    parse_options: pacsv.ParseOptions,
    null_values: Iterable[str] | None = None,
    max_batches: int | None = None,
) -> dict[str, DigitStats]:
    """The digit statistics of ``columns``, read from the file as text.

    Only those columns are parsed, in blocks, so memory stays bounded; a column drops out of the
    scan once it shows leading zeros (the answer is settled). ``max_batches`` stops after that many
    blocks (the first one is what a streamed read of a huge file has typed its columns from).
    """
    wanted = list(columns)
    stats = {c: DigitStats() for c in wanted}
    if not wanted:
        return stats
    kwargs: dict[str, Any] = {
        "include_columns": wanted,
        "column_types": {c: pa.string() for c in wanted},
        "strings_can_be_null": True,
        "quoted_strings_can_be_null": True,
    }
    if null_values is not None:
        kwargs["null_values"] = list(null_values)
    reader = pacsv.open_csv(
        path,
        read_options=read_options,
        parse_options=parse_options,
        convert_options=pacsv.ConvertOptions(**kwargs),
    )
    live = set(wanted)
    seen = 0
    with reader:
        for batch in reader:
            for name in list(live):
                stats[name].add(batch.column(name))
                if stats[name].leading_zero:
                    live.discard(name)
            seen += 1
            if not live or (max_batches is not None and seen >= max_batches):
                break
    return stats


def identifier_columns(
    path: str | Path,
    schema: pa.Schema,
    *,
    read_options: pacsv.ReadOptions,
    parse_options: pacsv.ParseOptions,
    null_values: Iterable[str] | None = None,
    skip: Iterable[str] = (),
    max_batches: int | None = None,
    on_suspect: Callable[[Suspect], None] | None = None,
) -> dict[str, str]:
    """``{column: reason}`` for every integer column of ``schema`` that holds identifiers.

    ``skip`` names columns whose type the caller fixed. ``on_suspect`` receives the integer
    columns that stay numbers although they look like identifiers.
    """
    skipped = set(skip)
    candidates = [c for c in integer_columns(schema) if c not in skipped]
    stats = scan_text(
        path,
        candidates,
        read_options=read_options,
        parse_options=parse_options,
        null_values=null_values,
        max_batches=max_batches,
    )
    found: dict[str, str] = {}
    for name, s in stats.items():
        reason = judge(name, s)
        if reason is not None:
            found[name] = reason
        elif on_suspect is not None and (why := suspect(name, s)) is not None:
            on_suspect(Suspect(name, why))
    return found


def suspect_message(items: Iterable[Suspect], *, option: str) -> str | None:
    """The warning text for integer columns that look like identifiers, or ``None``."""
    items = list(items)
    if not items:
        return None
    listed = "; ".join(f"{s.column!r} ({s.reason})" for s in items)
    names = ",".join(s.column for s in items)
    return (
        f"read as integers although they look like identifiers: {listed}. A number loses its "
        f"leading zeros; if these are identifiers, keep them as text with {option} {names}."
    )


_TYPE_NAMES = {
    "string": pa.string(),
    "text": pa.string(),
    "integer": pa.int64(),
    "float": pa.float64(),
    "boolean": pa.bool_(),
    "date": pa.date32(),
    "datetime": pa.timestamp("us"),
}


def resolve_type(spec: Any) -> pa.DataType:
    """A column type given as an Arrow type, a friendly name (``string``, ``text``, ``integer``,
    ``float``, ``boolean``, ``date``, ``datetime``) or an Arrow alias (``int64``, ``float32``)."""
    if isinstance(spec, pa.DataType):
        return spec
    if isinstance(spec, str):
        named = _TYPE_NAMES.get(spec.lower())
        if named is not None:
            return named
        try:
            return pa.type_for_alias(spec)
        except (ValueError, KeyError):
            pass
    raise ValueError(
        f"unknown Arrow type name {spec!r}: use string, integer, float, boolean, date, datetime "
        "or an Arrow type name such as int32"
    )


def read_csv_keeping_identifiers(source: Any) -> pa.Table:
    """A whole CSV (a path, or a seekable file object) as a table, with Arrow's default options
    and the identifier rule: integer columns that hold identifiers are text."""
    ro, po = pacsv.ReadOptions(), pacsv.ParseOptions()

    def rewind() -> None:
        if hasattr(source, "seek"):
            source.seek(0)

    table = pacsv.read_csv(source, read_options=ro, parse_options=po)
    rewind()
    found = identifier_columns(source, table.schema, read_options=ro, parse_options=po)
    if found:
        names = list(found)
        rewind()
        text = pacsv.read_csv(
            source,
            read_options=ro,
            parse_options=po,
            convert_options=pacsv.ConvertOptions(
                include_columns=names, column_types=dict.fromkeys(names, pa.string())
            ),
        )
        for name in names:
            table = table.set_column(table.schema.get_field_index(name), name, text[name])
    return table
