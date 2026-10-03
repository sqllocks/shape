from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
patterns = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"][^'\"]{12,}"),
]
bad = []
for p in ROOT.rglob("*"):
    if not p.is_file() or any(
        x in p.parts for x in (".git", ".venv", "dist", "build", "__pycache__", ".pytest_cache")
    ):
        continue
    if p.suffix.lower() in (".pem", ".key", ".p12", ".pfx"):
        bad.append(str(p.relative_to(ROOT)))
        continue
    # Security tests intentionally contain synthetic secret signatures to verify detection.
    if "tests" in p.parts and "security" in p.parts:
        continue
    if p.stat().st_size < 2_000_000:
        text = p.read_text(errors="ignore")
        if any(rx.search(text) for rx in patterns):
            bad.append(str(p.relative_to(ROOT)))
if bad:
    print("Possible secrets:\n" + "\n".join(bad))
    raise SystemExit(1)
print("Basic secret check OK.")
