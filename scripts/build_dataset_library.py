"""Build the profile of one public dataset for the dataset library (W6-03).

    python scripts/build_dataset_library.py NAME [--refresh] [--from-file FILE] [--retrieved DATE]

This is the only place that touches the network: it downloads the dataset's source file,
checks its SHA-256 against the one recorded in ``src/shape/library/datasets/index.json`` (a first
build records it), profiles the table with the default safe capture, writes ``NAME.shape`` and
updates the index entry. No source data is written anywhere in the repository.

A source that no longer matches its recorded checksum is an error (exit 1); ``--refresh`` accepts
the new file and records its checksum and the retrieval date. ``--from-file`` reads a copy you
already have instead of downloading it (the checksum is still checked). Exit 2 for an unknown
name or an unusable source.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "src" / "shape" / "library" / "datasets"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
UCI = "https://archive.ics.uci.edu"

#: The datasets of the library: where each comes from and under which licence. A dataset joins only
#: under a licence of ``shape.library.ALLOWED_LICENSES``.
SOURCES: dict[str, dict[str, str]] = {
    "palmer-penguins": {
        "title": "Palmer penguins",
        "url": "https://raw.githubusercontent.com/allisonhorst/palmerpenguins/main/inst/extdata/penguins.csv",
        "license": "CC0-1.0",
        "attribution": "Horst AM, Hill AP, Gorman KB (2020). palmerpenguins: Palmer Archipelago "
        "(Antarctica) penguin data. R package version 0.1.1; data collected by Kristen Gorman "
        "and the Palmer Station LTER, released under CC0 "
        "(https://allisonhorst.github.io/palmerpenguins/).",
    },
    "iris": {
        "title": "Iris",
        "url": f"{UCI}/static/public/53/data.csv",
        "license": "CC-BY-4.0",
        "attribution": "Fisher, R. A. (1936). Iris. UCI Machine Learning Repository. "
        "https://doi.org/10.24432/C56C76. Licensed under CC BY 4.0.",
    },
    "wine": {
        "title": "Wine",
        "url": f"{UCI}/static/public/109/data.csv",
        "license": "CC-BY-4.0",
        "attribution": "Aeberhard, S. and Forina, M. (1992). Wine. UCI Machine Learning "
        "Repository. https://doi.org/10.24432/C5PC7J. Licensed under CC BY 4.0.",
    },
    "abalone": {
        "title": "Abalone",
        "url": f"{UCI}/static/public/1/data.csv",
        "license": "CC-BY-4.0",
        "attribution": "Nash, W., Sellers, T., Talbot, S., Cawthorn, A. and Ford, W. (1994). "
        "Abalone. UCI Machine Learning Repository. https://doi.org/10.24432/C55C7W. Licensed "
        "under CC BY 4.0.",
    },
    "adult-income": {
        "title": "Adult (census income)",
        "url": f"{UCI}/static/public/2/data.csv",
        "license": "CC-BY-4.0",
        "attribution": "Becker, B. and Kohavi, R. (1996). Adult. UCI Machine Learning Repository. "
        "https://doi.org/10.24432/C5XW20. Licensed under CC BY 4.0.",
    },
    "breast-cancer-wisconsin": {
        "title": "Breast Cancer Wisconsin (Diagnostic)",
        "url": f"{UCI}/static/public/17/data.csv",
        "license": "CC-BY-4.0",
        "attribution": "Wolberg, W., Mangasarian, O., Street, N. and Street, W. (1993). Breast "
        "Cancer Wisconsin (Diagnostic). UCI Machine Learning Repository. "
        "https://doi.org/10.24432/C5DW2B. Licensed under CC BY 4.0.",
    },
}


class BuildError(Exception):
    """The source cannot be used (exit 2) or no longer matches its checksum (exit 1)."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def download(url: str) -> bytes:
    """The bytes at ``url`` (https only, at most :data:`MAX_SOURCE_BYTES`)."""
    if not url.startswith("https://"):
        raise BuildError(f"only https sources are downloaded: {url}")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 (https checked)
            data: bytes = response.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise BuildError(f"cannot download {url}: {exc}") from None
    if len(data) > MAX_SOURCE_BYTES:
        raise BuildError(f"{url} is larger than {MAX_SOURCE_BYTES} bytes")
    return data


def read_table(data: bytes, name: str) -> Any:
    """The source as an Arrow table (a CSV with a header row)."""
    import pyarrow.csv as pacsv  # type: ignore[import-untyped]

    try:
        return pacsv.read_csv(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - any parse error is "the source is not a CSV"
        raise BuildError(f"the source of {name} is not a readable CSV: {exc}") from None


def build(
    name: str,
    *,
    refresh: bool = False,
    from_file: Path | None = None,
    retrieved: str | None = None,
    library: Path = LIBRARY,
    sources: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build the profile of ``name`` and update the index; return the index entry."""
    import shape
    from shape.library import MAX_PROFILE_BYTES, load_index
    from shape.profile.reference.profile import save

    spec = (sources or SOURCES).get(name)
    if spec is None:
        raise BuildError(f"unknown dataset {name!r}; the sources are: {', '.join(sorted(SOURCES))}")
    index_file = library / "index.json"
    entries = load_index(library) if index_file.is_file() else []
    old = next((e for e in entries if e["name"] == name), None)
    data = from_file.read_bytes() if from_file is not None else download(spec["url"])
    digest = hashlib.sha256(data).hexdigest()
    if old is not None and old["source_sha256"] != digest and not refresh:
        raise BuildError(
            f"the source of {name} has changed: its SHA-256 is {digest}, the index records "
            f"{old['source_sha256']}. Look at what changed, then rebuild with --refresh to "
            f"accept it",
            code=1,
        )
    table = read_table(data, name)
    profile = shape.profile(table, name=name)
    target = library / f"{name}.shape"
    library.mkdir(parents=True, exist_ok=True)
    save(profile, target, capture="safe")
    size = target.stat().st_size
    if size > MAX_PROFILE_BYTES:
        target.unlink()
        raise BuildError(f"the profile of {name} is {size} bytes; the limit is {MAX_PROFILE_BYTES}")
    entry = {
        "name": name,
        "title": spec["title"],
        "source_url": spec["url"],
        "license": spec["license"],
        "attribution": spec["attribution"],
        "retrieved": retrieved
        or (old["retrieved"] if old is not None and old["source_sha256"] == digest else None)
        or dt.date.today().isoformat(),
        "source_sha256": digest,
        "rows": table.num_rows,
        "profile": target.name,
    }
    entries = sorted([*[e for e in entries if e["name"] != name], entry], key=lambda e: e["name"])
    doc = {"format": "shape-dataset-library", "version": 1, "datasets": entries}
    index_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return entry


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("name", help=f"one of: {', '.join(sorted(SOURCES))}")
    p.add_argument("--refresh", action="store_true", help="accept a source whose checksum changed")
    p.add_argument("--from-file", type=Path, metavar="FILE", help="read this copy, do not download")
    p.add_argument("--retrieved", metavar="DATE", help="the retrieval date to record (ISO)")
    a = p.parse_args(argv)
    try:
        if a.retrieved:
            dt.date.fromisoformat(a.retrieved)
        entry = build(a.name, refresh=a.refresh, from_file=a.from_file, retrieved=a.retrieved)
    except ValueError as exc:
        print(f"build_dataset_library: {exc}", file=sys.stderr)
        return 2
    except BuildError as exc:
        print(f"build_dataset_library: {exc}", file=sys.stderr)
        return exc.code
    print(f"{entry['name']}: {entry['rows']} rows, sha256 {entry['source_sha256'][:12]}..., "
          f"profile {entry['profile']}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
