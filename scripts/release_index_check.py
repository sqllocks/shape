"""Check that an index serves exactly the archives a release built and attested (P8-04, T-25).

``scripts/release_sbom.py build`` writes ``SHA256SUMS`` for every archive of a release, and the
release workflow attests those archives. After the upload, this script asks the index's JSON API
(``/pypi/<project>/<version>/json``) for every project of the release and checks that:

* every archive in ``SHA256SUMS`` is on the index with that SHA-256 (none missing, none changed);
* the index holds no other file for that version of those projects.

The publish step skips files that already exist, so a publish job that failed half-way can be
re-run; this check is what makes that safe: a file that was already there must be the one built.

    python scripts/release_index_check.py SHA256SUMS --index testpypi
    python scripts/release_index_check.py SHA256SUMS --index pypi

An index can lag an upload by a few minutes, so the check is retried (``--attempts``).
Exit status: 0 when the index matches, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

JSON_API = {"pypi": "https://pypi.org/pypi", "testpypi": "https://test.pypi.org/pypi"}
ARCHIVE_RE = re.compile(
    r"^(?P<stem>[A-Za-z0-9_.]+)-(?P<version>[^-]+?)(?:-[^-]+-[^-]+-[^-]+\.whl|\.tar\.gz)$"
)

Fetch = Callable[[str], dict[str, Any]]


def parse_sums(text: str) -> dict[str, str]:
    """``{file name: sha256}`` from ``sha256sum`` output."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        m = re.fullmatch(r"([0-9a-f]{64}) [ *](\S+)", line.strip())
        if m is None:
            raise ValueError(f"not a sha256sum line: {line!r}")
        sums[m[2]] = m[1]
    return sums


def projects(sums: dict[str, str]) -> dict[str, str]:
    """``{project: version}`` of the archives (one version per release)."""
    out: dict[str, str] = {}
    for name in sums:
        m = ARCHIVE_RE.match(name)
        if m is None:
            raise ValueError(f"{name}: not a wheel or sdist file name")
        out[m["stem"].replace("_", "-").lower()] = m["version"]
    return out


def fetch_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed https hosts)
        data: dict[str, Any] = json.load(resp)
    return data


def index_problems(sums: dict[str, str], index: str, fetch: Fetch = fetch_json) -> list[str]:
    problems: list[str] = []
    served: dict[str, str] = {}
    for project, version in sorted(projects(sums).items()):
        url = f"{JSON_API[index]}/{project}/{version}/json"
        try:
            data = fetch(url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            problems.append(f"{project} {version}: not on {index} ({exc})")
            continue
        for f in data.get("urls", []):
            served[f["filename"]] = f.get("digests", {}).get("sha256", "")
    for name, digest in sorted(sums.items()):
        if name not in served:
            problems.append(f"{name}: not on {index}")
        elif served[name] != digest:
            problems.append(
                f"{name}: {index} serves sha256 {served[name]}, the release built {digest}"
            )
    problems += [
        f"{name}: on {index} but not built by this release"
        for name in sorted(set(served) - set(sums))
    ]
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("sums", type=Path, help="the release's SHA256SUMS")
    p.add_argument("--index", choices=sorted(JSON_API), required=True)
    p.add_argument("--attempts", type=int, default=5)
    ns = p.parse_args(argv)
    sums = parse_sums(ns.sums.read_text(encoding="utf-8"))
    problems: list[str] = [f"{ns.sums}: lists no archives"] if not sums else []
    for attempt in range(1, max(1, ns.attempts) + 1):
        if not sums:
            break
        problems = index_problems(sums, ns.index)
        if not problems or attempt == ns.attempts:
            break
        time.sleep(30 * attempt)  # a fresh upload can take minutes to reach the JSON API
    for line in problems:
        print(f"FAIL {line}", file=sys.stderr)
    if not problems:
        print(f"{ns.index} serves exactly the {len(sums)} archives this release built")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
