"""FDA NDC Directory: one row per package, with the 11-digit 5-4-2 NDC and marketing dates.

Source: the FDA's ``ndctext.zip`` (``product.txt`` and ``package.txt``, tab separated, a header
row). The FDA lists NDCs as dashed 10-digit codes in three layouts (4-4-2, 5-3-2, 5-4-1);
:func:`shape_healthcare_codes.ndc.normalize_ndc` pads them to 5-4-2. The table has the base
columns (``code`` is the 11-digit NDC; ``valid_from`` and ``valid_to`` are the package marketing
start and end dates, falling back to the product's), plus the product attributes below.

The FDA rebuilds the file daily and publishes no checksum: the manifest records the SHA-256 of
the bytes used and the file date. The directory lists current and recently delisted products
only, so an NDC that was real years ago may be absent ``[VERIFY]``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import zipfile
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape_healthcare_codes.ndc import normalize_ndc

URL = "https://www.accessdata.fda.gov/cder/ndctext.zip"
SOURCE = "https://www.fda.gov/drugs/drug-approvals-and-databases/national-drug-code-directory"


def _date(s: str) -> dt.date | None:
    s = s.strip()
    if len(s) == 8 and s.isdigit():
        try:
            return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return None


def _rows(text: str) -> list[dict[str, str]]:
    # QUOTE_NONE: the FDA file is tab separated and its text fields hold bare quote marks.
    return list(csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE))


def parse(product_text: str, package_text: str) -> pa.Table:
    products = {p["PRODUCTID"]: p for p in _rows(product_text)}
    cols: dict[str, list[Any]] = {
        k: []
        for k in (
            "code short_desc long_desc leaf valid_from valid_to product_ndc labeler_name "
            "proprietary_name nonproprietary_name dosage_form route marketing_category "
            "product_type dea_schedule substance_name strength strength_unit pharm_classes "
            "sample_package listing_certified_through"
        ).split()
    }
    seen: set[str] = set()
    for pk in sorted(_rows(package_text), key=lambda r: r["NDCPACKAGECODE"]):
        ndc11 = normalize_ndc(pk["NDCPACKAGECODE"])
        prod = products.get(pk["PRODUCTID"])
        if ndc11 is None or prod is None or ndc11 in seen:
            continue
        seen.add(ndc11)
        name = prod["PROPRIETARYNAME"] or prod["NONPROPRIETARYNAME"]
        cols["code"].append(ndc11)
        cols["short_desc"].append(name)
        cols["long_desc"].append(pk["PACKAGEDESCRIPTION"])
        cols["leaf"].append(True)
        cols["valid_from"].append(
            _date(pk["STARTMARKETINGDATE"]) or _date(prod["STARTMARKETINGDATE"])
        )
        cols["valid_to"].append(_date(pk["ENDMARKETINGDATE"]) or _date(prod["ENDMARKETINGDATE"]))
        cols["product_ndc"].append(prod["PRODUCTNDC"])
        cols["labeler_name"].append(prod["LABELERNAME"])
        cols["proprietary_name"].append(prod["PROPRIETARYNAME"] or None)
        cols["nonproprietary_name"].append(prod["NONPROPRIETARYNAME"] or None)
        cols["dosage_form"].append(prod["DOSAGEFORMNAME"] or None)
        cols["route"].append(prod["ROUTENAME"] or None)
        cols["marketing_category"].append(prod["MARKETINGCATEGORYNAME"] or None)
        cols["product_type"].append(prod["PRODUCTTYPENAME"] or None)
        cols["dea_schedule"].append(prod["DEASCHEDULE"] or None)
        cols["substance_name"].append(prod["SUBSTANCENAME"] or None)
        cols["strength"].append(prod["ACTIVE_NUMERATOR_STRENGTH"] or None)
        cols["strength_unit"].append(prod["ACTIVE_INGRED_UNIT"] or None)
        cols["pharm_classes"].append(prod["PHARM_CLASSES"] or None)
        cols["sample_package"].append(pk["SAMPLE_PACKAGE"] == "Y")
        cols["listing_certified_through"].append(_date(prod["LISTING_RECORD_CERTIFIED_THROUGH"]))
    schema = pa.schema(
        [
            ("code", pa.string()),
            ("short_desc", pa.string()),
            ("long_desc", pa.string()),
            ("leaf", pa.bool_()),
            ("valid_from", pa.date32()),
            ("valid_to", pa.date32()),
            ("product_ndc", pa.string()),
            ("labeler_name", pa.string()),
            ("proprietary_name", pa.string()),
            ("nonproprietary_name", pa.string()),
            ("dosage_form", pa.string()),
            ("route", pa.string()),
            ("marketing_category", pa.string()),
            ("product_type", pa.string()),
            ("dea_schedule", pa.string()),
            ("substance_name", pa.string()),
            ("strength", pa.string()),
            ("strength_unit", pa.string()),
            ("pharm_classes", pa.string()),
            ("sample_package", pa.bool_()),
            ("listing_certified_through", pa.date32()),
        ]
    )
    return pa.table(cols, schema=schema)


def build(
    data_dir: Path | None = None,
    *,
    download_dir: Path | None = None,
    from_files: dict[str, Path] | None = None,
) -> tuple[pa.Table, dict[str, Any]]:
    from shape_healthcare_codes.fetch import download, verify

    path = (from_files or {}).get("zip") or download(URL, download_dir)
    with zipfile.ZipFile(path) as z:
        product = z.read("product.txt").decode("latin-1")
        package = z.read("package.txt").decode("latin-1")
        stamp = max(i.date_time for i in z.infolist())
    table = parse(product, package)
    day = dt.date(stamp[0], stamp[1], stamp[2]).isoformat()
    manifest = {
        "asset": "ndc",
        "release": f"FDA NDC Directory file of {day}",
        "source": SOURCE,
        "sources": {path.name: verify(path, None)},
        "subset": False,
    }
    return table, manifest
