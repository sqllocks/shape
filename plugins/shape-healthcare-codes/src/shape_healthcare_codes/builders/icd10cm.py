"""ICD-10-CM from CDC/NCHS: one row per code, validity per release.

Inputs: every published release from FY2016 on, as CDC's (or, for mid-year updates, CMS's)
*order file* (fixed width: order number, code, header-or-billable flag, short and long
description); and from the latest year the tabular XML (chapters, blocks, 7th-character
definitions). The output table has the base columns of :mod:`shape_healthcare_codes.model` plus:

``category`` (first three characters), ``chapter`` (1-22) and ``chapter_desc``, ``block``
(``A00-A09``) and ``block_desc``, ``ext7`` (the 7th-character extension) and ``ext7_meaning``
(its definition in the current tabular list; null for a code without one and for retired
codes), ``laterality`` (``right``, ``left``, ``bilateral`` or ``unspecified``, from the
7th-character definition or the description, else null), and the masks ``valid_mask`` (the
code exists in release ``i``, see the ``releases`` schema metadata) and ``leaf_mask`` (it is
billable in it).

The code set changes every October 1, and sometimes mid-year: the January 1, 2021, April 1,
2022 and April 1, 2023 updates add codes and are separate releases here. CDC's April 1, 2025
and April 1, 2026 files are identical to October's in code and flag (checked at build design),
so they are not releases. Known gap ``[VERIFY]``: the April 1, 2020 update (U07.1) has no
file on the CDC or CMS download pages that this builder can pin, so U07.1 is reported invalid
from 2020-04-01 to 2020-09-30; it is valid from FY2021 on.
"""

from __future__ import annotations

import datetime as dt
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape_healthcare_codes._xml import parse_xml
from shape_healthcare_codes.builders._releases import OrderRow, accumulate, parse_order, read_text
from shape_healthcare_codes.model import Release, releases_to_json

BASE = "https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM"
CMS = "https://www.cms.gov/files/zip"


@dataclass(frozen=True, slots=True)
class ReleaseSource:
    id: str
    effective: dt.date
    url: str
    member: str | None  # the file inside a zip, as a regex on its base name; None: plain text


_O = r"^(?!.*addend).*order.*\.txt$"
RELEASES: tuple[ReleaseSource, ...] = (
    ReleaseSource(
        "FY2016", dt.date(2015, 10, 1), f"{BASE}/2016/ICD10CM_FY2016_code_descriptions.zip", _O
    ),
    ReleaseSource("FY2017", dt.date(2016, 10, 1), f"{BASE}/2017/icd10cm_order_2017.txt", None),
    ReleaseSource(
        "FY2018", dt.date(2017, 10, 1), f"{BASE}/2018/2018-ICD-10-Code-Order-Descriptions.zip", _O
    ),
    ReleaseSource("FY2019", dt.date(2018, 10, 1), f"{BASE}/2019/icd10cm_order_2019.txt", None),
    ReleaseSource("FY2020", dt.date(2019, 10, 1), f"{BASE}/2020/icd10cm_order_2020.txt", None),
    ReleaseSource("FY2021", dt.date(2020, 10, 1), f"{BASE}/2021/icd10cm_order_2021.txt", None),
    ReleaseSource(
        "2021-01",
        dt.date(2021, 1, 1),
        f"{CMS}/2021-code-descriptions-tabular-order-updated-12162020.zip",
        _O,
    ),
    ReleaseSource("FY2022", dt.date(2021, 10, 1), f"{BASE}/2022/Code%20Descriptions%20zip.zip", _O),
    ReleaseSource("2022-04", dt.date(2022, 4, 1), f"{BASE}/2022/icd10cm_order_2022.txt", None),
    ReleaseSource("FY2023", dt.date(2022, 10, 1), f"{BASE}/2023/icd10-Order-CodeFiles2023.zip", _O),
    ReleaseSource(
        "2023-04",
        dt.date(2023, 4, 1),
        f"{CMS}/2023-code-descriptions-tabular-order-updated-01/11/2023.zip",
        _O,
    ),
    ReleaseSource(
        "FY2024", dt.date(2023, 10, 1), f"{BASE}/2024/icd10cm-CodesDescriptions-2024.zip", _O
    ),
    ReleaseSource(
        "FY2025", dt.date(2024, 10, 1), f"{BASE}/2025/ICD10-CM%20Code%20Descriptions%202025.zip", _O
    ),
    ReleaseSource(
        "FY2026", dt.date(2025, 10, 1), f"{BASE}/2026/icd10cm-Code%20Descriptions-2026.zip", _O
    ),
    ReleaseSource(
        "FY2027",
        dt.date(2026, 10, 1),
        f"{BASE}/2027/icd10cm-code-descriptions-2027.zip",
        r"order-2027\.txt$",
    ),
)
"""sha256 of each source file as downloaded on 2026-10-02 (the publisher gives no checksums)."""
PINS: dict[str, str] = {
    "FY2016": "sha256:89a04f5c03754ae72a76e5a751a712f570ae38ba592c032c146dc96ff9f49c89",
    "FY2017": "sha256:f3540a91e0186a4f963875f755e6b7b3b0e9251bf647a3c515102df8f541b47f",
    "FY2018": "sha256:9be0496f74887b563029569579ca345d091c31547cc81973e3725a212d0cce78",
    "FY2019": "sha256:c3d6a88945ba1416848f6f7bea738d382484a8c469dff585e58172b9a573aeb4",
    "FY2020": "sha256:80991e20ffb60086ba0340572dd0e3e2248c6647c31a06fadcb30b419dac02bc",
    "FY2021": "sha256:4f13fe608a039d01eab8ae417d69b351160fb1eb2256d4586f8d9f56a197899b",
    "2021-01": "sha256:479be4d5773fb2e8357c5edb8a56bbd48da4d2bca443a7c42527ad102e3b388c",
    "FY2022": "sha256:bab2b3b041cf4b999f8380f3198c1dd6ca404ba7d5513ec60efc813a97f7c214",
    "2022-04": "sha256:6fd40ef6ab4860d3299578bf15fa7e2dba17d271efc3929c56b3353a45f22cc7",
    "FY2023": "sha256:a7bfd4511dc154bff96fe0ced5fcd633bd26f997ccd6ae386f5c24595e1789b5",
    "2023-04": "sha256:cc7158228f6de01aa08650cc3afe446973a879dd78d44f1fec7150d45cfe1e06",
    "FY2024": "sha256:a3f3d21bfced02ce422699dbc10b952594783b12674bd2c133e8221ba3b5c260",
    "FY2025": "sha256:5a9ff9e18d90a9979a144559a93e377624795369123cebf76817c44f565f0c60",
    "FY2026": "sha256:a852eb91b3344ae38476e63816976ee1eeb94dcced7151118324f060e8499f88",
    "FY2027": "sha256:93e3ad6004badf470c55bfe679b748ae88fd9b2b421851e409eec382c7713b9a",
    "tabular": "sha256:99b5c2be16ff53eab861cc0be4b1dacba27f687b8ff4bc700679944355037ee0",
}
TABULAR_URL = f"{BASE}/2027/icd10cm-table-and-index-2027.zip"
TABULAR_MEMBER = r"icd10cm-tabular.*\.xml$"


# -- tabular XML ---------------------------------------------------------------------------


@dataclass(slots=True)
class Tabular:
    chapters: list[tuple[int, str, str, str]]  # number, first category, last category, desc
    blocks: list[tuple[str, str, str, int]]  # first, last, desc, chapter
    ext7: dict[str, dict[str, str]]  # stem (no dot) -> {7th char: meaning}, inherited


def _strip_range(desc: str) -> str:
    return re.sub(r"\s*\([A-Z0-9]{3}(?:-[A-Z0-9]{3})?\)\s*$", "", desc).strip()


def parse_tabular(xml: bytes | str) -> Tabular:
    root = parse_xml(xml)
    chapters: list[tuple[int, str, str, str]] = []
    blocks: list[tuple[str, str, str, int]] = []
    ext7: dict[str, dict[str, str]] = {}

    def walk(diag: Element, inherited: dict[str, str]) -> None:
        own = diag.find("sevenChrDef")
        defs = (
            {e.get("char", ""): (e.text or "").strip() for e in own.findall("extension")}
            if own is not None
            else inherited
        )
        name = (diag.findtext("name") or "").replace(".", "").upper()
        kids = diag.findall("diag")
        if defs:
            ext7[name] = defs
        for kid in kids:
            walk(kid, defs)

    for ch in root.findall("chapter"):
        num = int(ch.findtext("name") or 0)
        desc = (ch.findtext("desc") or "").strip()
        m = re.search(r"\(([A-Z0-9]{3})-([A-Z0-9]{3})\)\s*$", desc)
        chapters.append((num, m.group(1) if m else "", m.group(2) if m else "", _strip_range(desc)))
        for sec in ch.findall("section"):
            sid = sec.get("id", "")
            first, _, last = sid.partition("-")
            blocks.append((first, last or first, _strip_range(sec.findtext("desc") or ""), num))
            for diag in sec.findall("diag"):
                walk(diag, {})
    return Tabular(chapters, blocks, ext7)


def _in_range(cat: str, first: str, last: str) -> bool:
    return first <= cat <= last


_UNSPEC = re.compile(
    r"unspecified (?:side|eye|ear|breast|kidney|lung|limb|arm|leg|hand|foot|hip|knee|ankle|"
    r"shoulder|elbow|wrist|finger|toe|thumb|femur|tibia|fibula|humerus|radius|ulna|"
    r"extremity|lower|upper|orbit|lens|cornea|eyelid)",
    re.I,
)


def laterality_of(text: str) -> str | None:
    """``right``, ``left``, ``bilateral`` or ``unspecified`` from a description or a 7th-
    character meaning, else None. A text that names both right and left is None."""
    low = text.lower()
    if "bilateral" in low:
        return "bilateral"
    r, left = re.search(r"\bright\b", low), re.search(r"\bleft\b", low)
    if r and left:
        return None
    if r:
        return "right"
    if left:
        return "left"
    return "unspecified" if _UNSPEC.search(low) else None


def build_table(
    per_release: Sequence[tuple[Release, Iterable[OrderRow]]],
    tabular: Tabular | None = None,
) -> pa.Table:
    """The compact table from the order rows of each release, oldest first."""
    releases = [r for r, _ in per_release]
    acc = accumulate(per_release)
    chapters = tabular.chapters if tabular else []
    blocks = tabular.blocks if tabular else []
    names = (
        "code short_desc long_desc leaf valid_from valid_to category chapter chapter_desc "
        "block block_desc ext7 ext7_meaning laterality valid_mask leaf_mask"
    ).split()
    cols: dict[str, list[Any]] = {k: [] for k in names}
    newest = len(releases) - 1
    for code in sorted(acc):
        e = acc[code]
        cat = code[:3]
        chap = next((c for c in chapters if _in_range(cat, c[1], c[2])), None)
        blk = next((b for b in blocks if _in_range(cat, b[0], b[1])), None)
        # Only a stem the tabular list gives 7th-character definitions has an extension (a
        # 7-character neoplasm code such as C44.1021 has none), so retired codes, whose stem is
        # not in the current list, get no extension.
        meaning = _ext7_meaning(tabular, code) if tabular and len(code) == 7 else None
        char = code[6] if meaning is not None else None
        side = laterality_of(meaning) if meaning else None
        if side is None and e.leaf:
            side = laterality_of(e.long)
        cols["code"].append(code)
        cols["short_desc"].append(e.short)
        cols["long_desc"].append(e.long)
        cols["leaf"].append(e.leaf)
        cols["valid_from"].append(releases[e.first].effective)
        cols["valid_to"].append(
            None if e.last == newest else releases[e.last + 1].effective - dt.timedelta(days=1)
        )
        cols["category"].append(cat)
        cols["chapter"].append(chap[0] if chap else None)
        cols["chapter_desc"].append(chap[3] if chap else None)
        cols["block"].append(_block_id(blk))
        cols["block_desc"].append(blk[2] if blk else None)
        cols["ext7"].append(char)
        cols["ext7_meaning"].append(meaning)
        cols["laterality"].append(side)
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
            ("category", pa.string()),
            ("chapter", pa.int8()),
            ("chapter_desc", pa.string()),
            ("block", pa.string()),
            ("block_desc", pa.string()),
            ("ext7", pa.string()),
            ("ext7_meaning", pa.string()),
            ("laterality", pa.string()),
            ("valid_mask", pa.int64()),
            ("leaf_mask", pa.int64()),
        ],
        metadata={"releases": releases_to_json(releases)},
    )
    return pa.table(cols, schema=schema)


def _ext7_meaning(tabular: Tabular, code: str) -> str | None:
    """The tabular list defines the 7th characters on a stem (the 6-character code or a
    shorter ancestor), and does not list the 7-character codes themselves."""
    for n in range(6, 2, -1):
        defs = tabular.ext7.get(code[:n])
        if defs is not None:
            return defs.get(code[6])
    return None


def _block_id(blk: tuple[str, str, str, int] | None) -> str | None:
    if blk is None:
        return None
    return blk[0] if blk[0] == blk[1] else f"{blk[0]}-{blk[1]}"


STARTER_PREFIXES = (
    "A09 B20 C18 C34 C50 C61 C78 D50 E03 E04 E08 E09 E10 E11 E13 E55 E66 E78 E87 F10 F17 F32 F33 "
    "F41 G20 G30 G35 G43 G47 H25 H40 I10 I11 I12 I13 I20 I21 I25 I26 I48 I50 I63 I73 I82 J01 J02 "
    "J06 J18 J20 J30 J44 J45 J96 K21 K29 K35 K40 K57 K80 L03 L40 M05 M10 M16 M17 M19 M25 M32 M45 "
    "M47 M51 M54 M79 M81 N17 N18 N20 N30 N39 N40 N80 N92 O00 O10 O14 O24 O26 O34 O42 O60 O80 O82 "
    "P07 P22 P59 Q21 Q90 R05 R07 R10 R50 R51 R55 R73 S06 S42 S52 S72 S82 T14 T78 U07 V43 Z00 Z01 "
    "Z12 Z13 Z23 Z34 Z38 Z51 Z68 Z79 Z85 Z91 Z95 Z98"
).split()
"""Category prefixes of the starter subset shipped in the wheel: the conditions the healthcare
payer domain starts from (diabetes, hypertension, heart and kidney disease, obesity, lipids,
asthma and COPD, cancers, pregnancy and delivery, common injuries with 7th characters, encounters
and screening), with all their codes and their full validity history."""


def subset_codes(table: pa.Table, prefixes: Iterable[str] = STARTER_PREFIXES) -> pa.Table:
    """The rows whose code starts with one of ``prefixes`` (the shipped starter subset)."""
    keep = pa.array([False] * table.num_rows)
    for p in prefixes:
        keep = pc.or_(keep, pc.starts_with(table.column("code"), p))
    return table.filter(keep)


def build(
    data_dir: Path | None = None,
    *,
    download_dir: Path | None = None,
    from_files: dict[str, Path] | None = None,
) -> tuple[pa.Table, dict[str, Any]]:
    """Download (or read ``from_files``, keyed by release id and ``"tabular"``) and build the
    table and its manifest. Returns ``(table, manifest)``; the caller writes them."""
    from shape_healthcare_codes.fetch import download, verify

    files: dict[str, Path] = {}
    per_release: list[tuple[Release, list[OrderRow]]] = []
    for src in RELEASES:
        path = (from_files or {}).get(src.id) or download(src.url, download_dir, pin=PINS[src.id])
        files[src.id] = path
        per_release.append(
            (Release(src.id, src.effective), parse_order(read_text(path, src.member)))
        )
    tab_path = (from_files or {}).get("tabular") or download(
        TABULAR_URL, download_dir, pin=PINS["tabular"]
    )
    with zipfile.ZipFile(tab_path) as z:
        name = next(
            n for n in z.namelist() if re.search(TABULAR_MEMBER, n.rsplit("/", 1)[-1], re.I)
        )
        tabular = parse_tabular(z.read(name))
    files["tabular"] = tab_path
    table = build_table(per_release, tabular)
    manifest = {
        "asset": "icd10cm",
        "release": f"{RELEASES[-1].id} (effective {RELEASES[-1].effective.isoformat()})",
        "releases": [r.id for r, _ in per_release],
        "source": BASE,
        "sources": {k: verify(p, None) for k, p in sorted(files.items())},
        "subset": False,
    }
    return table, manifest
