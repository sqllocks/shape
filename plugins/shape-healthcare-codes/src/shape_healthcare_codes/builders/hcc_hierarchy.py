"""CMS-HCC, ESRD and RxHCC hierarchies from the CMS model software (see ``_cms_software``).

Each model package has a hierarchy macro (``V28115H1``, ``V24H86H1``, ``R08X84H1``, ...: a
``.TXT`` file whose name ends in ``H`` and a digit) with one line per hierarchy::

    /*Neoplasm 1 */   %SET0(CC=17    , HIER=%STR(18, 19, 20, 21, 22, 23 ));

meaning: when a person has category 17, categories 18 to 23 are set to 0. The output has one row
per line, in the published order (the order the software applies them): ``model``, ``hcc`` (the
category number, as text like the mapping table's) and ``drops`` (the category numbers it
removes). Packages that carry the same model must agree; they are kept once.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape_healthcare_codes.builders._cms_software import (
    PAGE,
    RELEASE,
    URL,
    Package,
    read_software_zip,
)

FORMAT = "shape-hcc-hierarchy"
VERSION = 1
SCHEMA = pa.schema(
    [("model", pa.string()), ("hcc", pa.string()), ("drops", pa.list_(pa.string()))],
    metadata={b"format": FORMAT.encode(), b"version": str(VERSION).encode()},
)

_FILE = re.compile(r"^[A-Z]\d{2}\w*H\d\.TXT$", re.I)
_SET0 = re.compile(r"%SET0\s*\(\s*CC\s*=\s*(\d+)\s*,\s*HIER\s*=\s*%STR\s*\(([^)]*)\)\s*\)", re.S)
_DROP = re.compile(r"^\d+$")


def parse(text: str, model: str) -> list[tuple[str, list[str]]]:
    """The ``(hcc, drops)`` pairs of one hierarchy macro, in the published order."""
    out: list[tuple[str, list[str]]] = []
    for m in _SET0.finditer(text):
        drops = [d for d in re.split(r"[\s,]+", m.group(2)) if d]
        bad = [d for d in drops if not _DROP.match(d)]
        if bad or not drops:
            raise ValueError(
                f"{model}: hierarchy of {m.group(1)} lists {drops!r}, not category numbers: "
                "the file layout changed"
            )
        out.append((m.group(1), drops))
    if not out:
        raise ValueError(f"{model}: no %SET0(CC=..., HIER=...) lines: the file layout changed")
    return out


def hierarchy_file(pkg: Package) -> str:
    names = sorted(n for n in pkg.files if _FILE.match(n))
    if len(names) != 1:
        raise ValueError(
            f"{pkg.software}: expected one hierarchy macro (a '...H<digit>.TXT' file), found "
            f"{names}: the file layout changed"
        )
    return names[0]


def from_packages(packages: list[Package]) -> pa.Table:
    by_model: dict[str, tuple[str, list[tuple[str, list[str]]]]] = {}
    for pkg in packages:
        rows = parse(pkg.files[hierarchy_file(pkg)], pkg.model)
        seen = by_model.get(pkg.model)
        if seen is None:
            by_model[pkg.model] = (pkg.software, rows)
        elif seen[1] != rows:
            raise ValueError(
                f"{pkg.model}: packages {seen[0]} and {pkg.software} publish different hierarchies"
            )
    out: dict[str, list[Any]] = {"model": [], "hcc": [], "drops": []}
    for model in sorted(by_model):
        for hcc, drops in by_model[model][1]:
            out["model"].append(model)
            out["hcc"].append(hcc)
            out["drops"].append(drops)
    return pa.table(out, schema=SCHEMA)


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
        "asset": "hcc_hierarchy",
        "format": FORMAT,
        "version": VERSION,
        "release": RELEASE,
        "source": PAGE,
        "sources": {path.name: verify(path, None)},
        "subset": False,
    }
    return table, manifest
