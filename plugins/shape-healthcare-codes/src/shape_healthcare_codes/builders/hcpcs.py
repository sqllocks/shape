"""HCPCS Level II from CMS: the alpha-numeric quarterly file, codes and modifiers.

Fixed-width record layout (``HCPC<year>_recordlayout.txt`` in the zip): code 1-5, modifier 4-5,
sequence 6-10, record id 11 (3 = code, 4 = code continuation, 7 = modifier, 8 = modifier
continuation), long description 12-91 (continued on the following records of the same code),
short description 92-119, pricing indicator 120-121, coverage code 230, BETOS 257-259, added
date 269-276, action effective date 277-284, termination date 285-292, action code 293.

**CPT is never read.** The zip's record layout says CPT-4 codes and descriptors are copyrighted
by the AMA. The file this builder reads carries Level II only (codes starting with a letter);
the builder refuses a code record whose code starts with a digit, rather than store it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

QUARTERLY_PAGE = "https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update"
URL = "https://www.cms.gov/files/zip/october-2026-alpha-numeric-hcpcs-file.zip"
RELEASE = "October 2026 (file of 2026-09-23)"

MEMBER = r"^HCPC\d{4}_[A-Z]{3}_ANWEB_\d+\.txt$"


class CptRecordError(ValueError):
    """A record whose code is a CPT code (AMA-copyrighted) was found in the input."""


def _date(s: str) -> dt.date | None:
    s = s.strip()
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])) if len(s) == 8 and s.isdigit() else None


def parse(text: str) -> pa.Table:
    """Parse the fixed-width file into one row per Level II code or modifier."""
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if len(line) < 12:
            raise ValueError(f"HCPCS line {n} is too short for the record layout")
        rid = line[10]
        if rid in "37" and len(line) < 293:
            raise ValueError(f"HCPCS line {n} is {len(line)} characters, not the record layout")
        if rid in "34":
            kind, code = "code", line[:5].strip().upper()
            if not code[:1].isalpha():
                raise CptRecordError(f"line {n}: code {code!r} is not a Level II code")
        elif rid in "78":
            kind, code = "modifier", line[3:5].strip().upper()
            if not code[:1].isalpha():
                raise CptRecordError(f"line {n}: modifier {code!r} is not a Level II modifier")
        else:
            raise ValueError(f"HCPCS line {n}: unknown record id {rid!r}")
        key = (kind, code)
        if rid in "37":
            if key in recs:
                raise ValueError(f"HCPCS line {n}: duplicate {kind} {code}")
            recs[key] = {
                "code": code,
                "kind": kind,
                "long": line[11:91].strip(),
                "short": line[91:119].strip(),
                "pricing": line[119:121].strip() or None,
                "coverage": line[229:230].strip() or None,
                "betos": line[256:259].strip() or None,
                "added": _date(line[268:276]),
                "effective": _date(line[276:284]),
                "end": _date(line[284:292]),
                "action": line[292:293].strip() or None,
            }
            order.append(key)
        else:
            if key not in recs:
                raise ValueError(f"HCPCS line {n}: continuation of unknown {kind} {code}")
            recs[key]["long"] = f"{recs[key]['long']} {line[11:91].strip()}".strip()
    cols: dict[str, list[Any]] = {
        k: []
        for k in (
            "code short_desc long_desc leaf valid_from valid_to kind betos coverage_code "
            "pricing_indicator action_code"
        ).split()
    }
    for key in sorted(order):
        r = recs[key]
        cols["code"].append(r["code"])
        cols["short_desc"].append(r["short"])
        cols["long_desc"].append(r["long"])
        cols["leaf"].append(True)
        cols["valid_from"].append(r["added"] or r["effective"])
        cols["valid_to"].append(r["end"])
        cols["kind"].append(r["kind"])
        cols["betos"].append(r["betos"])
        cols["coverage_code"].append(r["coverage"])
        cols["pricing_indicator"].append(r["pricing"])
        cols["action_code"].append(r["action"])
    schema = pa.schema(
        [
            ("code", pa.string()),
            ("short_desc", pa.string()),
            ("long_desc", pa.string()),
            ("leaf", pa.bool_()),
            ("valid_from", pa.date32()),
            ("valid_to", pa.date32()),
            ("kind", pa.string()),
            ("betos", pa.string()),
            ("coverage_code", pa.string()),
            ("pricing_indicator", pa.string()),
            ("action_code", pa.string()),
        ]
    )
    return pa.table(cols, schema=schema)


def build(
    data_dir: Path | None = None,
    *,
    download_dir: Path | None = None,
    from_files: dict[str, Path] | None = None,
) -> tuple[pa.Table, dict[str, Any]]:
    import re
    import zipfile

    from shape_healthcare_codes.fetch import download, verify

    path = (from_files or {}).get("zip") or download(URL, download_dir)
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if re.match(MEMBER, n.rsplit("/", 1)[-1]))
        table = parse(z.read(name).decode("latin-1"))
    manifest = {
        "asset": "hcpcs2",
        "release": RELEASE,
        "source": QUARTERLY_PAGE,
        "sources": {path.name: verify(path, None)},
        "subset": False,
    }
    return table, manifest
