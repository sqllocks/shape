"""Link check for the documentation (P8-03, T-24). Offline and deterministic.

    python scripts/check_doc_links.py                 # the Markdown sources
    mkdocs build --strict && python scripts/check_doc_links.py --site site

**Sources** (always): every relative link in ``README.md`` and in the site's pages (``docs/``
without ``plans/`` and ``talks/``) must name a file or folder that exists in the repository, so the
docs work when browsing the repository too. The two generated pages (``GENERATED``) count as
existing.

**Built site** (``--site DIR``): every ``href`` and ``src`` in every HTML page must resolve to a
file of the built site, and a ``#fragment`` must be an ``id`` on the target page. A link to this
repository's own files (``REPO_URL/blob/main/...`` or ``/tree/main/...``, which the site uses for
files outside it) must name a path that exists in the checkout. Other external links are not
fetched: the check needs no network.

Exit codes: 0 every link resolves, 1 a broken link (each one is printed), 2 usage error.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/sqllocks/shape"
SKIP_DOCS = ("plans", "talks")
# Pages that scripts/mkdocs_hooks.py generates at build time (they have no source file).
GENERATED = ("docs/reference/cli.md", "docs/reference/performance.md")
_LINK = re.compile(r"\]\((<[^>]+>|[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_REF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s+(\S+)")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def source_files(root: Path = ROOT) -> list[Path]:
    docs = root / "docs"
    found = [root / "README.md"] if (root / "README.md").is_file() else []
    if docs.is_dir():
        found += sorted(
            p for p in docs.rglob("*.md") if p.relative_to(docs).parts[0] not in SKIP_DOCS
        )
    return found


def markdown_links(text: str) -> list[tuple[int, str]]:
    """(line number, target) of every inline and reference link outside fenced code."""
    out: list[tuple[int, str]] = []
    fenced = False
    for n, line in enumerate(text.split("\n"), 1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        for m in _LINK.finditer(line):
            t = m.group(1)
            out.append((n, t[1:-1] if t.startswith("<") else t))
        ref = _REF.match(line)
        if ref:
            out.append((n, ref.group(1)))
    return out


def _generated(dest: Path, root: Path) -> bool:
    d, r = dest.resolve(), root.resolve()
    return d.is_relative_to(r) and d.relative_to(r).as_posix() in GENERATED


def check_sources(root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    for f in source_files(root):
        rel = f.relative_to(root).as_posix()
        for n, target in markdown_links(f.read_text(encoding="utf-8")):
            if target.startswith("#") or _SCHEME.match(target):
                continue
            path = unquote(target.partition("#")[0].partition("?")[0])
            if not path:
                continue
            dest = (root / path.lstrip("/")) if path.startswith("/") else (f.parent / path)
            if not dest.exists() and not _generated(dest, root):
                problems.append(f"{rel}:{n}: link target does not exist: {target}")
    return problems


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        for key in ("id", "name"):
            v = a.get(key)
            if v and (key == "id" or tag == "a"):
                self.ids.add(v)
        for key in ("href", "src"):
            v = a.get(key)
            if v is not None and not (tag == "link" and a.get("rel") in ("canonical",)):
                self.links.append(v)


def _parse(path: Path) -> _Page:
    p = _Page()
    p.feed(path.read_text(encoding="utf-8", errors="replace"))
    return p


def _site_target(site: Path, page: Path, path: str) -> Path | None:
    base = page.parent.relative_to(site).as_posix()
    joined = (
        posixpath.normpath(posixpath.join(base, path))
        if path
        else page.relative_to(site).as_posix()
    )
    if joined.startswith("../") or joined == "..":
        return None
    dest = site / joined
    if dest.is_dir() or path.endswith("/"):
        dest = dest / "index.html"
    return dest


def check_site(site: Path, root: Path = ROOT, repo_url: str = REPO_URL) -> list[str]:
    problems: list[str] = []
    pages = sorted(p for p in site.rglob("*.html") if p.name != "404.html")
    cache: dict[Path, _Page] = {}

    def parsed(p: Path) -> _Page:
        if p not in cache:
            cache[p] = _parse(p)
        return cache[p]

    prefixes = (f"{repo_url}/blob/main/", f"{repo_url}/tree/main/")
    for page in pages:
        rel = page.relative_to(site).as_posix()
        for link in parsed(page).links:
            if link.startswith(prefixes):
                repo_path = unquote(urlsplit(link).path.split("/main/", 1)[1])
                if not (root / repo_path).exists():
                    problems.append(f"{rel}: repository path does not exist: {link}")
                continue
            if _SCHEME.match(link) or link.startswith("//"):
                continue
            parts = urlsplit(link)
            dest = _site_target(site, page, unquote(parts.path))
            if dest is None or not dest.exists():
                problems.append(f"{rel}: link target is not in the site: {link}")
                continue
            if parts.fragment and dest.suffix == ".html":
                if unquote(parts.fragment) not in parsed(dest).ids:
                    problems.append(f"{rel}: no anchor #{parts.fragment} on the target: {link}")
    return problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--site", type=Path, help="also check a built site (mkdocs build output)")
    ap.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.site is not None and not a.site.is_dir():
        print(f"check_doc_links: no built site at {a.site}", file=sys.stderr)
        return 2
    problems = check_sources(a.root)
    if a.site is not None:
        problems += check_site(a.site, a.root)
    for line in problems:
        print(f"broken link: {line}")
    checked = "sources" + (" and built site" if a.site is not None else "")
    print(f"check_doc_links: {len(problems)} broken link(s) in the {checked}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
