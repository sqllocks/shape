"""Age and sex edits from CMS's "Definitions of Medicare Code Edits" (the Medicare Code Editor).

Two inputs, because CMS **deactivated the sex-conflict edit as of 2024-10-01**, so its code lists
are no longer published:

* **age** lists from the current document (version 44, FY2027): codes only possible in
  the perinatal/newborn period (age 0), in children (0-17), in the maternity age range
  (9-64) and in adults (15-124), with the ranges taken from the section headings;
* **sex** lists (diagnoses and procedures for females only and for males only) from version
  41.1, the last that published them (effective 2024-04-01 to 2024-09-30). Codes added to
  ICD-10-CM or ICD-10-PCS after FY2024 are not on these lists ``[VERIFY]``.

Output table ``mce_edits``: ``system`` (``icd10cm`` or ``icd10pcs``), ``code``, ``edit``
(``age`` or ``sex``), ``value`` (``perinatal``, ``pediatric``, ``maternity``, ``adult``, ``F``,
``M``), ``age_min`` and ``age_max`` (years, inclusive; null for sex rows), ``source``.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

PAGE = (
    "https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/"
    "ms-drg-classifications-and-software"
)
AGE_URL = "https://www.cms.gov/files/zip/fy2027-fr-definition-mce-v44.zip"
AGE_VERSION = "MCE v44 (FY2027)"
SEX_URL = "https://www.cms.gov/files/zip/definition-medicare-code-edits-v411.zip"
SEX_VERSION = "MCE v41.1 (FY2024, last to list sex conflicts)"
PINS: dict[str, str] = {}

_HEAD = re.compile(r"^([A-D])\. (.+?)\s*$")
_ROW = re.compile(r"^([0-9A-Z]{3,7})\t")
_AGES = {
    "perinatal/newborn diagnoses": ("perinatal", 0, 0),
    "pediatric diagnoses": ("pediatric", None, None),
    "maternity diagnoses": ("maternity", None, None),
    "adult diagnoses": ("adult", None, None),
}
_SEX = {
    "diagnoses for females only": ("icd10cm", "F"),
    "procedures for females only": ("icd10pcs", "F"),
    "diagnoses for males only": ("icd10cm", "M"),
    "procedures for males only": ("icd10pcs", "M"),
}


def _sections(text: str, wanted: dict[str, Any]) -> dict[str, tuple[str, list[str]]]:
    """Body sections (not the table of contents): heading key -> (heading, codes). A heading
    is 'A. ...' alone on its line; the table-of-contents copy ends in a tab and a page number
    and holds no code rows, so a heading that gathers no rows is dropped."""
    out: dict[str, tuple[str, list[str]]] = {}
    cur: tuple[str, str] | None = None
    rows: list[str] = []

    def close() -> None:
        if cur is not None and rows:
            out[cur[0]] = (cur[1], rows[:])

    for line in text.splitlines():
        line = line.rstrip("\r")
        m = _HEAD.match(line)
        if m and "\t" not in line:
            close()
            rows = []
            key = re.sub(r"\s*\(.*\)\s*$", "", m.group(2)).strip().lower()
            cur = (key, m.group(2)) if key in wanted else None
            continue
        if re.match(r"^\d+\. ", line) and "\t" not in line:
            close()
            rows = []
            cur = None
            continue
        if cur is not None:
            r = _ROW.match(line)
            if r:
                rows.append(r.group(1))
    close()
    return out


def parse(age_text: str, sex_text: str) -> pa.Table:
    cols: dict[str, list[Any]] = {
        k: [] for k in "system code edit value age_min age_max source".split()
    }
    ages = _sections(age_text, _AGES)
    if set(ages) != set(_AGES):
        raise ValueError(f"age sections found: {sorted(ages)}; expected {sorted(_AGES)}")
    for key, (heading, codes) in ages.items():
        name, lo, hi = _AGES[key]
        rng = re.search(r"age (\d+) through (\d+)", heading)
        if rng:
            lo, hi = int(rng.group(1)), int(rng.group(2))
        if lo is None or hi is None:
            raise ValueError(f"no age range in heading {heading!r}")
        for c in codes:
            cols["system"].append("icd10cm")
            cols["code"].append(c)
            cols["edit"].append("age")
            cols["value"].append(name)
            cols["age_min"].append(lo)
            cols["age_max"].append(hi)
            cols["source"].append(AGE_VERSION)
    sexes = _sections(sex_text, _SEX)
    if set(sexes) != set(_SEX):
        raise ValueError(f"sex sections found: {sorted(sexes)}; expected {sorted(_SEX)}")
    for key, (_, codes) in sexes.items():
        system, sex = _SEX[key]
        for c in codes:
            cols["system"].append(system)
            cols["code"].append(c)
            cols["edit"].append("sex")
            cols["value"].append(sex)
            cols["age_min"].append(None)
            cols["age_max"].append(None)
            cols["source"].append(SEX_VERSION)
    schema = pa.schema(
        [
            ("system", pa.string()),
            ("code", pa.string()),
            ("edit", pa.string()),
            ("value", pa.string()),
            ("age_min", pa.int16()),
            ("age_max", pa.int16()),
            ("source", pa.string()),
        ]
    )
    return pa.table(cols, schema=schema)


def _read(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        return z.read(name).decode("utf-8", errors="replace")


def build(
    data_dir: Path | None = None,
    *,
    download_dir: Path | None = None,
    from_files: dict[str, Path] | None = None,
) -> tuple[pa.Table, dict[str, Any]]:
    from shape_healthcare_codes.fetch import download, verify

    age = (from_files or {}).get("age") or download(AGE_URL, download_dir)
    sex = (from_files or {}).get("sex") or download(SEX_URL, download_dir)
    table = parse(_read(age), _read(sex))
    manifest = {
        "asset": "mce_edits",
        "release": f"age: {AGE_VERSION}; sex: {SEX_VERSION}",
        "source": PAGE,
        "sources": {age.name: verify(age, None), sex.name: verify(sex, None)},
        "subset": False,
    }
    return table, manifest
