"""Check or regenerate the docs-only pip compatibility export from uv.lock."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = "# Generated from uv.lock; run uv run python scripts/check_docs_requirements.py --write.\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--only-group",
            "docs",
            "--no-emit-workspace",
            "--no-hashes",
            "--no-header",
            "--no-annotate",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    expected = HEADER + result.stdout
    path = ROOT / "requirements-docs.txt"
    if args.write:
        path.write_text(expected)
    elif path.read_text() != expected:
        print(
            "requirements-docs.txt differs from uv.lock; regenerate with --write", file=sys.stderr
        )
        return 1
    print("Docs requirements agree with uv.lock.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
