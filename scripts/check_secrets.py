"""Exit 1 when a file that can be committed holds a private key or a credential.

    python scripts/check_secrets.py

Scanned: in a git checkout, the tracked files and the untracked files git does not ignore (what
``git add .`` would commit), so a virtualenv, a build tree or an ignored ``.env`` is never read;
outside one, every file below the repository root except caches and build output. Reported: key
files by suffix (``.pem``, ``.key``, ``.p12``, ``.pfx``), private key blocks, quoted
``api_key``/``secret_key``/``access_token``/``client_secret`` values, Azure Storage
``AccountKey=`` and Event Hubs/Service Bus ``SharedAccessKey=`` values, GitHub tokens and AWS
access key ids. A line marked ``# nosec`` or ``pragma: allowlist secret`` is a deliberate fake,
except for an RSA, EC or OpenSSH private key header and a quoted ``api_key``, ``secret_key`` or
``access_token`` value, which take no exemption; a ``security`` folder under ``tests`` is exempt:
its tests hold synthetic secrets on purpose.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
SKIP_PARTS = {".git", ".venv", "dist", "build", "target", "__pycache__", ".pytest_cache"}
MAX_BYTES = 2_000_000
# Each pattern is linear: no nested or adjacent unbounded quantifiers.
# These two take no exemption: a line marked as a fake still reports them.
STRICT = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"][^'\"]{12,}"),
]
PATTERNS = [
    # these headers alone are format probes in code; a key has its base64 body after them
    re.compile(
        r"-----BEGIN (?:ENCRYPTED |DSA |PGP )PRIVATE KEY(?: BLOCK)?-----"
        r"(?:\r?\n|\\n)[\w :,-]{0,80}(?:\r?\n|\\n){0,3}[A-Za-z0-9+/]{40}"
    ),
    re.compile(r"(?i)\bclient[_-]?secret\s*[:=]\s*['\"][^'\"\s]{12,}"),
    re.compile(r"(?i)\b(?:AccountKey|SharedAccessKey)=[A-Za-z0-9+/]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
]
# A deliberate fake on one line (a test double, a contract fixture) carries one of these.
ALLOW = ("pragma: allowlist secret", "nosec")


def candidate_files(root: Path = ROOT) -> list[Path]:
    """Files that could be committed: git's view when ``root`` is a checkout, else a walk."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others"]
            + ["--exclude-standard"],
            capture_output=True,
            check=False,
        )
    except OSError:
        out = None
    if out is not None and out.returncode == 0 and (root / ".git").exists():
        names = [n for n in out.stdout.decode("utf-8", "surrogateescape").split("\0") if n]
        return sorted(p for p in (root / n for n in names) if p.is_file())
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and not SKIP_PARTS & set(p.relative_to(root).parts)
    )


def _secret_lines(text: str) -> list[int]:
    """Line numbers of the matches that are not marked as deliberate fakes."""
    out: list[int] = []
    lines = text.split("\n")
    for rx in STRICT:
        out += [text.count("\n", 0, m.start()) + 1 for m in rx.finditer(text)]
    for rx in PATTERNS:
        for m in rx.finditer(text):
            n = text.count("\n", 0, m.start())
            if not any(a in lines[n] for a in ALLOW):
                out.append(n + 1)
    return sorted(set(out))


def findings(root: Path = ROOT) -> list[str]:
    bad: list[str] = []
    for p in candidate_files(root):
        rel = p.relative_to(root)
        if p.suffix.lower() in KEY_SUFFIXES:
            bad.append(rel.as_posix())
            continue
        # security tests hold synthetic secret signatures on purpose, to test detection
        if "tests" in rel.parts and "security" in rel.parts:
            continue
        if p.stat().st_size < MAX_BYTES:
            hits = _secret_lines(p.read_bytes().decode("utf-8", "ignore"))
            bad += [f"{rel.as_posix()}:{n}" for n in hits]
    return bad


def main() -> int:
    bad = findings()
    if bad:
        print("Possible secrets:\n" + "\n".join(bad))
        return 1
    print("Basic secret check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
