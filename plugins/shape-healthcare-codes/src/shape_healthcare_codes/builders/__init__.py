"""Asset builders: download from the official source at a pinned release and write compact
Arrow files (see :mod:`shape_healthcare_codes.fetch` and :mod:`shape_healthcare_codes.store`).

Each builder module has ``build(data_dir, *, download_dir, from_files)`` returning the table
(or a ``{asset: table}`` dict) and a manifest. ``from_files`` names files the user already has
(``{"zip": path}``), which skips the download. :func:`run` calls a builder and writes the result.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

BUILDERS: dict[str, str] = {
    "icd10cm": "shape_healthcare_codes.builders.icd10cm",
    "icd10pcs": "shape_healthcare_codes.builders.icd10pcs",
    "hcpcs2": "shape_healthcare_codes.builders.hcpcs",
    "ndc": "shape_healthcare_codes.builders.ndc",
    "rxnorm": "shape_healthcare_codes.builders.rxnorm",
    "pos": "shape_healthcare_codes.builders.pos",
    "hcc": "shape_healthcare_codes.builders.hcc",
    "hcc_hierarchy": "shape_healthcare_codes.builders.hcc_hierarchy",
    "hcc_coefficients": "shape_healthcare_codes.builders.hcc_coefficients",
    "ccsr": "shape_healthcare_codes.builders.ccsr",
    "mce_edits": "shape_healthcare_codes.builders.mce",
}
"""Fetchable assets and the module that builds each. Every other asset is bring-your-own."""


def run(
    asset: str,
    data_dir: Path | None = None,
    *,
    download_dir: Path | None = None,
    from_files: dict[str, Path] | None = None,
    subset: bool = False,
) -> list[Path]:
    """Build ``asset`` and write it (a builder may write more than one asset, e.g. RxNorm)."""
    from shape_healthcare_codes.store import user_dir, write_asset

    if download_dir is None:
        download_dir = (data_dir or user_dir()) / "downloads"
    if asset not in BUILDERS:
        raise KeyError(
            f"{asset!r} has no builder (bring-your-own or unknown); fetchable: {sorted(BUILDERS)}"
        )
    mod = importlib.import_module(BUILDERS[asset])
    result, manifest = mod.build(data_dir, download_dir=download_dir, from_files=from_files)
    tables: dict[str, Any] = result if isinstance(result, dict) else {asset: result}
    out: list[Path] = []
    for name, table in tables.items():
        meta = dict(manifest)
        meta["asset"] = name
        if subset and asset == "icd10cm":
            from shape_healthcare_codes.builders.icd10cm import subset_codes

            table = subset_codes(table)
            meta["subset"] = True
        out.append(write_asset(name, table, meta, data_dir))
    return out
