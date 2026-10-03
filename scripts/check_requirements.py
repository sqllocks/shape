from pathlib import Path
import re, sys, yaml

ROOT = Path(__file__).resolve().parents[1]
data = yaml.safe_load((ROOT / "docs/specs/requirements.yaml").read_text())
ids = [r["id"] for r in data["requirements"]]
bad = []
if len(ids) != len(set(ids)):
    bad.append("duplicate requirement IDs")
known = set(ids)
rx = re.compile(r"\bSHAPE-[A-Z]+-\d{3}\b")
for p in list((ROOT / "tests").rglob("*.py")) + list((ROOT / "docs").rglob("*.md")):
    for ref in rx.findall(p.read_text(errors="replace")):
        if ref not in known:
            bad.append(f"{p.relative_to(ROOT)}: unknown {ref}")
if bad:
    print("\n".join(bad))
    raise SystemExit(1)
print(f"Requirement registry OK: {len(ids)} requirements.")
