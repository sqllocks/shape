"""CMS place-of-service codes, from the table on the CMS "Place of Service Code Set" page.

The page holds the code table as HTML (code, name, description; the description ends with
"(Effective <date>)" notes). ``valid_from`` is the earliest effective date in the description,
null when none is given; a code CMS marks "Deleted" or "Retired" gets no end date here
``[VERIFY]``, the description is kept in ``long_desc``.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

URL = "https://www.cms.gov/medicare/coding-billing/place-of-service-codes/code-sets"
_MONTH_NAMES = (
    "January February March April May June July August September October November December"
)
_MONTHS = {m: i for i, m in enumerate(_MONTH_NAMES.split(), 1)}
_EFFECTIVE = re.compile(r"Effective\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})")


class _Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None
        self._row: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", html.unescape("".join(self._cell))).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _effective(desc: str) -> dt.date | None:
    days = []
    for mon, day, year in _EFFECTIVE.findall(desc):
        if mon in _MONTHS:
            days.append(dt.date(int(year), _MONTHS[mon], int(day)))
    return min(days) if days else None


def parse(page_html: str) -> pa.Table:
    p = _Rows()
    p.feed(page_html)
    cols: dict[str, list[Any]] = {
        k: [] for k in "code short_desc long_desc leaf valid_from valid_to".split()
    }
    seen: set[str] = set()
    for row in p.rows:
        if len(row) < 3 or not re.fullmatch(r"\d{2}(?:-\d{2})?", row[0]):
            continue
        codes = [row[0]]
        if "-" in row[0]:  # a range of unassigned codes: not codes
            continue
        for c in codes:
            if c in seen:
                continue
            seen.add(c)
            cols["code"].append(c)
            cols["short_desc"].append(row[1])
            cols["long_desc"].append(row[2])
            cols["leaf"].append(True)
            cols["valid_from"].append(_effective(row[2]))
            cols["valid_to"].append(None)
    if not cols["code"]:
        raise ValueError("no place-of-service rows found: the page layout changed")
    schema = pa.schema(
        [
            ("code", pa.string()),
            ("short_desc", pa.string()),
            ("long_desc", pa.string()),
            ("leaf", pa.bool_()),
            ("valid_from", pa.date32()),
            ("valid_to", pa.date32()),
        ]
    )
    return pa.table(cols, schema=schema).sort_by("code")


def build(
    data_dir: Path | None = None,
    *,
    download_dir: Path | None = None,
    from_files: dict[str, Path] | None = None,
) -> tuple[pa.Table, dict[str, Any]]:
    from shape_healthcare_codes.fetch import download, verify

    path = (from_files or {}).get("html") or download(URL, download_dir, name="cms_pos.html")
    table = parse(path.read_text(encoding="utf-8", errors="replace"))
    manifest = {
        "asset": "pos",
        "release": "CMS place of service code set page (database updated May 2, 2024)",
        "source": URL,
        "sources": {path.name: verify(path, None)},
        "subset": False,
    }
    return table, manifest
