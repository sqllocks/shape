"""Check every authenticated release recording and frozen fixture."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import RECORDED
from recorded_store import check_all, manifest


def main() -> int:
    problems = check_all()
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 2 if not (RECORDED / "MANIFEST.json").exists() else 1
    doc = manifest()
    print(
        f"{len(doc['files'])} files, {sum(e['bytes'] for e in doc['files'].values())} bytes, "
        f"shape_version {doc['shape_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
