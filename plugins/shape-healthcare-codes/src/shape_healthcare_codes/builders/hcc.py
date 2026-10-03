"""CMS-HCC, ESRD and RxHCC mappings from CMS ("<year> Model Software/ICD-10 Mappings").

The mapping zip holds one CSV: a title block, then a header row whose cells name each model
column ("CMS-HCC Model Category V28") and, from the second block, a payment-year flag column
("... V28 for 2026 Payment Year", Yes or No), then one row per ICD-10-CM code. The output is
long: ``code``, ``model`` (``CMS-HCC V28``, ``ESRD V24``, ``RxHCC V08``), ``hcc`` (the category
number), and ``payment_years`` (the payment years in which CMS counts the mapping, per the flag
columns; empty when the file has none).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

PAGE = (
    "https://www.cms.gov/medicare/payment/medicare-advantage-rates-statistics/risk-adjustment/"
    "2027-model-software-icd-10-mappings"
)
URL = "https://www.cms.gov/files/zip/2027-initial-icd-10-cm-mappings.zip"
RELEASE = "2027 Initial ICD-10-CM Mappings (file of 2026-05-13)"
_CAT = re.compile(r"(ESRD|CMS-HCC|RxHCC).*?\bV(\d+)\b(?!.*Payment Year)", re.S)
_FLAG = re.compile(r"(ESRD|CMS-HCC|RxHCC).*?\bV(\d+)\b.*?(\d{4}) Payment Year", re.S)


def _model_name(header: str) -> str | None:
    flat = re.sub(r"\s+", " ", header)
    m = _CAT.search(flat)
    if not m:
        return None
    kind = m.group(1)
    if kind == "CMS-HCC" and "ESRD" in flat:
        kind = "ESRD"
    return f"{kind} V{m.group(2)}"


def parse(csv_text: str) -> pa.Table:
    rows = list(csv.reader(io.StringIO(csv_text)))
    hi = next(
        (
            i
            for i, r in enumerate(rows)
            if r and r[0].replace("\n", " ").strip() == "Diagnosis Code"
        ),
        None,
    )
    if hi is None:
        raise ValueError("no 'Diagnosis Code' header row: the file layout changed")
    header = rows[hi]
    cat_cols: dict[int, str] = {}
    flag_cols: dict[int, tuple[str, int]] = {}
    for i, h in enumerate(header[2:], 2):
        flat = re.sub(r"\s+", " ", h)
        fm = _FLAG.search(flat)
        if fm:
            kind = "ESRD" if "ESRD" in flat else fm.group(1)
            flag_cols[i] = (f"{kind} V{fm.group(2)}", int(fm.group(3)))
            continue
        name = _model_name(h)
        if name:
            cat_cols[i] = name
    if not cat_cols:
        raise ValueError("no model columns found in the header row")
    out: dict[str, list[Any]] = {"code": [], "model": [], "hcc": [], "payment_years": []}
    for r in rows[hi + 1 :]:
        if not r or not re.fullmatch(r"[A-Z][0-9A-Z]{2,6}", r[0].strip()):
            continue
        code = r[0].strip().upper()
        for i, model in cat_cols.items():
            val = r[i].strip() if i < len(r) else ""
            if not val:
                continue
            years = sorted(
                y
                for j, (m2, y) in flag_cols.items()
                if m2 == model and j < len(r) and r[j].strip().lower() == "yes"
            )
            out["code"].append(code)
            out["model"].append(model)
            out["hcc"].append(val)
            out["payment_years"].append(years)
    schema = pa.schema(
        [
            ("code", pa.string()),
            ("model", pa.string()),
            ("hcc", pa.string()),
            ("payment_years", pa.list_(pa.int16())),
        ]
    )
    return pa.table(out, schema=schema)


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
        "asset": "hcc",
        "release": RELEASE,
        "source": PAGE,
        "sources": {path.name: verify(path, None)},
        "subset": False,
    }
    return table, manifest
