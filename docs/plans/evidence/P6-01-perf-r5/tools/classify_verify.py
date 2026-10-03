"""Every cell's verifier findings on this tree against round 4's, with the new ones classified.
usage: classify_verify.py  (from the repo root)"""

import re
from pathlib import Path

R5 = Path("docs/plans/evidence/P6-01-perf-r5/verify")
R4 = Path("docs/plans/evidence/P6-01-perf-r4/verify")
IDENT = re.compile(r"\.(\w*email|\w*phone|page_url|destination|ssn)\b")


def findings(path: Path) -> set[str]:
    if not path.exists():
        return set()
    text = path.read_bytes().replace(b"\0", b"").decode("utf-8", "replace")
    return {m.strip() for m in re.findall(r"NOT EQUIVALENT: (.*)", text)}


codes = dict(
    (" ".join(line.split()[:2]), line.split()[-1])
    for line in (R5 / "exit_codes.txt").read_text().splitlines()
)
for cell, code in codes.items():
    d, s = cell.split()
    name = f"verify_shape_{d}_{s}.txt"
    new, old = findings(R5 / name), findings(R4 / name)
    added, gone = sorted(new - old), sorted(old - new)
    ident = [f for f in added if IDENT.search(f)]
    other = [f for f in added if not IDENT.search(f)]
    print(f"{d} {s}: exit {code}; as round 4: {len(new & old)}; new identifier: {len(ident)}; "
          f"new other: {other}; gone: {gone}")
