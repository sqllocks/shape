"""``shape learn``: profile data files and write a generation schema (P4-08).

Nothing heavy loads at import time (T-18); the command imports the profiler and the schema builder
when it runs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

FORMATS = ("csv", "parquet", "jsonl")
_PATTERNS = {"csv": ("*.csv",), "parquet": ("*.parquet",), "jsonl": ("*.jsonl", "*.ndjson")}


def add_arguments(sub: Any) -> None:
    le = sub.add_parser(
        "learn",
        help="infer a generation schema from data files",
        description="Profile CSV, Parquet or JSON Lines data (a file, or a directory with one "
        "file per table) and write the generation schema that reproduces it: a strategy per "
        "column, keys and relationships, scale presets and correlated column pairs. Run it with "
        "`shape generate SCHEMA.json`. To generate from a profile directly, see "
        "`shape generate --from`.",
    )
    le.add_argument("input", metavar="PATH", help="a data file, or a directory of data files")
    le.add_argument(
        "-o",
        "--output",
        metavar="SCHEMA.json",
        help="where to write the schema (default: next to the input, as <name>.schema.json)",
    )
    le.add_argument(
        "--format",
        dest="input_format",
        choices=FORMATS,
        help="the file format (default: from the file's extension; csv for a directory)",
    )
    le.add_argument("--domain", default="inferred", help="the schema's domain name")
    le.add_argument("--json", action="store_true", help="print the summary as JSON")


def _sources(path: Path, fmt: str | None) -> dict[str, Path] | Path:
    if path.is_dir():
        patterns = _PATTERNS[fmt or "csv"]
        files = sorted(p for pattern in patterns for p in path.glob(pattern))
        if not files:
            raise ValueError(f"no {fmt or 'csv'} files found in {path}")
        return {p.stem: p for p in files}
    if not path.is_file():
        raise ValueError(f"path not found: {path}")
    if fmt is not None and path.suffix.lower().lstrip(".") not in (fmt, "ndjson"):
        raise ValueError(f"{path} is not a {fmt} file")
    return path


def run(a: argparse.Namespace) -> int:
    from shape.generation.learn import learn
    from shape.profile.reference import profile

    path = Path(a.input)
    source = _sources(path, a.input_format)
    schema = learn(profile(source), a.domain)
    if a.output:
        out = Path(a.output)
    else:
        out = (
            (path / f"{a.domain}.schema.json")
            if path.is_dir()
            else path.with_suffix(".schema.json")
        )
    out.write_text(json.dumps(schema.to_dict(), indent=2) + "\n", encoding="utf-8")

    tables: dict[str, dict[str, Any]] = {
        name: {
            "columns": len(t.columns),
            "primary_key": list(t.primary_key),
            "foreign_keys": sum(1 for c in t.columns.values() if c.strategy == "foreign_key"),
        }
        for name, t in schema.tables.items()
    }
    summary = {
        "domain": a.domain,
        "tables": tables,
        "relationships": len(schema.relationships),
        "correlated_pairs": sum(len(v) for v in schema.correlated_columns.values()),
        "output": str(out),
    }
    if a.json:
        print(json.dumps(summary, indent=2))
        return 0
    print(f"Schema inference: {len(tables)} tables, {len(schema.relationships)} relationships")
    for name, t in tables.items():
        keys = list(t["primary_key"])
        key = f" (key: {', '.join(keys)})" if keys else ""
        fks = f", {t['foreign_keys']} foreign keys" if t["foreign_keys"] else ""
        print(f"  {name}: {t['columns']} columns{key}{fks}")
    print(f"Schema written to {out}")
    return 0
