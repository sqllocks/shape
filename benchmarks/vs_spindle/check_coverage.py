"""Every Spindle file must be owned by a work package (D-01).

    source scripts/env.sh && python benchmarks/vs_spindle/check_coverage.py

Every file under ``$SPINDLE_ROOT/sqllocks_spindle/`` (excluding ``__pycache__``) must match at
least one glob in ``docs/plans/spindle_coverage.tsv`` (fnmatch on the path relative to
``sqllocks_spindle/``, with an equal number of path segments), and every work package named
there must exist in section 11 of ``docs/plans/COMPLETION_PLAN.md``. Exit 1 otherwise.
Standard library only; it only reads the Spindle checkout.
"""

from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import SHAPE_ROOT, SPINDLE_ROOT  # noqa: E402

TSV = SHAPE_ROOT / "docs" / "plans" / "spindle_coverage.tsv"
PLAN = SHAPE_ROOT / "docs" / "plans" / "COMPLETION_PLAN.md"
TRACKER_ROW = re.compile(r"^\|\s*\d+\s*\|\s*(\S+)\s*\|")


def load_globs(path: Path = TSV) -> list[tuple[str, str]]:
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            raise SystemExit(f"{path}: malformed row {line!r}")
        rows.append((cols[0].strip(), cols[1].strip()))
    return rows


def tracker_wps(plan: Path = PLAN) -> set[str]:
    """Work-package IDs in the section 11 status tracker."""
    text = plan.read_text()
    section = text[text.index("## 11. Status tracker") : text.index("## 12.")]
    return {m.group(1) for ln in section.splitlines() if (m := TRACKER_ROW.match(ln))}


def matches(glob: str, rel: str) -> bool:
    return len(glob.split("/")) == len(rel.split("/")) and fnmatch.fnmatchcase(rel, glob)


def spindle_files(root: Path) -> list[str]:
    base = root / "sqllocks_spindle"
    return sorted(
        p.relative_to(base).as_posix()
        for p in base.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def check(root: Path = SPINDLE_ROOT) -> tuple[list[str], list[str]]:
    """-> (files matched by no glob, work packages missing from the tracker)"""
    rows = load_globs()
    files = spindle_files(root)
    unmatched = [f for f in files if not any(matches(g, f) for g, _ in rows)]
    known = tracker_wps()
    missing = sorted({wp for _, wp in rows if wp not in known})
    return unmatched, missing


def main() -> int:
    if not (SPINDLE_ROOT / "sqllocks_spindle").is_dir():
        print(
            f"ERROR: no Spindle checkout at {SPINDLE_ROOT} (run setup_spindle.sh)", file=sys.stderr
        )
        return 2
    unmatched, missing = check()
    for f in unmatched:
        print(f"UNMAPPED  {f}")
    for wp in missing:
        print(f"UNKNOWN WORK PACKAGE  {wp}")
    n = len(spindle_files(SPINDLE_ROOT))
    if unmatched or missing:
        print(f"FAIL: {len(unmatched)} of {n} files unmapped, {len(missing)} unknown work packages")
        return 1
    print(f"OK: all {n} Spindle files are mapped; every work package exists in section 11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
