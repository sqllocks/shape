"""``shape library list|show|get``: the public dataset library.

``list`` and ``show NAME`` print what the library holds; ``get NAME -o X.shape`` copies a safe
profile to a file, ready for ``shape generate --from X.shape`` (or use ``--from dataset:NAME``
directly). Exit 0, or 2 for an unknown dataset or an output that exists. See
``docs/DATASET_LIBRARY.md``. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def add_arguments(sub: Any) -> None:
    """Register ``library`` on the subparsers."""
    li = sub.add_parser(
        "library",
        help="safe profiles of public datasets, to generate realistic data without your own",
        description="The dataset library holds safe profiles (statistics and formats, never rows) "
        "of public datasets under the CC0, CC BY 4.0 and public domain licences, with their "
        "sources and attribution. `shape generate --from dataset:NAME` generates from one with "
        "no network. See docs/DATASET_LIBRARY.md.",
    )
    actions = li.add_subparsers(dest="library_cmd", required=True, metavar="{list,show,get}")
    actions.add_parser("list", help="list the datasets", description=li.description)
    show = actions.add_parser("show", help="show one dataset", description=li.description)
    show.add_argument("name", metavar="NAME", help="a dataset name (see `shape library list`)")
    get = actions.add_parser("get", help="copy a profile to a file", description=li.description)
    get.add_argument("name", metavar="NAME", help="a dataset name")
    get.add_argument("-o", "--output", required=True, metavar="X.shape", help="a new file")


def run(a: argparse.Namespace) -> int:
    from shape import library

    if a.library_cmd == "list":
        entries = library.load_index()
        if a.json:
            print(json.dumps({"datasets": entries}, indent=2))
            return 0
        width = max(len(e["name"]) for e in entries)
        for e in entries:
            print(f"{e['name']:<{width}}  {e['license']:<9}  {e['rows']:>6} rows  {e['title']}")
        return 0
    if a.library_cmd == "show":
        entry = library.get_dataset(a.name)
        if a.json:
            print(json.dumps(entry, indent=2))
            return 0
        print(f"{entry['name']}: {entry['title']}")
        for key in ("license", "rows", "source_url", "retrieved", "source_sha256", "attribution"):
            print(f"  {key}: {entry[key]}")
        print(f"  use: shape generate --from dataset:{entry['name']}")
        return 0
    out = library.copy_profile(a.name, a.output)
    if a.json:
        print(json.dumps({"name": a.name, "written": str(out)}))
    else:
        print(f"wrote {out}")
    return 0
