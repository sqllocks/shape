"""``shape fabric check-answers``: results exported from a DAX client against ``answers.json``.

``RESULTS`` is a CSV or JSON export of the queries of ``queries.dax``: a folder with one file per
query (``q01.csv``, ``q02.json``, ...; the file name is the query id; other files are not results),
or one file that says which query each row belongs to (a ``query`` column or key, or a JSON object
that maps a query id to its rows). A file holding one query's rows needs no id when ``answers.json``
has one query only. A JSON file may also be the response of the Power BI ``executeQueries`` REST
call.

Columns are found by name, however the client writes it: a measure as ``[item.Total Price]`` or
``item.Total Price``, a grouping column as ``category[label]``, ``'category'[label]`` or
``[label]``. Other columns are ignored.

Counts, sums of integer and decimal columns, minimums and maximums are compared exactly. The
other values (averages, ratios, sums of float columns) are compared rounded half-even to the
answer's places, or to ``places`` when that is given: both sides are rounded, the expected value
from its exact fraction.

Exit codes: 0 every value matches; 1 a mismatch; 2 a file is malformed, a query has no result, or
a result is for a query the answers do not have.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any

from .known_answer import (
    MAX_PLACES,
    KnownAnswerError,
    key_form,
    round_half_even,
)

MAX_SHOWN = 50

Row = dict[str, Any]  # header -> text, Decimal or None

_HEADER = re.compile(r"\s*(?:'?(?P<table>.*?)'?)?\[(?P<name>.*)\]\s*\Z", re.S)


def split_header(header: str) -> tuple[str | None, str]:
    """A result header as ``(table, name)``: ``category[label]`` is ``("category", "label")``,
    ``[item.Total Price]`` is ``(None, "item.Total Price")`` and a bare word is ``(None, word)``."""
    m = _HEADER.fullmatch(header)
    if not m:
        return None, header.strip()
    table = (m.group("table") or "").replace("''", "'").strip()
    return (table or None), m.group("name").replace("]]", "]").strip()


# ---------------------------------------------------------------------------------------------
# reading the results


def _json_rows(value: Any, where: str) -> list[Row]:
    if isinstance(value, dict) and isinstance(value.get("results"), list):  # executeQueries
        try:
            value = value["results"][0]["tables"][0]["rows"]
        except (IndexError, KeyError, TypeError):
            raise KnownAnswerError(f"{where}: no rows in the response") from None
    if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
        raise KnownAnswerError(f"{where}: expected a list of rows (objects)")
    return [dict(r) for r in value]


def _csv_rows(path: Path) -> list[Row]:
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            table = list(csv.reader(fh))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise KnownAnswerError(f"{path}: not readable as CSV: {exc}") from exc
    if not table or not any(cell.strip() for cell in table[0]):
        raise KnownAnswerError(f"{path}: the file is empty (no header row)")
    head = table[0]
    for h in head:
        if head.count(h) > 1:
            raise KnownAnswerError(f"{path}: the column {h!r} appears twice")
    return [
        {h: (row[i] if i < len(row) else None) for i, h in enumerate(head)}
        for row in table[1:]
        if any(cell.strip() for cell in row)
    ]


def _file_rows(path: Path) -> Any:
    if path.suffix.lower() == ".csv":
        return _csv_rows(path)
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), parse_float=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise KnownAnswerError(f"{path}: not valid JSON: {exc}") from exc


def read_results(location: str | Path, query_ids: list[str]) -> dict[str, list[Row]]:
    """The rows of every query in ``location``: ``{query id: rows}``."""
    path = Path(location)
    if not path.exists():
        raise KnownAnswerError(f"the results {path} do not exist")
    found: dict[str, list[Row]] = {}
    if path.is_dir():
        for f in sorted(path.iterdir()):
            if (
                f.name.startswith(".")
                or not f.is_file()
                or f.suffix.lower() not in (".csv", ".json")
            ):
                continue
            if f.stem not in query_ids:
                raise KnownAnswerError(
                    f"{f.name}: a result for the query {f.stem!r}, which is not one of the "
                    f"answers' queries ({', '.join(query_ids)})"
                )
            content = _file_rows(f)
            found[f.stem] = content if f.suffix.lower() == ".csv" else _json_rows(content, f.name)
    else:
        if path.suffix.lower() not in (".csv", ".json"):
            raise KnownAnswerError(f"{path}: results are a .csv or .json file, or a folder of them")
        content = _file_rows(path)
        found = _split_single_file(path, content, query_ids)
    missing = [q for q in query_ids if q not in found]
    if missing:
        raise KnownAnswerError(f"no result for the query {', '.join(missing)}")
    return found


def _split_single_file(path: Path, content: Any, query_ids: list[str]) -> dict[str, list[Row]]:
    name = path.name
    if isinstance(content, dict) and "results" not in content:
        unknown = [k for k in content if k not in query_ids]
        if unknown:
            raise KnownAnswerError(
                f"{name}: a result for the query {unknown[0]!r}, which is not one of the "
                f"answers' queries ({', '.join(query_ids)})"
            )
        return {k: _json_rows(v, f"{name}[{k}]") for k, v in content.items()}
    rows = content if path.suffix.lower() == ".csv" else _json_rows(content, name)
    if any("query" in r for r in rows):
        out: dict[str, list[Row]] = {}
        for r in rows:
            qid = r.get("query")
            qid = None if qid is None else str(qid).strip()
            if qid not in query_ids:
                raise KnownAnswerError(
                    f"{name}: a row for the query {qid!r}, which is not one of the answers' "
                    f"queries ({', '.join(query_ids)})"
                )
            out.setdefault(qid, []).append({k: v for k, v in r.items() if k != "query"})
        return out
    if len(query_ids) != 1:
        raise KnownAnswerError(
            f"{name} holds one table but the answers have {len(query_ids)} queries: add a "
            "'query' column that names each row's query, or give a folder with one file per query"
        )
    return {query_ids[0]: rows}


# ---------------------------------------------------------------------------------------------
# comparing


@dataclass
class Report:
    mismatches: list[str]
    compared: int


def _number(value: Any, where: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise KnownAnswerError(f"{where}: {value!r} is not a number")
    if isinstance(value, (int, float, Decimal)):
        d = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    else:
        text = str(value).strip()
        if text == "":
            return None
        try:
            d = Decimal(text)
        except InvalidOperation:
            raise KnownAnswerError(f"{where}: {text!r} is not a number") from None
    if not d.is_finite():
        raise KnownAnswerError(f"{where}: {value!r} is not a finite number")
    return d


def _shown(value: Any) -> str:
    if value is None:
        return "(blank)"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value).strip()


def _find_column(headers: list[str], name: str, table: str | None, what: str, query: str) -> str:
    """The header that is ``name`` (of ``table`` when the header names one)."""
    hits = []
    for h in headers:
        t, n = split_header(h)
        if n == name and (table is None or t is None or t == table):
            hits.append(h)
    if not hits:
        raise KnownAnswerError(f"query {query}: the result has no column for {what}")
    if len(hits) > 1:
        raise KnownAnswerError(f"query {query}: the column for {what} appears twice ({hits})")
    return hits[0]


def _grid(rows: list[Row]) -> list[str]:
    headers: list[str] = []
    for r in rows:
        for h in r:
            if h not in headers:
                headers.append(h)
    return headers


def _round_decimal(value: Decimal, places: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 120
        return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def check(answers: dict[str, Any], results: dict[str, list[Row]], places: int | None) -> Report:
    """Compare every value of ``answers`` with ``results`` (see the module docstring)."""
    if places is not None and not 0 <= places <= MAX_PLACES:
        raise KnownAnswerError(f"--places must be from 0 to {MAX_PLACES}, got {places}")
    measures = {m["id"]: m for m in answers["measures"]}
    mismatches: list[str] = []
    compared = 0
    for q in answers["queries"]:
        qid = q["id"]
        rows = results[qid]
        headers = _grid(rows)
        if not rows:
            headers = []
        slice_kind = q.get("slice_type")
        slice_col = None
        if q["slice"]:
            table, _, column = q["slice"].partition(".")
            slice_col = _find_column(headers, column, table, q["slice"], qid) if rows else None
        cols: dict[str, str] = {}
        if rows:
            for mid in q["measures"]:
                cols[mid] = _find_column(headers, mid, None, mid, qid)
        observed: dict[tuple[str | None, ...], dict[str, Decimal | None]] = {}
        for n, row in enumerate(rows, 1):
            where = f"query {qid}, row {n}"
            key: tuple[str | None, ...] = ()
            if slice_col is not None:
                raw = row.get(slice_col)
                if isinstance(raw, Decimal):
                    raw = format(raw, "f")
                elif isinstance(raw, bool):
                    raw = "true" if raw else "false"
                key = (key_form(None if raw is None else str(raw), slice_kind or "string", where),)
            if key in observed:
                raise KnownAnswerError(f"query {qid}: the group {key[0]!r} appears twice")
            observed[key] = {
                mid: _number(row.get(col), f"{where}, {mid}") for mid, col in cols.items()
            }
        expected_keys = {
            tuple(key_form(k, slice_kind or "string", f"answers {qid}") for k in r["key"]): r
            for r in q["rows"]
        }
        label = q["slice"] or "grand total"
        for key in sorted(set(expected_keys) | set(observed), key=lambda k: tuple(map(str, k))):
            exp_row = expected_keys.get(key)
            obs_row = observed.get(key)
            where_key = (
                f"{label}"
                if not q["slice"]
                else f"{label}={'(blank)' if key[0] is None else key[0]}"
            )
            for mid in q["measures"]:
                m = measures[mid]
                exp_text = None if exp_row is None else exp_row["values"].get(mid)
                obs = None if obs_row is None else obs_row.get(mid)
                if exp_text is None and obs is None:
                    continue
                compared += 1
                if exp_text is None:
                    mismatches.append(
                        f"{qid} {where_key} {mid}: expected (none) observed {_shown(obs)}"
                    )
                    continue
                if obs is None:
                    seen = "(none)" if obs_row is None else "(blank)"
                    mismatches.append(
                        f"{qid} {where_key} {mid}: expected {exp_text} observed {seen}"
                    )
                    continue
                if m["exact"]:
                    same = Decimal(exp_text) == obs
                    shown_expected = exp_text
                else:
                    p = answers["places"] if places is None else places
                    fraction = (exp_row or {}).get("fractions", {}).get(mid)
                    if fraction is None:
                        value = Fraction(Decimal(exp_text))
                    else:
                        top, _, bottom = fraction.partition("/")
                        value = Fraction(int(top), int(bottom))
                    shown_expected = round_half_even(value, p)
                    same = Decimal(shown_expected) == _round_decimal(obs, p)
                if not same:
                    mismatches.append(
                        f"{qid} {where_key} {mid}: expected {shown_expected} observed {_shown(obs)}"
                    )
    return Report(mismatches, compared)


def render(report: Report) -> str:
    if not report.mismatches:
        return f"All {report.compared} values match the known answers."
    lines = list(report.mismatches[:MAX_SHOWN])
    total = len(report.mismatches)
    word = "mismatch" if total == 1 else "mismatches"
    rest = total - len(lines)
    tail = f"{total} {word} in {report.compared} values"
    if rest:
        tail += f" ({len(lines)} shown, {rest} more)"
    lines.append(tail)
    return "\n".join(lines)
