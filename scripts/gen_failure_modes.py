"""Generate ``docs/FAILURE_MODES.md`` from the failure mode catalog (W6-03).

python scripts/gen_failure_modes.py            # write the document
python scripts/gen_failure_modes.py --check    # exit 1 when it is out of date (make check runs this)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / "docs" / "FAILURE_MODES.md"


def render() -> str:
    from shape.scenario.library import catalog

    return catalog.render_markdown(catalog.load_catalog())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--check", action="store_true", help="exit 1 when the document is out of date")
    p.add_argument("--output", type=Path, default=DEFAULT, metavar="FILE")
    a = p.parse_args(argv)
    text = render()
    if a.check:
        have = a.output.read_text(encoding="utf-8") if a.output.is_file() else None
        if have != text:
            print(
                f"{a.output} is out of date: run `python scripts/gen_failure_modes.py`",
                file=sys.stderr,
            )
            return 1
        return 0
    a.output.write_text(text, encoding="utf-8")
    print(f"wrote {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
