"""Check public documentation wording without printing disallowed terms."""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Claims only: the required data-minimisation wording is allowed.
CLAIMS = re.compile(
    r"\b(?:anonymous|anonymized|anonymised|anonymization|anonymity|"
    r"HIPAA[- ]compliant|GDPR[- ]compliant|PII[- ]free)\b",
    re.I,
)
PRODUCT = re.compile(
    r"\b(?:Shape Studio|Shape Hub|faster than|baseline library it replaces)\b"
    r"|benchmarks/vs_refengine",
    re.I,
)


def check(paths: list[Path]) -> list[str]:
    """Return filenames and line numbers of forbidden claims, without their text."""
    failures = []
    for path in paths:
        for index, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if CLAIMS.search(line) or PRODUCT.search(line):
                failures.append(
                    f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}:{index}"
                )
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path)
    args = parser.parse_args()
    paths = [
        p
        for p in (ROOT / "docs").rglob("*.md")
        if not {"plans", "talks"}.intersection(p.relative_to(ROOT / "docs").parts)
    ]
    paths += [
        ROOT / name
        for name in (
            "README.md",
            "CONTRIBUTING.md",
            "GOVERNANCE.md",
            "SECURITY.md",
            "CHANGELOG.md",
            "THIRD_PARTY_NOTICES.md",
        )
    ]
    if args.site:
        paths += list(args.site.rglob("*.html")) + list(args.site.glob("llms*.txt"))
    failures = check(paths)
    for failure in failures:
        print("Documentation wording:", failure)
    print(f"Documentation wording: {len(failures)} hits")
    sys.exit(bool(failures))
