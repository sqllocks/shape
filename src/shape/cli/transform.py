"""``shape transform star|cdm``: reshape a set of tables (P6-06).

``star`` turns normalised tables into dimension and fact tables with surrogate keys and a date
dimension; ``cdm`` writes them as a Common Data Model folder. The source is an installed domain
(generated first) or a directory of CSV, Parquet or JSON Lines files, one per table.

Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

FORMATS = ("csv", "parquet")


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "source",
        metavar="SOURCE",
        help="an installed domain (generated first), or a directory of table files",
    )
    p.add_argument("-o", "--output", required=True, metavar="DIR", help="output directory")
    p.add_argument("--format", choices=FORMATS, default="csv", help="data file format")
    p.add_argument("--scale", default="small", help="scale preset, when SOURCE is a domain")
    p.add_argument("--seed", type=int, default=42, help="random seed, when SOURCE is a domain")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")


def add_arguments(sub: Any) -> None:
    tr = sub.add_parser(
        "transform",
        help="reshape tables into a star schema or a CDM folder",
        description="Reshape a set of related tables. `star` writes dimension tables (dim_*, "
        "with surrogate keys), a date dimension and fact tables (fact_*); `cdm` writes a Common "
        "Data Model folder (model.json plus one data file per entity).",
    )
    kinds = tr.add_subparsers(dest="transform", required=True, metavar="{star,cdm}")
    star = kinds.add_parser(
        "star",
        help="dimension and fact tables with surrogate keys",
        description="Build a star schema. The domain supplies its own mapping; for a directory "
        "of tables give one with --map.",
    )
    _common(star)
    star.add_argument(
        "--map", metavar="MAP.json", help="a star map: dimensions, facts and their keys"
    )
    cdm = kinds.add_parser(
        "cdm",
        help="a Common Data Model folder",
        description="Write a CDM folder. Entities are named by the domain's mapping, by "
        '--map (`{"entities": {"table": "Entity"}}`), or in PascalCase after the table.',
    )
    _common(cdm)
    cdm.add_argument("--map", metavar="ENTITIES.json", help="table to entity names")
    cdm.add_argument("--model-name", help="the model's name (default: Shape<Domain>)")


def _domain_plugin(name: str) -> Any:
    from shape.plugins.host import default_host

    host = default_host()
    return host.get("shape.domains", name) if name in host.names("shape.domains") else None


def _tables(a: argparse.Namespace) -> tuple[dict[str, Any], Any, str]:
    """The source tables, the domain plugin (None for a directory) and the source's name."""
    path = Path(a.source)
    if path.is_dir():
        from shape.dimensional.files import read_tables

        return read_tables(path), None, path.name
    from shape.api import generate

    result = generate(a.source, scale=a.scale, seed=a.seed)
    return dict(result.tables), _domain_plugin(a.source), a.source


def _load_map(path: str) -> dict[str, Any]:
    try:
        document: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read the map {path}: {exc}") from exc
    return document


def _star(a: argparse.Namespace) -> dict[str, Any]:
    from shape.dimensional import StarMap, star_transform
    from shape.dimensional.files import write_tables

    tables, plugin, _ = _tables(a)
    if a.map:
        document = _load_map(a.map)
    elif plugin is not None and hasattr(plugin, "star_map"):
        document = plugin.star_map()
    else:
        raise ValueError(
            f"{a.source!r} has no star mapping: pass one with --map MAP.json "
            f"(dimensions and facts; see `shape transform star --help`)"
        )
    result = star_transform(tables, StarMap.from_dict(document))
    files = write_tables(result.tables(), a.output, a.format)
    for key, n in result.orphans.items():
        print(
            f"shape: warning: {n} row(s) of {key} have no matching dimension row; "
            f"their surrogate key is null",
            file=sys.stderr,
        )
    return {
        "output": a.output,
        "format": a.format,
        "files": [p.name for p in files],
        "tables": result.summary(),
        "orphans": result.orphans,
    }


def _cdm(a: argparse.Namespace) -> dict[str, Any]:
    from shape.dimensional import entity_name, write_cdm_folder

    tables, plugin, name = _tables(a)
    names: dict[str, str] | None = None
    if a.map:
        names = dict(_load_map(a.map).get("entities") or {})
    elif plugin is not None and hasattr(plugin, "cdm_entities"):
        names = dict(plugin.cdm_entities())
    model = a.model_name or f"Shape{entity_name(name) if name else 'Output'}"
    files = write_cdm_folder(tables, a.output, model, names, a.format)
    return {
        "output": a.output,
        "format": a.format,
        "model": model,
        "entities": [entity_name(t, names) for t in tables],
        "files": [str(p.relative_to(a.output)) for p in files],
    }


def run(a: argparse.Namespace) -> int:
    info = _star(a) if a.transform == "star" else _cdm(a)
    if a.json:
        print(json.dumps(info, indent=2))
    elif a.transform == "star":
        print(f"star schema written to {info['output']}/ ({info['format']})")
        for table, stats in info["tables"].items():
            print(f"  {table:<24} {stats['rows']:>10,} rows  {stats['columns']} cols")
    else:
        print(f"CDM folder '{info['model']}' written to {info['output']}/ ({info['format']})")
        print(f"  {len(info['entities'])} entities + model.json")
    return 0
