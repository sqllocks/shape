"""``_shape_provenance.json``: the record that a folder of table files was written by Shape.

``shape generate -o DIR``, ``shape continue``, ``shape pack run`` and ``shape chaos`` write the
sidecar next to the table files (the files and their bytes are unchanged). It lists every file
with its SHA-256 and row count, so ``shape chaos --input DIR`` can tell Shape-generated data from
a folder of someone's real extracts (``docs/CHAOS.md``). The check is provenance only: it stops a
mistake, not a determined user (``docs/THREAT_MODEL.md``).

Format (``format: "shape-provenance"``, integer ``version``, currently 1)::

    {"format": "shape-provenance", "version": 1, "shape_version": "0.9.0", "seed": 42,
     "domain": "retail", "scale": "small", "spec_hash": null,
     "files": [{"path": "orders.csv", "sha256": "...", "rows": 1000}]}

``path`` is relative to the folder and uses ``/``; ``rows`` is an integer, or ``null`` when the
writer did not know it. Writing into a folder that already holds a sidecar adds to it (a file
written again replaces its entry). Folder readers skip :data:`IGNORED_FILES`.
Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

PROVENANCE_FILE = "_shape_provenance.json"
FORMAT = "shape-provenance"
VERSION = 1
#: Files a folder reader never treats as a table.
IGNORED_FILES = frozenset({PROVENANCE_FILE, "_SUCCESS"})
SYNTHETIC_KEY = b"shape_synthetic"

VERIFIED = "verified"
CHANGED = "changed"  # listed, but the bytes differ from the record
UNMARKED = "unmarked"  # not listed and not a marked Parquet file


def is_ignored(path: str | Path) -> bool:
    """True for a file a folder reader skips (the sidecar and ``_SUCCESS``)."""
    return Path(path).name in IGNORED_FILES


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_header(doc: Any, where: str) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ValueError(f"{where} is not a Shape provenance file (format is not {FORMAT!r})")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError(f"{where} has no integer version")
    if version > VERSION:
        raise ValueError(
            f"{where} is provenance version {version}, written by a newer Shape; this Shape "
            f"reads up to version {VERSION}"
        )
    if not isinstance(doc.get("files"), list):
        raise ValueError(f"{where} has no files list")
    return doc


def read_provenance(folder: str | Path) -> dict[str, Any] | None:
    """The sidecar of ``folder``, or ``None`` when it has none. A file that is not a provenance
    record, or one written by a newer Shape, is a ``ValueError``."""
    path = Path(folder) / PROVENANCE_FILE
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} is not readable provenance: {exc}") from exc
    return _check_header(doc, str(path))


def write_provenance(
    folder: str | Path,
    files: Iterable[tuple[str | Path, int | None]],
    *,
    seed: int | None,
    domain: str | None = None,
    scale: str | None = None,
    spec_hash: str | None = None,
) -> Path | None:
    """Record ``files`` (``(path, rows)`` pairs, each inside ``folder``) in the folder's sidecar.
    Directories, missing files and files outside the folder are left out; a folder that has no
    file to record gets no sidecar (``None``)."""
    import shape

    root = Path(folder)
    existing = None
    try:
        existing = read_provenance(root)
    except ValueError:
        existing = None  # an unreadable record is replaced by a fresh one
    entries: dict[str, dict[str, Any]] = {}
    if existing is not None:
        entries = {str(e["path"]): e for e in existing["files"] if isinstance(e, dict)}
    base = root.resolve()
    for item, rows in files:
        path = Path(item)
        if not path.is_file() or is_ignored(path):
            continue
        try:
            rel = path.resolve().relative_to(base).as_posix()
        except ValueError:
            continue
        entries[rel] = {"path": rel, "sha256": sha256_file(path), "rows": rows}
    if not entries:
        return None
    doc: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "shape_version": shape.__version__,
        "seed": seed,
        "domain": domain,
        "scale": scale,
        "spec_hash": spec_hash,
        "files": [entries[k] for k in sorted(entries)],
    }
    root.mkdir(parents=True, exist_ok=True)
    target = root / PROVENANCE_FILE
    temp = root / f".{PROVENANCE_FILE}.{os.getpid()}.tmp"
    temp.write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    os.replace(temp, target)
    return target


def record_tables(
    folder: str | Path,
    paths: Iterable[str | Path],
    rows: Mapping[str, int] | None = None,
    **header: Any,
) -> Path | None:
    """:func:`write_provenance` for files named after their table (``orders.csv`` is ``orders``);
    ``rows`` maps the table name to its row count. ``header`` is ``seed``, ``domain``, ``scale``
    and ``spec_hash``."""
    counts = rows or {}
    pairs = [(p, counts.get(Path(p).stem)) for p in paths]
    header.setdefault("seed", None)
    return write_provenance(folder, pairs, **header)


def has_synthetic_marker(path: str | Path) -> bool:
    """True for a Parquet file whose schema metadata carries ``shape_synthetic``."""
    if Path(path).suffix.lower() not in (".parquet", ".pq"):
        return False
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        metadata = pq.read_schema(path).metadata or {}
    except Exception:  # not Parquet after all: not marked
        return False
    return SYNTHETIC_KEY in metadata


def verify_file(path: str | Path) -> str:
    """:data:`VERIFIED` when ``path`` is listed with a matching SHA-256 in its folder's sidecar or
    is a Parquet file with the ``shape_synthetic`` marker; :data:`CHANGED` when it is listed but
    its bytes differ; :data:`UNMARKED` otherwise."""
    file = Path(path)
    record = read_provenance(file.parent)
    listed = False
    if record is not None:
        for entry in record["files"]:
            if isinstance(entry, dict) and entry.get("path") == file.name:
                listed = True
                if entry.get("sha256") == sha256_file(file):
                    return VERIFIED
    if has_synthetic_marker(file):
        return VERIFIED
    return CHANGED if listed else UNMARKED


__all__ = [
    "CHANGED",
    "FORMAT",
    "IGNORED_FILES",
    "PROVENANCE_FILE",
    "UNMARKED",
    "VERIFIED",
    "VERSION",
    "has_synthetic_marker",
    "is_ignored",
    "read_provenance",
    "record_tables",
    "sha256_file",
    "verify_file",
    "write_provenance",
]
