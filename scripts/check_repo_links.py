"""Check built-site links to this repository's main branch without network access."""

from __future__ import annotations

import argparse
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = (
    "https://github.com/sqllocks/shape/blob/main/",
    "https://github.com/sqllocks/shape/tree/main/",
)


class Links(HTMLParser):
    """Collect the URL attributes lychee checks from an HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"href", "src"} and value is not None:
                self.urls.append(value)


def check_site(site: Path, root: Path) -> list[str]:
    """Report missing repository paths, including links on the site's 404 page."""
    root = root.resolve()
    problems: list[str] = []
    for page in sorted(site.rglob("*.html")):
        parser = Links()
        parser.feed(page.read_text(encoding="utf-8"))
        for url in parser.urls:
            if not url.startswith(PREFIXES):
                continue
            path = unquote(urlsplit(url).path.split("/main/", 1)[1])
            dest = (root / path).resolve()
            if not dest.is_relative_to(root) or not dest.exists():
                problems.append(f"{page.relative_to(site)}: repository path does not exist: {url}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True, help="built MkDocs site directory")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository checkout")
    args = parser.parse_args(argv)
    if not args.site.is_dir() or not args.root.is_dir():
        print("check_repo_links: site and checkout must be existing directories", file=sys.stderr)
        return 2
    problems = check_site(args.site, args.root)
    for problem in problems:
        print(f"broken link: {problem}")
    print(f"check_repo_links: {len(problems)} broken repository link(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
