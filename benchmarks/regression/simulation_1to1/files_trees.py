"""Comparing the directory trees two simulators wrote (file drops, SCD2 drops, hybrid batches).

A tree holds data files (Parquet, CSV, JSON Lines), ``_manifest.json`` files and ``_done`` flags.
``compare_trees`` adds one check per aspect to a case's ``Checks``; a case passes an ``explain``
hook for the differences its allow-list names (it returns True when a data file's difference is
the allowed one, and counts it), so any other difference still fails.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import files_common as sc
import files_compare as cmp
import pandas as pd

DATA_SUFFIXES = {".parquet", ".csv", ".jsonl"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def list_files(root: Path) -> dict[str, Path]:
    """Relative POSIX path to file, for every file under ``root`` but the worker's result."""
    return {
        p.relative_to(root).as_posix(): p
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != "_result.json"
    }


def is_data(path: str) -> bool:
    return Path(path).suffix in DATA_SUFFIXES


def iso_ok(text: str) -> bool:
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def manifest_problems(doc: dict[str, Any]) -> list[str]:
    """What is wrong with the form of a manifest's wall-clock and random fields."""
    out = []
    if "created_utc" in doc and not iso_ok(str(doc["created_utc"])):
        out.append(f"created_utc {doc['created_utc']!r} is not ISO-8601")
    if "correlation_id" in doc and not UUID_RE.match(str(doc["correlation_id"])):
        out.append(f"correlation_id {doc['correlation_id']!r} is not a UUID")
    return out


def compare_manifests(b: dict[str, Any], s: dict[str, Any]) -> list[str]:
    """Differences of two manifests, volatile fields aside."""
    keys_b, keys_s = list(b), list(s)
    out = []
    if keys_b != keys_s:
        out.append(f"keys {keys_b} vs {keys_s}")
    for k in keys_b:
        if k in sc.VOLATILE or k not in s:
            continue
        if k == "file_details":
            if [d["name"] for d in b[k]] != [d["name"] for d in s[k]]:
                out.append("file_details names")
            continue
        if b[k] != s[k]:
            out.append(f"{k}: {b[k]!r} vs {s[k]!r}")
    return out


Explain = Callable[[str, pd.DataFrame, pd.DataFrame], bool]


def compare_trees(
    checks: sc.Checks,
    label: str,
    b_root: Path,
    s_root: Path,
    *,
    ignore_columns: tuple[str, ...] = (),
    explain: Explain | None = None,
    same_column_order: bool = True,
) -> dict[str, int]:
    """Add the checks that ``s_root`` holds what ``b_root`` holds; return how many data files were
    compared and how many differences ``explain`` accounted for."""
    fb, fs = list_files(b_root), list_files(s_root)
    only_b, only_s = sorted(set(fb) - set(fs)), sorted(set(fs) - set(fb))
    checks.add(
        f"{label}: same files",
        not only_b and not only_s,
        f"{len(fb)} files; only in baseline {only_b[:3]}, only in Shape {only_s[:3]}",
    )
    shared = sorted(set(fb) & set(fs))
    data = [p for p in shared if is_data(p)]
    bad: list[str] = []
    explained = 0
    for p in data:
        a, b = cmp.read_frame(fb[p]), cmp.read_frame(fs[p])
        ok, why = cmp.frames_equal(a, b, ignore=ignore_columns, same_order=same_column_order)
        if not ok and explain is not None and explain(p, a, b):
            explained += 1
            continue
        if not ok:
            bad.append(f"{p}: {why}")
        else:
            for col in ignore_columns:  # an ignored column must still be there, in both
                if (col in a.columns) != (col in b.columns):
                    bad.append(f"{p}: column {col} on one side only")
    checks.add(
        f"{label}: data files equal",
        not bad,
        f"{len(data)} files, {explained} explained by the allow-list; first problems {bad[:3]}",
    )
    names = sorted(p for p in shared if p.endswith("_manifest.json"))
    mdiff: list[str] = []
    for p in names:
        mb, ms = json.loads(fb[p].read_text()), json.loads(fs[p].read_text())
        mdiff += [f"{p}: {d}" for d in compare_manifests(mb, ms)]
        mdiff += [f"{p} (Shape): {d}" for d in manifest_problems(ms)]
    checks.add(f"{label}: manifests equal", not mdiff, f"{len(names)} manifests; {mdiff[:3]}")
    flags = sorted(p for p in shared if p.endswith("/_done"))
    badflag = [p for p in flags if not iso_ok(fs[p].read_text().strip())]
    checks.add(f"{label}: done flags", not badflag, f"{len(flags)} flags; bad {badflag[:3]}")
    return {"data_files": len(data), "explained": explained}
