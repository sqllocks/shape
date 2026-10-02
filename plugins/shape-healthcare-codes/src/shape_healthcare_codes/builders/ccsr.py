"""AHRQ CCSR for ICD-10-CM diagnoses (HCUP): ICD-10-CM code to clinical categories.

Source: the tool zip (``DXCCSR_v<year>-<n>.csv`` inside). Cells are quoted with apostrophes
and the file has 19 columns: code, description, default category and description for
inpatient and for outpatient, then up to six category / description pairs, then the rationale.
The output is long: ``code``, ``ccsr`` (``DIG001``), ``ccsr_desc``, ``default_ip``,
``default_op`` (true where the category is the default for the setting).
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

PAGE = "https://hcup-us.ahrq.gov/toolssoftware/ccsr/dxccsr.jsp"
URL = "https://hcup-us.ahrq.gov/toolssoftware/ccsr/DXCCSR-v2026-1.zip"
RELEASE = "CCSR v2026.1 (valid for ICD-10-CM codes through 2026-09-30)"


def _clean(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        s = s[1:-1]
    return s.strip()


def parse(csv_text: str) -> pa.Table:
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows or len(rows[0]) != 19 or "ICD-10-CM CODE" not in rows[0][0]:
        raise ValueError("unexpected CCSR header: the file layout changed")
    out: dict[str, list[Any]] = {k: [] for k in "code ccsr ccsr_desc default_ip default_op".split()}
    for n, r in enumerate(rows[1:], 2):
        if len(r) != 19:
            raise ValueError(f"CCSR row {n} has {len(r)} columns, expected 19")
        code, ip, op = _clean(r[0]).upper(), _clean(r[2]), _clean(r[4])
        for k in range(6, 18, 2):
            cat = _clean(r[k])
            if not cat:
                continue
            out["code"].append(code)
            out["ccsr"].append(cat)
            out["ccsr_desc"].append(_clean(r[k + 1]))
            out["default_ip"].append(cat == ip)
            out["default_op"].append(cat == op)
    return pa.table(
        out,
        schema=pa.schema(
            [
                ("code", pa.string()),
                ("ccsr", pa.string()),
                ("ccsr_desc", pa.string()),
                ("default_ip", pa.bool_()),
                ("default_op", pa.bool_()),
            ]
        ),
    )


def build(
    data_dir: Path | None = None,
    *,
    download_dir: Path | None = None,
    from_files: dict[str, Path] | None = None,
) -> tuple[pa.Table, dict[str, Any]]:
    from shape_healthcare_codes.fetch import download, verify

    path = (from_files or {}).get("zip") or download(URL, download_dir)
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        table = parse(z.read(name).decode("utf-8", errors="replace"))
    manifest = {
        "asset": "ccsr",
        "release": RELEASE,
        "source": PAGE,
        "sources": {path.name: verify(path, None)},
        "subset": False,
    }
    return table, manifest
