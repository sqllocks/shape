"""Landing layout: where one table's file for one business date goes.

A path template such as ``{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}`` is filled for a
table and a batch date, so a daily run writes exactly the files a source system would drop and a
backfill writes a range of them. The date is always given, never read from the clock, so the same
run is the same files.

Tokens: ``{table}``, ``{ext}``, ``{date}`` (``YYYY-MM-DD``), ``{yyyymmdd}``, ``{yyyy}``, ``{mm}``,
``{dd}``; for rolling writers also ``{part}`` (the file's number in its table, five digits) and
``{hhmmss}`` (the time the file was opened, UTC). A template is relative to the output directory
and cannot leave it.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import PurePosixPath

DEFAULT_TEMPLATE = "{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}"
TOKENS = ("table", "ext", "date", "yyyymmdd", "yyyy", "mm", "dd", "part", "hhmmss")
_TOKEN = re.compile(r"\{([^{}]*)\}")
_DATE_TOKENS = frozenset({"date", "yyyymmdd", "yyyy", "mm", "dd"})


def parse_date(text: str | dt.date) -> dt.date:
    """A batch date from ``YYYY-MM-DD`` text (a date or datetime is accepted as it is)."""
    if isinstance(text, dt.datetime):
        return text.date()
    if isinstance(text, dt.date):
        return text
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"a batch date is YYYY-MM-DD, got {text!r}") from None


def check_template(template: str) -> None:
    """Raise ``ValueError`` for an unknown token, an absolute path or a ``..`` segment."""
    for token in _TOKEN.findall(template):
        if token not in TOKENS:
            raise ValueError(
                f"unknown token {{{token}}} in path template {template!r}; "
                f"the tokens are {', '.join('{' + t + '}' for t in TOKENS)}"
            )
    if re.sub(_TOKEN, "", template).count("{") or re.sub(_TOKEN, "", template).count("}"):
        raise ValueError(f"unbalanced braces in path template {template!r}")
    parts = PurePosixPath(template.replace("\\", "/"))
    if parts.is_absolute() or re.match(r"^[A-Za-z]:", template) or ".." in parts.parts:
        raise ValueError(f"a path template is relative to the output directory: {template!r}")
    if "{table}" not in template:
        raise ValueError(f"a path template must contain {{table}}, got {template!r}")


def uses_date(template: str) -> bool:
    """True when ``template`` has a date token."""
    return any(t in _DATE_TOKENS for t in _TOKEN.findall(template))


def render_path(
    template: str,
    table: str,
    ext: str,
    batch_date: str | dt.date | None,
    *,
    part: int | None = None,
    now: dt.datetime | None = None,
) -> str:
    """``template`` for ``table`` with extension ``ext`` (no dot) on ``batch_date``.

    ``part`` fills ``{part}`` and ``now`` fills ``{hhmmss}``; a template using either needs it."""
    check_template(template)
    if "/" in table or "\\" in table or table in ("", ".", ".."):
        raise ValueError(f"table name {table!r} cannot be used in a path")
    values = {"table": table, "ext": ext}
    tokens = _TOKEN.findall(template)
    if "part" in tokens:
        if part is None:
            raise ValueError(f"path template {template!r} has {{part}}: it is for rolling files")
        values["part"] = f"{part:05d}"
    if "hhmmss" in tokens:
        if now is None:
            raise ValueError(f"path template {template!r} has {{hhmmss}}: it is for rolling files")
        values["hhmmss"] = now.strftime("%H%M%S")
    if uses_date(template):
        if batch_date is None:
            raise ValueError(
                f"path template {template!r} has a date token: give the batch date (YYYY-MM-DD)"
            )
        day = parse_date(batch_date)
        values.update(
            date=day.isoformat(),
            yyyymmdd=day.strftime("%Y%m%d"),
            yyyy=f"{day.year:04d}",
            mm=f"{day.month:02d}",
            dd=f"{day.day:02d}",
        )
    return _TOKEN.sub(lambda m: values[m.group(1)], template)


def parse_table_formats(items: list[str] | None) -> dict[str, str]:
    """``["orders=parquet", "customers=csv"]`` as ``{"orders": "parquet", "customers": "csv"}``."""
    formats: dict[str, str] = {}
    for item in items or []:
        table, sep, fmt = item.partition("=")
        if not sep or not table.strip() or not fmt.strip():
            raise ValueError(f"a per-table format is TABLE=FORMAT, got {item!r}")
        formats[table.strip()] = fmt.strip()
    return formats


def parse_pairs(items: list[str] | None, what: str) -> dict[str, int]:
    """``["customer=300", "order=4000"]`` as ``{"customer": 300, "order": 4000}``."""
    pairs: dict[str, int] = {}
    for item in items or []:
        name, sep, number = item.partition("=")
        try:
            value = int(number)
        except ValueError:
            value = -1
        if not sep or not name.strip() or value < 0:
            raise ValueError(f"{what} is TABLE=N with N a whole number, got {item!r}")
        pairs[name.strip()] = value
    return pairs


__all__ = [
    "DEFAULT_TEMPLATE",
    "TOKENS",
    "check_template",
    "parse_date",
    "parse_pairs",
    "parse_table_formats",
    "render_path",
    "uses_date",
]
