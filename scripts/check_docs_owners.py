"""Inventory hidden documentation owners and block release publishing until resolved."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = re.compile(r"<!--\s*owner:\s*(.*?)\s*-->", re.S | re.I)
ROOT_PAGES = (
    "README.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "CHANGELOG.md",
    "THIRD_PARTY_NOTICES.md",
)


def inventory(root: Path = ROOT) -> list[tuple[str, int, str]]:
    """Return each rendered-input owner note with its source location."""
    paths = [
        p
        for p in (root / "docs").rglob("*.md")
        if not {"plans", "talks"}.intersection(p.relative_to(root / "docs").parts)
    ]
    paths += [root / name for name in ROOT_PAGES if (root / name).exists()]
    result = []
    for path in sorted(paths):
        text = path.read_text()
        for match in OWNER.finditer(text):
            result.append(
                (
                    path.relative_to(root).as_posix(),
                    text[: match.start()].count("\n") + 1,
                    " ".join(match.group(1).split()),
                )
            )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    notes = inventory()
    for path, line, note in notes:
        print(f"{path}:{line}: <!-- owner: {note} -->")
    print(f"Documentation owners: {len(notes)} unresolved")
    raise SystemExit(1 if args.release and notes else 0)
