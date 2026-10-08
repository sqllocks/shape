"""CMS-HCC, ESRD and RxHCC coefficients from the CMS model software (see ``_cms_software``).

Each model package has a coefficient file (``C2824T2N.csv``, ``D2423T2M.csv``,
``R0827T11.csv``, ...) with the header ``Name, Coeff, Label`` and one row per coefficient. A name
is a segment prefix and a regression variable (``CNA_HCC17``, ``Rx_CE_LowAged_F70_74``). The
segments and their prefixes are read from the package's main macro, which computes one score
per segment::

    %&SCOREMAC(PVAR=SCORE_COMMUNITY_NA,  RLIST=&COMM_REGA, CPREF=CNA_);

so segment ``COMMUNITY_NA`` is every coefficient named ``CNA_<variable>``. The segment name is the
score variable as published, without ``SCORE_`` (and the leading ``_`` of the ESRD graft parts).
A coefficient whose name has no segment prefix (the ESRD transplant and graft adjustment factors)
has a null segment and its whole name as the variable. The payment year is the year of
``DATE_ASOF`` in the package's main program.

Output, in the published order: ``model``, ``segment``, ``variable``, ``coefficient`` (the
published text, never converted to a float), ``payment_years`` and ``software`` (the package id,
which tells apart packages that carry the same model, such as RxHCC V08's three).
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape_healthcare_codes.builders._cms_software import (
    PAGE,
    RELEASE,
    URL,
    Package,
    payment_year,
    read_software_zip,
)

FORMAT = "shape-hcc-coefficients"
VERSION = 1
SCHEMA = pa.schema(
    [
        ("model", pa.string()),
        ("segment", pa.string()),
        ("variable", pa.string()),
        ("coefficient", pa.string()),
        ("payment_years", pa.list_(pa.int16())),
        ("software", pa.string()),
    ],
    metadata={b"format": FORMAT.encode(), b"version": str(VERSION).encode()},
)
HEADER = ["Name", "Coeff", "Label"]
DECIMAL = re.compile(r"^-?(\d+\.?\d*|\.\d+)$")

_SCORE = re.compile(
    r"%&SCOREMAC\s*\(\s*PVAR\s*=\s*(\w+)\s*,\s*RLIST\s*=\s*&?\w+\s*,\s*CPREF\s*=\s*(\w+)\s*\)",
    re.I,
)


def parse_segments(text: str) -> list[tuple[str, str]]:
    """The ``(segment, prefix)`` pairs of a main macro, in the published order."""
    out: list[tuple[str, str]] = []
    for pvar, prefix in _SCORE.findall(text):
        name = re.sub(r"^_?SCORE_", "", pvar, flags=re.I)
        out.append((name, prefix))
    return out


def segments_of(pkg: Package) -> list[tuple[str, str]]:
    found = [(n, parse_segments(t)) for n, t in sorted(pkg.files.items()) if n.endswith(".TXT")]
    found = [(n, s) for n, s in found if s]
    if len(found) != 1:
        raise ValueError(
            f"{pkg.software}: expected one main macro with %&SCOREMAC(PVAR=..., CPREF=...) "
            f"calls, found {[n for n, _ in found]}: the file layout changed"
        )
    return found[0][1]


def parse(
    csv_text: str,
    segments: list[tuple[str, str]],
    *,
    model: str,
    software: str,
    payment_years: list[int],
) -> dict[str, list[Any]]:
    rows = list(csv.reader(io.StringIO(csv_text.lstrip("﻿"))))
    if not rows or [c.strip() for c in rows[0][:3]] != HEADER:
        got = [c.strip() for c in rows[0]] if rows else []
        raise ValueError(
            f"{software}: coefficient file header is {got}, expected {HEADER}: "
            "the file layout changed"
        )
    # longest prefix first: GNE_ and GNPN_ both start with G
    prefixes = sorted(segments, key=lambda s: -len(s[1]))
    out: dict[str, list[Any]] = {k: [] for k in SCHEMA.names}
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        name, value = r[0].strip(), (r[1].strip() if len(r) > 1 else "")
        if not DECIMAL.match(value):
            raise ValueError(f"{software}: {name} has coefficient {value!r}, not a decimal")
        segment: str | None = None
        variable = name
        for seg, prefix in prefixes:
            if name.startswith(prefix) and len(name) > len(prefix):
                segment, variable = seg, name[len(prefix) :]
                break
        out["model"].append(model)
        out["segment"].append(segment)
        out["variable"].append(variable)
        out["coefficient"].append(value)
        out["payment_years"].append(list(payment_years))
        out["software"].append(software)
    if not out["model"]:
        raise ValueError(f"{software}: the coefficient file has no rows")
    return out


def from_packages(packages: list[Package]) -> pa.Table:
    cols: dict[str, list[Any]] = {k: [] for k in SCHEMA.names}
    for pkg in packages:
        csvs = sorted(n for n in pkg.files if n.lower().endswith(".csv"))
        if len(csvs) != 1:
            raise ValueError(
                f"{pkg.software}: expected one coefficient .csv file, found {csvs}: "
                "the file layout changed"
            )
        part = parse(
            pkg.files[csvs[0]],
            segments_of(pkg),
            model=pkg.model,
            software=pkg.software,
            payment_years=[payment_year(pkg.files)],
        )
        for k in cols:
            cols[k].extend(part[k])
    return pa.table(cols, schema=SCHEMA)


def build(
    data_dir: Path | None = None,
    *,
    download_dir: Path | None = None,
    from_files: dict[str, Path] | None = None,
) -> tuple[pa.Table, dict[str, Any]]:
    from shape_healthcare_codes.fetch import download, verify

    path = (from_files or {}).get("zip") or download(URL, download_dir)
    table = from_packages(read_software_zip(path))
    manifest = {
        "asset": "hcc_coefficients",
        "format": FORMAT,
        "version": VERSION,
        "release": RELEASE,
        "source": PAGE,
        "sources": {path.name: verify(path, None)},
        "subset": False,
    }
    return table, manifest
