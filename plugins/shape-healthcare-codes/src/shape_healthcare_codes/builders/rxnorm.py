"""RxNorm current prescribable content (NLM): drug concepts and their NDCs.

Source: ``RxNorm_full_prescribe_<MMDDYYYY>.zip``, which NLM marks "no license required" and
publishes with an MD5 (pinned here). Only ``RXNCONSO.RRF`` and ``RXNSAT.RRF`` are read (the
relationships file, RXNREL, is not used), streamed from the zip.

Two tables: ``rxnorm`` (``code`` is the RxCUI; ``tty`` the term type: IN ingredient, PIN
precise ingredient, MIN multiple ingredient, BN brand name, SCD semantic clinical drug, SBD
semantic branded drug, GPCK generic pack, BPCK branded pack; ``leaf`` is true for the four
orderable types SCD, SBD, GPCK, BPCK) and ``rxnorm_ndc`` (RxCUI to 11-digit NDC, from the
RXNORM-sourced ``NDC`` attributes).

NLM's terms (https://www.nlm.nih.gov/research/umls/rxnorm/docs/termsofservice.html) ask that a
product using RxNorm carry the NLM statement, and that a redistributor keep the data current or
say it is not the most current: ``THIRD_PARTY_NOTICES.md`` carries the statement, and the
manifest records the release date.
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

PAGE = "https://www.nlm.nih.gov/research/umls/rxnorm/docs/rxnormfiles.html"
RELEASE_DATE = dt.date(2026, 9, 8)
URL = "https://download.nlm.nih.gov/rxnorm/RxNorm_full_prescribe_09082026.zip"
PIN = "md5:88bbe4cefabd8e71f58651c1c3188646"  # the MD5 NLM publishes on PAGE
TTYS = ("IN", "PIN", "MIN", "BN", "SCD", "SBD", "GPCK", "BPCK")
ORDERABLE = ("SCD", "SBD", "GPCK", "BPCK")


def _lines(z: zipfile.ZipFile, suffix: str) -> Iterable[str]:
    name = next(n for n in z.namelist() if n.endswith(suffix))
    with z.open(name) as raw:
        yield from io.TextIOWrapper(raw, encoding="utf-8", newline="")


def parse_conso(lines: Iterable[str]) -> pa.Table:
    """RXNCONSO rows (RXNORM source, the TTYs above) as the ``rxnorm`` table."""
    cols: dict[str, list[Any]] = {
        k: [] for k in "code short_desc long_desc leaf valid_from valid_to tty".split()
    }
    seen: set[str] = set()
    for line in lines:
        f = line.rstrip("\r\n").split("|")
        if len(f) < 17 or f[11] != "RXNORM" or f[12] not in TTYS or f[16] == "O":
            continue
        key = f"{f[0]}|{f[12]}"
        if key in seen:
            continue
        seen.add(key)
        cols["code"].append(f[0])
        cols["short_desc"].append(f[14][:120])
        cols["long_desc"].append(f[14])
        cols["leaf"].append(f[12] in ORDERABLE)
        cols["valid_from"].append(None)
        cols["valid_to"].append(None)
        cols["tty"].append(f[12])
    schema = pa.schema(
        [
            ("code", pa.string()),
            ("short_desc", pa.string()),
            ("long_desc", pa.string()),
            ("leaf", pa.bool_()),
            ("valid_from", pa.date32()),
            ("valid_to", pa.date32()),
            ("tty", pa.string()),
        ]
    )
    return pa.table(cols, schema=schema)


def parse_ndc(lines: Iterable[str]) -> pa.Table:
    """RXNSAT ``NDC`` attributes from the RXNORM source as ``rxcui`` to ``ndc11``."""
    pairs: set[tuple[str, str]] = set()
    for line in lines:
        if "|NDC|RXNORM|" not in line:
            continue
        f = line.rstrip("\r\n").split("|")
        if len(f) > 10 and f[8] == "NDC" and f[9] == "RXNORM" and len(f[10]) == 11:
            if f[10].isdigit():
                pairs.add((f[0], f[10]))
    rows = sorted(pairs, key=lambda p: (p[1], p[0]))
    return pa.table(
        {"rxcui": [p[0] for p in rows], "ndc11": [p[1] for p in rows]},
        schema=pa.schema([("rxcui", pa.string()), ("ndc11", pa.string())]),
    )


def md5_match(path: Path) -> bool:
    """True when ``path`` is the release whose MD5 NLM published (a file the user supplied may
    be another release: it is built, and the manifest says so)."""
    from shape_healthcare_codes.fetch import digest

    return digest(path, "md5") == PIN.partition(":")[2]


def build(
    data_dir: Path | None = None,
    *,
    download_dir: Path | None = None,
    from_files: dict[str, Path] | None = None,
) -> tuple[dict[str, pa.Table], dict[str, Any]]:
    from shape_healthcare_codes.fetch import download, verify

    supplied = (from_files or {}).get("zip")
    path = supplied or download(URL, download_dir, pin=PIN)
    with zipfile.ZipFile(path) as z:
        conso = parse_conso(_lines(z, "RXNCONSO.RRF"))
        ndc = parse_ndc(_lines(z, "RXNSAT.RRF"))
    manifest = {
        "asset": "rxnorm",
        "release": f"RxNorm current prescribable content {RELEASE_DATE.isoformat()}",
        "source": PAGE,
        "sources": {path.name: verify(path, None)},
        "pinned_md5_match": md5_match(path),
        "subset": False,
        "attribution": (
            "This product uses publicly available data courtesy of the U.S. National Library of "
            "Medicine (NLM), National Institutes of Health, Department of Health and Human "
            "Services; NLM is not responsible for the product and does not endorse or recommend "
            "this or any other product."
        ),
    }
    return {"rxnorm": conso, "rxnorm_ndc": ndc}, manifest
