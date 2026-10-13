"""Pure TSV/tracker helpers retained with their offline tests after FRZ-01."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TSV = ROOT / "docs/plans/refengine_coverage.tsv"
PLAN = ROOT / "docs/plans/COMPLETION_PLAN.md"
TRACKER_ROW = re.compile(r"^\|\s*\d+\s*\|\s*(\S+)\s*\|")


def load_globs(path: Path = TSV) -> list[tuple[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            raise SystemExit(f"{path}: malformed row {line!r}")
        rows.append((cols[0].strip(), cols[1].strip()))
    return rows


def tracker_wps(plan: Path = PLAN) -> set[str]:
    """Work-package IDs in the section 11 status tracker."""
    text = plan.read_text(encoding="utf-8")
    section = text[text.index("## 11. Status tracker") : text.index("## 12.")]
    return {m.group(1) for ln in section.splitlines() if (m := TRACKER_ROW.match(ln))}


def matches(glob: str, rel: str) -> bool:
    return len(glob.split("/")) == len(rel.split("/")) and fnmatch.fnmatchcase(rel, glob)
