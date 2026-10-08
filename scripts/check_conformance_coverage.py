"""Count the normative statements of docs/specs/SHAPE_2.md and assert one conformance test each.

    python scripts/check_conformance_coverage.py [--write]

Exits 1 when a statement has no test, a test has no statement, a normative line has no
identifier, or ``src/shape/validation/statements.json`` (the copy packaged so that an installed
``shape conformance`` can do the same check) differs from the specification. ``--write``
refreshes that copy first.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shape.validation import requirements  # noqa: E402

PACKAGED = ROOT / "src" / "shape" / "validation" / "statements.json"


def main(argv: list[str]) -> int:
    spec = requirements.parse_statements(
        (ROOT / "docs" / "specs" / "SHAPE_2.md").read_text(encoding="utf-8")
    )
    if "--write" in argv:
        PACKAGED.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
    problems = requirements.coverage_problems()
    if json.loads(PACKAGED.read_text(encoding="utf-8")) != spec:
        problems.append("statements.json is out of date; run with --write")
    print(f"{len(spec)} normative statements, {len(requirements.REQUIREMENTS)} conformance tests")
    for p in problems:
        print("PROBLEM:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
