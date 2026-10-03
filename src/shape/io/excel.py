"""Excel source (``[excel]`` extra, openpyxl in read-only mode): one table per sheet.

``book.xlsx`` is a dataset with one table per visible sheet; ``book.xlsx#Sheet`` is that one sheet
(a hidden sheet is read only when it is named this way, or with ``include_hidden``). The first
non-empty row of a sheet is its header.

Cell types are kept: a cell stored as text stays text (so a ZIP code, an NDC or a member id keeps
its leading zeros), numbers, dates, booleans and blanks come through as their Excel types, and a
column that mixes text with other types becomes text. Cached formula results are read, not the
formulas.

Reading also records what a reader of the workbook would want to know, as *findings* (plain
dicts with ``kind``, ``table``, ``count``, cell positions and examples): numbers and dates stored
as text, hidden columns and sheets, error cells, duplicate or blank headers (renamed
deterministically) and sentinel values.
"""

from __future__ import annotations

import datetime as dt
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

XLSX_SUFFIXES = (".xlsx", ".xlsm")
MAX_CELLS = 10  # cell positions kept per finding
MAX_EXAMPLES = 5  # example values kept per finding
ERROR_CODES = ("#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!")
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .xls, and the wrapper of an encrypted .xlsx

# Placeholder values that stand for "no value" (reported, never removed).
SENTINEL_NUMBERS = (99999, 999999, 9999999, 99999999, -9999, -99999)
SENTINEL_TEXT = {
    "00000": "zero-filled",
    "0000": "zero-filled",
    "000000": "zero-filled",
    "99999": "nines",
    "999999": "nines",
    "9999999": "nines",
    "99999999": "nines",
    "n/a": "not available",
    "na": "not available",
    "null": "null",
    "none": "null",
    "nil": "null",
    "tbd": "to be decided",
    "unknown": "unknown",
    "missing": "missing",
    "-": "dash",
    "--": "dash",
    "?": "question mark",
}
SENTINEL_DATES = (
    dt.date(9999, 12, 31),
    dt.date(1900, 1, 1),
    dt.date(1899, 12, 30),
    dt.date(1, 1, 1),
)

_NUMBER_TEXT = re.compile(
    r"^\s*[-+]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?([eE][-+]?\d+)?\s*$|^\s*[-+]?\.\d+\s*$"
)
_DATE_TEXT = (
    (re.compile(r"^\d{4}-\d{1,2}-\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$"), ("%Y-%m-%d",)),
    (re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$"), ("%Y/%m/%d",)),
    (re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"), ("%m/%d/%Y", "%d/%m/%Y")),
    (re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$"), ("%d.%m.%Y",)),
    (re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{2,4}$"), ("%d-%b-%Y", "%d-%b-%y")),
    (re.compile(r"^[A-Za-z]{3,9} \d{1,2}, \d{4}$"), ("%B %d, %Y", "%b %d, %Y")),
)


class WorkbookError(ValueError):
    """The file is not a readable ``.xlsx`` workbook (legacy format, password, damaged)."""


def is_workbook_path(path: str | Path) -> bool:
    """True for a path whose suffix is ``.xlsx`` or ``.xlsm`` (or the legacy ``.xls`` and
    ``.xlsb``, which are routed here so that they get a clear error)."""
    return Path(path).suffix.lower() in (*XLSX_SUFFIXES, ".xls", ".xlsb")


def split_spec(spec: str | Path) -> tuple[str, str | None]:
    """``"book.xlsx#Sheet"`` -> ``("book.xlsx", "Sheet")``; ``"book.xlsx"`` -> ``("book.xlsx",
    None)``. A ``#`` after anything but a workbook suffix is not a sheet selector."""
    text = str(spec)
    head, sep, tail = text.rpartition("#")
    if sep and tail and is_workbook_path(head):
        return head, tail
    return text, None


def is_workbook_spec(spec: Any) -> bool:
    """True for a string or path that names a workbook, with or without a ``#Sheet`` part."""
    return isinstance(spec, (str, Path)) and is_workbook_path(split_spec(spec)[0])


def _import_openpyxl() -> Any:
    try:
        import openpyxl
    except ImportError as exc:
        raise ImportError(
            "reading Excel needs openpyxl: pip install 'sqllocks-shape[excel]'"
        ) from exc
    return openpyxl


def check_workbook_file(path: str | Path) -> Path:
    """The workbook path, or a :class:`WorkbookError` that says what is wrong with the file."""
    p = Path(path)
    if p.suffix.lower() == ".xls":
        raise WorkbookError(
            f"{p.name} is a legacy .xls workbook, which Shape does not read: open it in Excel "
            "and save it as .xlsx"
        )
    if p.suffix.lower() == ".xlsb":
        raise WorkbookError(
            f"{p.name} is a binary .xlsb workbook, which Shape does not read: save it as .xlsx"
        )
    if not p.is_file():
        raise FileNotFoundError(f"source not found: {p}")
    with open(p, "rb") as fh:
        head = fh.read(8)
    if head == _OLE2:
        raise WorkbookError(
            f"{p.name} is password-protected (or a legacy workbook with an .xlsx name): remove "
            "the password in Excel (File > Info > Protect Workbook) and save it again"
        )
    if not zipfile.is_zipfile(p):
        raise WorkbookError(f"{p.name} is not a valid .xlsx workbook")
    _refuse_zip_bomb(p)
    return p


MAX_EXPANSION = 1000  # an archive member that inflates more than this many times is refused
MAX_PLAIN_BYTES = 256 << 20  # ... once it is also larger than this


def _refuse_zip_bomb(p: Path) -> None:
    try:
        with zipfile.ZipFile(p) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise WorkbookError(f"{p.name} is not a valid .xlsx workbook: {exc}") from exc
    for info in infos:
        if info.file_size > MAX_PLAIN_BYTES and info.file_size > MAX_EXPANSION * max(
            info.compress_size, 1
        ):
            raise WorkbookError(
                f"{p.name} is refused: {info.filename} inflates from {info.compress_size:,} to "
                f"{info.file_size:,} bytes, which is not a spreadsheet"
            )


@dataclass(frozen=True)
class SheetInfo:
    name: str
    state: str  # "visible", "hidden" or "veryHidden"


def sheet_infos(path: str | Path) -> list[SheetInfo]:
    """The sheets of a workbook in workbook order, with their visibility."""
    p = check_workbook_file(path)
    openpyxl = _import_openpyxl()
    wb = _open(openpyxl, p)
    try:
        return [SheetInfo(ws.title, ws.sheet_state) for ws in wb.worksheets]
    finally:
        wb.close()


def _open(openpyxl: Any, p: Path) -> Any:
    try:
        return openpyxl.load_workbook(p, read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise WorkbookError(f"{p.name} cannot be read as an .xlsx workbook: {exc}") from exc


@dataclass
class SheetRead:
    """One sheet as an Arrow table, with the findings its cells and header gave."""

    name: str
    table: pa.Table
    findings: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WorkbookRead:
    """The sheets read from a workbook (in workbook order) and the findings about the workbook
    itself (hidden sheets)."""

    path: str
    sheets: dict[str, SheetRead]
    findings: list[dict[str, Any]] = field(default_factory=list)


def _letters(index: int) -> str:
    """1 -> ``A``, 27 -> ``AA``."""
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _hidden_columns(path: Path, sheet: str) -> set[int]:
    """1-based indexes of the hidden columns of one sheet (read from the sheet's ``<cols>``)."""
    from openpyxl.xml.functions import iterparse

    try:
        with zipfile.ZipFile(path) as zf:
            target = _sheet_part(zf, sheet)
            if target is None:
                return set()
            hidden: set[int] = set()
            with zf.open(target) as fh:
                for _, el in iterparse(fh, events=("end",)):
                    tag = el.tag.rsplit("}", 1)[-1]
                    if tag == "col" and el.get("hidden") in ("1", "true"):
                        lo, hi = int(el.get("min", "0")), int(el.get("max", "0"))
                        hidden.update(range(lo, min(hi, lo + 16384) + 1))
                    elif tag == "cols" or tag == "sheetData":
                        break
            return hidden
    except (zipfile.BadZipFile, KeyError, ValueError):
        return set()


def _sheet_part(zf: zipfile.ZipFile, sheet: str) -> str | None:
    from openpyxl.xml.functions import fromstring

    book = fromstring(zf.read("xl/workbook.xml"))
    rid = None
    for el in book.iter():
        if el.tag.rsplit("}", 1)[-1] == "sheet" and el.get("name") == sheet:
            rid = next((v for k, v in el.attrib.items() if k.endswith("}id")), None)
    if rid is None:
        return None
    rels = fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    for el in rels:
        if el.get("Id") == rid:
            target = el.get("Target", "")
            return target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    return None


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value == "")


def _unique_headers(raw: list[Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """Header names: text as written, numbers as text, blanks ``column_N``, repeats ``name_2``,
    ``name_3`` ... (bumped past any name already taken). Returns the names and their findings."""
    names: list[str] = []
    notes: list[dict[str, Any]] = []
    seen: set[str] = set()
    base_taken = {str(v) for v in raw if not _is_blank(v)}
    for i, value in enumerate(raw, 1):
        cell = f"{_letters(i)}1"
        if _is_blank(value):
            name = f"column_{i}"
            while name in seen or name in base_taken:
                name += "_"
            notes.append({"kind": "blank_header", "column": name, "cell": cell})
        else:
            name = str(value)
            if name in seen:
                n = 2
                while f"{name}_{n}" in seen or f"{name}_{n}" in base_taken:
                    n += 1
                notes.append(
                    {
                        "kind": "duplicate_header",
                        "column": f"{name}_{n}",
                        "original": name,
                        "cell": cell,
                    }
                )
                name = f"{name}_{n}"
        seen.add(name)
        names.append(name)
    return names, notes


def read_sheet(path: str | Path, sheet: str) -> SheetRead:
    """Read one sheet (hidden or not) into a :class:`SheetRead`."""
    p = check_workbook_file(path)
    openpyxl = _import_openpyxl()
    wb = _open(openpyxl, p)
    try:
        if sheet not in wb.sheetnames:
            raise WorkbookError(f"{p.name} has no sheet {sheet!r}; sheets: {wb.sheetnames}")
        return _read_ws(wb[sheet], p, sheet)
    finally:
        wb.close()


def _read_ws(ws: Any, path: Path, sheet: str) -> SheetRead:
    errors: dict[int, list[tuple[int, str]]] = {}  # column index -> [(row, error code)]
    header: list[Any] | None = None
    header_row = 0
    rows: list[list[Any]] = []
    for r, row in enumerate(ws.iter_rows(), 1):
        if header is None:
            first = [c.value for c in row]
            if all(_is_blank(v) for v in first):
                continue
            header, header_row = first, r
            while header and _is_blank(header[-1]):
                header.pop()
            continue
        record: list[Any] = []
        for ci, c in enumerate(row):
            if c.data_type == "e":
                errors.setdefault(ci, []).append((r, str(c.value)))
                record.append(None)
            else:
                record.append(None if _is_blank(c.value) else c.value)
        rows.append(record)
    width = len(header) if header is not None else 0
    for record in rows:  # data beyond the header's width is kept under a generated name
        while len(record) > width and record[-1] is None:
            record.pop()
        width = max(width, len(record))
    if header is not None:
        header = header + [None] * (width - len(header))
    columns = [[rec[i] if i < len(rec) else None for rec in rows] for i in range(width)]
    findings: list[dict[str, Any]] = []
    if header is None:
        return SheetRead(sheet, pa.table({}), findings)
    names, notes = _unique_headers(header)
    for note in notes:
        findings.append({**note, "table": sheet})
    hidden = _hidden_columns(path, sheet)
    for ci, name in enumerate(names):
        if ci + 1 in hidden:
            findings.append(
                {
                    "kind": "hidden_column",
                    "table": sheet,
                    "column": name,
                    "cell": f"{_letters(ci + 1)}{header_row}",
                    "column_letter": _letters(ci + 1),
                }
            )
    arrays = []
    for ci, (name, values) in enumerate(zip(names, columns, strict=True)):
        findings.extend(_column_findings(sheet, name, ci, values, header_row, errors.get(ci, [])))
        arrays.append(_to_arrow(values))
    table = pa.table(dict(zip(names, arrays, strict=True))) if names else pa.table({})
    return SheetRead(sheet, table, findings)


def _cell(col: int, row: int) -> str:
    return f"{_letters(col + 1)}{row}"


def _kind_of(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, dt.datetime):
        return "datetime"
    if isinstance(value, dt.date):
        return "date"
    if isinstance(value, dt.time):
        return "time"
    if isinstance(value, dt.timedelta):
        return "duration"
    return "other"


def _is_number_text(text: str) -> bool:
    return bool(_NUMBER_TEXT.match(text))


def _is_date_text(text: str) -> bool:
    t = text.strip()
    for pattern, formats in _DATE_TEXT:
        if pattern.match(t):
            core = t.split("T")[0].split(" ")[0] if pattern.pattern.startswith("^\\d{4}-") else t
            for fmt in formats:
                try:
                    dt.datetime.strptime(core, fmt)
                except ValueError:
                    continue
                return True
    return False


def _date_of(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return None


def _finding(
    kind: str,
    sheet: str,
    column: str,
    hits: list[tuple[int, Any]],
    nonblank: int,
    col: int,
    **extra: Any,
) -> dict[str, Any]:
    """``hits`` are ``(row, value)`` pairs in row order."""
    return {
        "kind": kind,
        "table": sheet,
        "column": column,
        "count": len(hits),
        "share": round(len(hits) / nonblank, 6) if nonblank else 0.0,
        "cells": [_cell(col, r) for r, _ in hits[:MAX_CELLS]],
        "examples": [_example(v) for _, v in hits[:MAX_EXAMPLES]],
        **extra,
    }


def _example(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    return value


def _column_findings(
    sheet: str,
    name: str,
    ci: int,
    values: list[Any],
    header_row: int,
    errors: list[tuple[int, str]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    nonblank = sum(v is not None for v in values) + len(errors)
    if errors:
        by_error = Counter(code for _, code in errors)
        out.append(
            {
                "kind": "error_cells",
                "table": sheet,
                "column": name,
                "count": len(errors),
                "share": round(len(errors) / nonblank, 6),
                "cells": [_cell(ci, r) for r, _ in errors[:MAX_CELLS]],
                "examples": [code for _, code in errors[:MAX_EXAMPLES]],
                "by_error": dict(sorted(by_error.items())),
            }
        )
    numbers: list[tuple[int, Any]] = []
    dates: list[tuple[int, Any]] = []
    sentinels: dict[str, list[tuple[int, Any]]] = {}
    kinds: Counter[str] = Counter()
    for i, v in enumerate(values):
        if v is None:
            continue
        row = header_row + 1 + i
        kind = _kind_of(v)
        kinds[kind] += 1
        if kind == "str":
            if _is_number_text(v):
                numbers.append((row, v))
            elif _is_date_text(v):
                dates.append((row, v))
            label = SENTINEL_TEXT.get(v.strip().lower())
            if label is not None:
                sentinels.setdefault(v.strip(), []).append((row, v))
            elif (parsed := _text_sentinel_date(v)) is not None:
                sentinels.setdefault(parsed, []).append((row, v))
        elif kind in ("int", "float") and v in SENTINEL_NUMBERS:
            sentinels.setdefault(str(v), []).append((row, v))
        elif kind in ("datetime", "date") and _date_of(v) in SENTINEL_DATES:
            sentinels.setdefault(str(_date_of(v)), []).append((row, v))
    total = sum(kinds.values()) + len(errors)
    if numbers:
        zeros = sum(
            1
            for _, t in numbers
            if len(t.strip()) > 1 and t.strip()[0] == "0" and not t.strip().startswith("0.")
        )
        out.append(
            _finding(
                "numbers_stored_as_text",
                sheet,
                name,
                numbers,
                total,
                ci,
                leading_zeros=zeros,
                kept_as_text=True,
            )
        )
    if dates:
        out.append(
            _finding("dates_stored_as_text", sheet, name, dates, total, ci, kept_as_text=True)
        )
    for token in sorted(sentinels):
        out.append(
            _finding("sentinel_values", sheet, name, sentinels[token], total, ci, value=token)
        )
    if len(kinds) > 1 and "str" in kinds:
        out.append(
            {
                "kind": "mixed_types",
                "table": sheet,
                "column": name,
                "count": total - kinds["str"],
                "share": round((total - kinds["str"]) / total, 6) if total else 0.0,
                "types": dict(sorted(kinds.items())),
                "stored_as": "string",
            }
        )
    return out


def _text_sentinel_date(text: str) -> str | None:
    t = text.strip()
    for d in SENTINEL_DATES:
        if t.startswith(d.isoformat()):
            return str(d)
    return None


def _to_arrow(values: list[Any]) -> Any:
    kinds = {_kind_of(v) for v in values if v is not None}
    if not kinds:
        return pa.array(values, type=pa.string())
    if kinds == {"str"}:
        return pa.array(values, type=pa.string())
    if kinds == {"bool"}:
        return pa.array(values, type=pa.bool_())
    if kinds == {"int"}:
        if all(v is None or -(2**63) <= v < 2**63 for v in values):
            return pa.array(values, type=pa.int64())
        return pa.array([None if v is None else float(v) for v in values], type=pa.float64())
    if kinds <= {"int", "float"}:
        return pa.array([None if v is None else float(v) for v in values], type=pa.float64())
    if kinds <= {"datetime", "date"}:
        if all(
            v is None or not isinstance(v, dt.datetime) or v.time() == dt.time(0) for v in values
        ):
            return pa.array([_date_of(v) for v in values], type=pa.date32())
        return pa.array([None if v is None else _naive(v) for v in values], type=pa.timestamp("us"))
    if kinds == {"time"}:
        return pa.array(values, type=pa.time64("us"))
    if kinds == {"duration"}:
        return pa.array(values, type=pa.duration("us"))
    return pa.array([None if v is None else _text(v) for v in values], type=pa.string())


def _naive(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    return dt.datetime.combine(value, dt.time(0))


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return str(value)


def read_workbook(
    path: str | Path, sheet: str | None = None, *, include_hidden: bool = False
) -> WorkbookRead:
    """Read a workbook: every visible sheet, or the named ``sheet`` (hidden or not). Hidden
    sheets are reported, and read only with ``include_hidden`` or when named."""
    p = check_workbook_file(path)
    infos = sheet_infos(p)
    names = [i.name for i in infos]
    if sheet is not None and sheet not in names:
        raise WorkbookError(f"{p.name} has no sheet {sheet!r}; sheets: {names}")
    findings = [
        {
            "kind": "hidden_sheet",
            "table": i.name,
            "sheet": i.name,
            "state": i.state,
            "read": sheet == i.name or include_hidden,
        }
        for i in infos
        if i.state != "visible"
    ]
    wanted = (
        [sheet]
        if sheet is not None
        else [i.name for i in infos if i.state == "visible" or include_hidden]
    )
    if not wanted:
        raise WorkbookError(f"{p.name} has no visible sheets")
    sheets = {name: read_sheet(p, name) for name in wanted}
    return WorkbookRead(str(p), sheets, findings)


def workbook_sheet_names(path: str | Path, *, include_hidden: bool = False) -> list[str]:
    """The visible sheets (and the hidden ones with ``include_hidden``), in workbook order."""
    return [i.name for i in sheet_infos(path) if i.state == "visible" or include_hidden]
