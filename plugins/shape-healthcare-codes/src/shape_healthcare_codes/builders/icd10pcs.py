"""ICD-10-PCS from CMS: one row per 7-character procedure code, validity per release.

The order files carry the same fixed-width layout as ICD-10-CM's. The table has the base columns
(:mod:`shape_healthcare_codes.model`), ``section`` (the first character), and the masks
``valid_mask`` / ``leaf_mask`` for the releases in the ``releases`` schema metadata.

Releases built: FY2021 (October 1, 2020) to FY2027, plus the April 1, 2025 and April 1, 2026
updates. ``[VERIFY]`` the files CMS labels "updated" (FY2021 to FY2024) are treated as effective
on October 1 of their fiscal year, so a code CMS added in such an update is accepted for up to
two months before its own effective date; dates before 2020-10-01 are not covered (CMS's
older order files sit at other addresses and two are not usable), so ``is_valid`` is False there.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape_healthcare_codes.builders._releases import (
    OrderRow,
    accumulate,
    parse_order,
    read_text,
)
from shape_healthcare_codes.builders.icd10cm import ReleaseSource
from shape_healthcare_codes.model import Release, releases_to_json

CMS = "https://www.cms.gov/files/zip"
_O = r"^(?!.*addend).*order.*\.txt$"
RELEASES: tuple[ReleaseSource, ...] = (
    ReleaseSource(
        "FY2021",
        dt.date(2020, 10, 1),
        f"{CMS}/2021-icd-10-pcs-order-file-long-and-abbreviated-titles-updated-december-1-2020.zip",
        _O,
    ),
    ReleaseSource(
        "FY2022",
        dt.date(2021, 10, 1),
        f"{CMS}/2022-icd-10-pcs-order-file-long-and-abbreviated-titles-updated-december-1-2021.zip",
        _O,
    ),
    ReleaseSource(
        "FY2023",
        dt.date(2022, 10, 1),
        f"{CMS}/2023-icd-10-pcs-order-file-long-and-abbreviated-titles-updated-01/11/2023.zip",
        _O,
    ),
    ReleaseSource(
        "FY2024",
        dt.date(2023, 10, 1),
        f"{CMS}/2024-icd-10-pcs-order-file-long-and-abbreviated-titles-updated-12/19/2023.zip",
        _O,
    ),
    ReleaseSource(
        "FY2025",
        dt.date(2024, 10, 1),
        f"{CMS}/2025-icd-10-pcs-order-file-long-and-abbreviated-titles.zip",
        _O,
    ),
    ReleaseSource(
        "2025-04",
        dt.date(2025, 4, 1),
        f"{CMS}/2025-icd-10-pcs-order-file-long-and-abbreviated-titles-april.zip",
        _O,
    ),
    ReleaseSource(
        "FY2026",
        dt.date(2025, 10, 1),
        f"{CMS}/2026-icd-10-pcs-order-file-long-and-abbreviated-titles.zip",
        _O,
    ),
    ReleaseSource(
        "2026-04",
        dt.date(2026, 4, 1),
        f"{CMS}/april-1-2026-icd-10-pcs-order-file-long-abbreviated-titles.zip",
        _O,
    ),
    ReleaseSource(
        "FY2027",
        dt.date(2026, 10, 1),
        f"{CMS}/2027-icd-10-pcs-order-file-long-abbreviated-titles.zip",
        _O,
    ),
)
"""sha256 of each source file as downloaded on 2026-10-02 (the publisher gives no checksums)."""
PINS: dict[str, str] = {
    "FY2021": "sha256:59c5ba3c6601a6f759b0110bfd9b4fdee8eb556462fcf661a823518ff90f2a93",
    "FY2022": "sha256:0a98d9bd017a04c9ec09d2e6f85924d6a5c54b091bcf7fa4a8ed8793a81244ca",
    "FY2023": "sha256:069062ca36cb8ea0b4803f4ed4668df653f07fd0843f5f28ef81b6a4dce2f653",
    "FY2024": "sha256:2b311e4fa3ac59ffe7b020cf0e05000ec035d1cfdf6a9ec7664eb74b82190253",
    "FY2025": "sha256:3534c5f6ec23d1179d960683feb24137a4c163c7b91aa45a1ad889c042f7027a",
    "2025-04": "sha256:47b135b720322d91ae1cd6926ee4d495d5b8028b4aa4e8e4bdfbfcf7f51420ae",
    "FY2026": "sha256:64e37e6060bfd9eca74d9973be28fdf570b40f87a3a855bd078eb343dd0e594e",
    "2026-04": "sha256:9bb4a892c34b82d525182a6db6ef42ccba2c7b331ac5d24d459664a1129d776d",
    "FY2027": "sha256:c9064c5c66873ecc4cc9aab1b100be605f2db457fd458113076b5eb6ce246439",
}


def build_table(per_release: Sequence[tuple[Release, Iterable[OrderRow]]]) -> pa.Table:
    releases = [r for r, _ in per_release]
    acc = accumulate(per_release)
    newest = len(releases) - 1
    cols: dict[str, list[Any]] = {
        k: []
        for k in (
            "code short_desc long_desc leaf valid_from valid_to section valid_mask leaf_mask"
        ).split()
    }
    for code in sorted(acc):
        e = acc[code]
        cols["code"].append(code)
        cols["short_desc"].append(e.short)
        cols["long_desc"].append(e.long)
        cols["leaf"].append(e.leaf)
        cols["valid_from"].append(releases[e.first].effective)
        cols["valid_to"].append(
            None if e.last == newest else releases[e.last + 1].effective - dt.timedelta(days=1)
        )
        cols["section"].append(code[0])
        cols["valid_mask"].append(e.vmask)
        cols["leaf_mask"].append(e.lmask)
    schema = pa.schema(
        [
            ("code", pa.string()),
            ("short_desc", pa.string()),
            ("long_desc", pa.string()),
            ("leaf", pa.bool_()),
            ("valid_from", pa.date32()),
            ("valid_to", pa.date32()),
            ("section", pa.string()),
            ("valid_mask", pa.int64()),
            ("leaf_mask", pa.int64()),
        ],
        metadata={"releases": releases_to_json(releases)},
    )
    return pa.table(cols, schema=schema)


def build(
    data_dir: Path | None = None,
    *,
    download_dir: Path | None = None,
    from_files: dict[str, Path] | None = None,
) -> tuple[pa.Table, dict[str, Any]]:
    from shape_healthcare_codes.fetch import download, verify

    files: dict[str, Path] = {}
    per_release: list[tuple[Release, list[OrderRow]]] = []
    for src in RELEASES:
        path = (from_files or {}).get(src.id) or download(src.url, download_dir, pin=PINS[src.id])
        files[src.id] = path
        per_release.append(
            (Release(src.id, src.effective), parse_order(read_text(path, src.member)))
        )
    table = build_table(per_release)
    manifest = {
        "asset": "icd10pcs",
        "release": f"{RELEASES[-1].id} (effective {RELEASES[-1].effective.isoformat()})",
        "releases": [r.id for r, _ in per_release],
        "source": "https://www.cms.gov/medicare/coding-billing/icd-10-codes",
        "sources": {k: verify(p, None) for k, p in sorted(files.items())},
        "subset": False,
    }
    return table, manifest
