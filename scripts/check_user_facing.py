"""Exit 1 when the reference engine's name (any case; checked by hash, ``refengine_name.py``)
appears on Shape's user-facing surface (P1-14).

Checked: ``src/``, ``rust/``, ``plugins/``, ``integrations/``, ``demo/``, ``README.md``,
``pyproject.toml`` and ``docs/`` outside ``docs/plans/`` (the talk included, owner 2026-09-30);
with ``--wheel PATH`` (repeatable) also every file inside a built wheel or sdist; with
``--site DIR`` (repeatable) also every file of a built documentation site (``mkdocs build``), so
the pages generated at build time are covered. ``THIRD_PARTY_NOTICES.md`` is not scanned: it
carries the attribution the licence requires.
"""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREES = ("src", "rust", "plugins", "integrations", "demo")
FILES = ("README.md", "pyproject.toml")
SKIP_DOCS = {"plans"}
SKIP_PARTS = {"__pycache__", "target", ".mypy_cache", ".pytest_cache"}
sys.path.insert(0, str(Path(__file__).resolve().parent))
from refengine_name import names_refengine  # noqa: E402


def hits(label: str, data: bytes) -> list[str]:
    low = data.lower()
    if not names_refengine(low):
        return []
    out = []
    for n, line in enumerate(low.split(b"\n"), 1):
        if names_refengine(line):
            out.append(f"{label}:{n}")
    return out or [label]


def tree_files() -> list[Path]:
    found: list[Path] = []
    for t in TREES:
        base = ROOT / t
        if base.is_dir():
            found += [p for p in base.rglob("*") if p.is_file()]
    found += [ROOT / f for f in FILES if (ROOT / f).is_file()]
    docs = ROOT / "docs"
    if docs.is_dir():
        found += [
            p
            for p in docs.rglob("*")
            if p.is_file() and p.relative_to(docs).parts[0] not in SKIP_DOCS
        ]
    return [p for p in found if not SKIP_PARTS & set(p.parts) and p.suffix != ".so"]


def archive_members(path: Path) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    if path.suffix in (".whl", ".zip"):
        with zipfile.ZipFile(path) as z:
            out += [(f"{path.name}!{n}", z.read(n)) for n in z.namelist() if not n.endswith("/")]
    else:
        with tarfile.open(path) as t:
            for m in t.getmembers():
                f = t.extractfile(m) if m.isfile() else None
                if f is not None:
                    out.append((f"{path.name}!{m.name}", f.read()))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wheel", action="append", default=[], type=Path)
    ap.add_argument("--site", action="append", default=[], type=Path)
    a = ap.parse_args(argv)
    for w in a.wheel:
        if not w.is_file():
            ap.error(f"no such archive: {w} (was the wheel or sdist built?)")
    found: list[str] = []
    for p in tree_files():
        found += hits(str(p.relative_to(ROOT)), p.read_bytes())
    for w in a.wheel:
        for label, data in archive_members(w):
            found += hits(label, data)
    for site in a.site:
        for p in sorted(q for q in site.rglob("*") if q.is_file()):
            found += hits(f"{site.name}!{p.relative_to(site).as_posix()}", p.read_bytes())
    for line in found:
        print(f"user-facing mention of the baseline library: {line}")
    if not found:
        print("check_user_facing: clean")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
