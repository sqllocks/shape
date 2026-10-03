"""``shape reference list|show``: the reference packs Shape can find (W3-12).

``list`` prints every pack and dataset found (search paths, then the shipped packs) with its
version, rows, license and where it came from. ``show NAME`` prints one pack's manifest and, for
each dataset, its fields and first rows (reading a dataset checks its checksum). A pack that
cannot be read is reported and does not stop the listing. Nothing heavy loads at import time
(T-18).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

FIRST_ROWS = 5


def add_arguments(sub: Any) -> None:
    top = sub.add_parser(
        "reference",
        help="reference packs: versioned, checksummed reference data that works offline",
        description="Reference packs hold code lists and lookup tables (ZIP to city, ISO codes) "
        "that `shape profile --reference-pair`, the `reference_pair` contract rule and the "
        "generation strategies read by dataset name. Packs are found in the directories of "
        "SHAPE_REFERENCE_PATH and in the packs that ship with Shape. See docs/REFERENCE_PACKS.md.",
    )
    cmds = top.add_subparsers(dest="reference_cmd", required=True)
    ls = cmds.add_parser("list", help="every pack and dataset found")
    ls.add_argument("--json", action="store_true", help="print JSON")
    sh = cmds.add_parser("show", help="one pack's manifest, fields and first rows")
    sh.add_argument("name", metavar="NAME", help="the pack name (see `shape reference list`)")
    sh.add_argument("--json", action="store_true", help="print JSON")


def _dump(doc: Any) -> None:
    json.dump(doc, sys.stdout, indent=2, ensure_ascii=False)
    print()


def _list(a: argparse.Namespace) -> int:
    from shape.reference import discover_packs

    found = discover_packs()
    for problem in found.problems:
        print(f"shape: warning: {problem.path}: {problem.message}", file=sys.stderr)
    if a.json:
        _dump(
            {
                "packs": [
                    {
                        "name": p.name,
                        "pack_version": p.manifest["pack_version"],
                        "source": p.manifest["source"],
                        "retrieved": p.manifest["retrieved"],
                        "license": p.manifest["license"],
                        "attribution": p.manifest["attribution"],
                        "sensitivity": p.manifest["sensitivity"],
                        "transformation_version": p.manifest["transformation_version"],
                        "origin": p.origin,
                        "path": str(p.path),
                        "datasets": [
                            {k: d[k] for k in ("name", "fields", "rows", "file", "sha256")}
                            for d in p.datasets
                        ],
                    }
                    for p in found.packs
                ],
                "problems": [{"path": str(q.path), "message": q.message} for q in found.problems],
            }
        )
        return 0
    header = ("PACK", "VERSION", "DATASET", "ROWS", "LICENSE", "ORIGIN")
    rows = [
        (
            p.name,
            str(p.manifest["pack_version"]),
            d["name"],
            str(d["rows"]),
            str(p.manifest["license"]),
            f"{p.origin} ({p.path})" if p.origin != "shipped" else "shipped",
        )
        for p in found.packs
        for d in p.datasets
    ]
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    for r in [header, *rows]:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip())
    return 0


def _show(a: argparse.Namespace) -> int:
    from shape.reference import find_pack

    pack = find_pack(a.name)
    datasets = []
    for entry in pack.datasets:
        table = pack.table(entry["name"])
        datasets.append(
            {
                "name": entry["name"],
                "file": entry["file"],
                "fields": list(entry["fields"]),
                "rows": entry["rows"],
                "first_rows": table.slice(0, FIRST_ROWS).to_pylist(),
            }
        )
    if a.json:
        _dump(
            {
                "manifest": pack.manifest,
                "origin": pack.origin,
                "path": str(pack.path),
                "datasets": datasets,
            }
        )
        return 0
    m = pack.manifest
    print(f"{m['name']} {m['pack_version']}  ({pack.origin}: {pack.path})")
    for key in (
        "source",
        "retrieved",
        "license",
        "attribution",
        "transformation_version",
        "sensitivity",
    ):
        print(f"  {key}: {m[key]}")
    for d in datasets:
        print(f"\ndataset {d['name']}: {d['rows']} rows, fields {', '.join(d['fields'])}")
        for row in d["first_rows"]:
            print("  " + "  ".join(f"{k}={row[k]}" for k in d["fields"]))
    return 0


def run(a: argparse.Namespace) -> int:
    return _list(a) if a.reference_cmd == "list" else _show(a)
