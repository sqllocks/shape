from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1]
patterns=[re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),re.compile(r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"][^'\"]{12,}")]
# Key blocks of other kinds count only with key material after the header: code that recognises a
# format names the header alone (src/shape/artifact/keys.py).
extra=[re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----\s*(?:[\w-]+: [^\n]*\n\s*)*[A-Za-z0-9+/=]{40,}"),
    re.compile(r"(?i)\b(?:AccountKey|SharedAccessKey)=[A-Za-z0-9+/]{20,}"),  # Azure Storage, Event Hubs
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}"),  # GitHub tokens
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),  # AWS access key IDs
    re.compile(r"(?i)\bclient[_-]?secret\s*[:=]\s*['\"][^'\"\s]{12,}")]  # Entra app secrets
def _line(text,m):
    start=text.rfind("\n",0,m.start())+1; end=text.find("\n",m.end())
    return text[start:] if end<0 else text[start:end]
bad=[]
for p in ROOT.rglob("*"):
    if not p.is_file() or any(x in p.parts for x in (".git",".venv","dist","build","__pycache__",".pytest_cache")): continue
    if p.suffix.lower() in (".pem",".key",".p12",".pfx"): bad.append(str(p.relative_to(ROOT))); continue
    # Security tests intentionally contain synthetic secret signatures to verify detection.
    if "tests" in p.parts and "security" in p.parts:continue
    if p.stat().st_size<2_000_000:
        text=p.read_text(errors="ignore")
        # an `extra` match on a line marked `nosec` (bandit's marker for deliberate fakes) is allowed
        if any(rx.search(text) for rx in patterns) or any("nosec" not in _line(text,m) for rx in extra for m in rx.finditer(text)): bad.append(str(p.relative_to(ROOT)))
if bad: print("Possible secrets:\n"+"\n".join(bad)); raise SystemExit(1)
print("Basic secret check OK.")
