"""``shape learn``: profile data files and write a generation schema (P4-08).

Nothing heavy loads at import time (T-18); the command imports the profiler and the schema builder
when it runs.
"""

from __future__ import annotations

import argparse
import errno
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
        raise FileNotFoundError(errno.ENOENT, "file not found", str(path))
    if fmt is not None and path.suffix.lower().lstrip(".") not in (fmt, "ndjson"):
        raise ValueError(f"{path} is not a {fmt} file")
    return path


def _non_finite(obj: Any, path: list[str] | None = None) -> tuple[list[str], float] | None:
    """The path and value of the first infinite or NaN number in ``obj``, or None."""
    import math

    path = path or []
    if isinstance(obj, float) and not math.isfinite(obj):
        return path, obj
    items = (
        obj.items() if isinstance(obj, dict) else enumerate(obj) if isinstance(obj, list) else ()
    )
    for key, value in items:
        found = _non_finite(value, [*path, str(key)])
        if found is not None:
            return found
    return None


def run(a: argparse.Namespace) -> int:
    from shape.generation.learn import learn
    from shape.profile.reference import profile

    path = Path(a.input)
    source = _sources(path, a.input_format)
    from shape.cli import errors

    errors.refuse_same_file(a.output, *(source.values() if isinstance(source, dict) else [path]))
    schema = learn(profile(source), a.domain)
    if a.output:
        out = Path(a.output)
    else:
        out = (
            (path / f"{a.domain}.schema.json")
            if path.is_dir()
            else path.with_suffix(".schema.json")
        )
    document = schema.to_dict()
    bad = _non_finite(document)
    if bad is not None:
        at, value = bad
        where = f"column {at[1]}.{at[3]}" if at[:1] == ["tables"] and len(at) > 3 else ""
        raise ValueError(
            f"the inferred schema holds {value} at {'.'.join(at)}"
            + (f" ({where}: its values overflow a float)" if where else "")
            + f", which JSON cannot hold; {out} was not written"
        )
    out.write_text(
        json.dumps(document, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n"
    )

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
