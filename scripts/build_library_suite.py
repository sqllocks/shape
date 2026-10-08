"""Write a ``shape-suite`` file that lists every scenario of the starter library.

The nightly workflow runs ``python scripts/build_library_suite.py FILE`` and then
``python -m shape suite run FILE --scale small`` in both kernel modes, so a scenario added to
``src/shape/scenario/library/index.json`` is run there without anyone editing a suite.
``tests/scenario/test_library_nightly.py`` checks that this stays true.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

INDEX = (
    Path(__file__).resolve().parents[1] / "src" / "shape" / "scenario" / "library" / "index.json"
)


def build_suite(index: Path = INDEX) -> dict[str, object]:
    entries = json.loads(index.read_text(encoding="utf-8"))["scenarios"]
    return {
        "format": "shape-suite",
        "version": 1,
        "name": "library",
        "description": "Every scenario of the starter library, from library/index.json.",
        "scenarios": [entry["id"] for entry in entries],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: build_library_suite.py FILE", file=sys.stderr)
        return 2
    Path(argv[0]).write_text(json.dumps(build_suite(), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
