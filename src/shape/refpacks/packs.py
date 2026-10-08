"""The reference pack format, its validation and its discovery (W3-12).

``pack.json``::

    {"format": "shape-reference-pack", "version": 1, "name", "pack_version", "source",
     "retrieved", "license", "attribution", "transformation_version", "sensitivity",
     "datasets": [{"name", "file", "fields", "rows", "sha256"}]}

Every data file is an Arrow IPC file whose SHA-256 is in the manifest; a file that does not match
is refused (``reference pack NAME: FILE does not match its checksum``) before any of it is read.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

PACK_FORMAT = "shape-reference-pack"
PACK_VERSION = 1
MANIFEST = "pack.json"

#: The plugin group whose installed packages may ship reference packs, each in its
#: ``reference_packs`` directory (core names no plugin package).
SHIPPED_GROUP = "shape.domains"
SHIPPED_DIR = "reference_packs"

_PACK_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_DATASET_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
_FILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}\.arrow")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_KEYS = (
    "format",
    "version",
    "name",
    "pack_version",
    "source",
    "retrieved",
    "license",
    "attribution",
    "transformation_version",
    "sensitivity",
    "datasets",
)
_TEXT_KEYS = (
    "pack_version",
    "source",
    "retrieved",
    "license",
    "attribution",
    "transformation_version",
    "sensitivity",
)
_DATASET_KEYS = ("name", "file", "fields", "rows", "sha256")


class ReferencePackError(ShapeError, ValueError):
    """A pack is malformed, or its data does not match its manifest."""


class UnknownPackError(ReferencePackError, LookupError):
    """No pack has the name asked for."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_dataset(pack: str, entry: Any) -> None:
    if not isinstance(entry, dict):
        raise ReferencePackError(f"reference pack {pack}: each dataset is an object")
    unknown = sorted(set(entry) - set(_DATASET_KEYS))
    missing = [k for k in _DATASET_KEYS if k not in entry]
    if missing or unknown:
        raise ReferencePackError(
            f"reference pack {pack}: a dataset needs {list(_DATASET_KEYS)} "
            f"(missing {missing}, unexpected {unknown})"
        )
    name = entry["name"]
    if not (isinstance(name, str) and _DATASET_NAME.fullmatch(name)):
        raise ReferencePackError(
            f"reference pack {pack}: dataset name {name!r} must be letters, digits and "
            "underscores, not starting with a digit"
        )
    file = entry["file"]
    if not (isinstance(file, str) and _FILE_NAME.fullmatch(file) and ".." not in file):
        raise ReferencePackError(
            f"reference pack {pack}: dataset {name!r} file {file!r} must be a plain *.arrow "
            "file name in the pack directory"
        )
    fields = entry["fields"]
    if not (
        isinstance(fields, list)
        and fields
        and all(isinstance(f, str) and f for f in fields)
        and len(set(fields)) == len(fields)
    ):
        raise ReferencePackError(
            f"reference pack {pack}: dataset {name!r} fields must be a non-empty list of "
            "distinct names"
        )
    if not (_is_int(entry["rows"]) and entry["rows"] >= 0):
        raise ReferencePackError(
            f"reference pack {pack}: dataset {name!r} rows must be a non-negative integer"
        )
    sha = entry["sha256"]
    if not (isinstance(sha, str) and _SHA256.fullmatch(sha)):
        raise ReferencePackError(
            f"reference pack {pack}: dataset {name!r} sha256 must be 64 lowercase hex digits"
        )


def check_manifest(manifest: Any, where: str = "pack.json") -> dict[str, Any]:
    """``manifest`` itself when it is a valid version 1 manifest, else a
    :class:`ReferencePackError` that says what is wrong."""
    if not isinstance(manifest, dict):
        raise ReferencePackError(f"{where}: the manifest is not a JSON object")
    if manifest.get("format") != PACK_FORMAT:
        raise ReferencePackError(
            f"{where}: format must be {PACK_FORMAT!r}, found {manifest.get('format')!r}"
        )
    version = manifest.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ReferencePackError(f"{where}: version must be an integer of 1 or more")
    if version > PACK_VERSION:
        raise ReferencePackError(
            f"{where}: pack format version {version} is newer than this Shape reads "
            f"({PACK_VERSION}); upgrade Shape to read it"
        )
    unknown = sorted(set(manifest) - set(_KEYS))
    if unknown:
        raise ReferencePackError(f"{where}: unexpected keys {unknown}")
    for key in _KEYS:
        if key not in manifest:
            raise ReferencePackError(f"{where}: missing {key}")
    name = manifest["name"]
    if not (isinstance(name, str) and _PACK_NAME.fullmatch(name)):
        raise ReferencePackError(
            f"{where}: name {name!r} must be lowercase letters, digits and hyphens"
        )
    for key in _TEXT_KEYS:
        if not (isinstance(manifest[key], str) and manifest[key].strip()):
            raise ReferencePackError(f"{where}: {key} must be a non-empty text")
    datasets = manifest["datasets"]
    if not (isinstance(datasets, list) and datasets):
        raise ReferencePackError(f"{where}: datasets must be a non-empty list")
    seen: set[str] = set()
    for entry in datasets:
        _check_dataset(name, entry)
        if entry["name"] in seen:
            raise ReferencePackError(
                f"reference pack {name}: duplicate dataset name {entry['name']!r}"
            )
        seen.add(entry["name"])
    return manifest


def read_manifest(directory: str | os.PathLike[str]) -> dict[str, Any]:
    """The validated ``pack.json`` of the pack in ``directory``."""
    path = Path(directory) / MANIFEST
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ReferencePackError(f"{directory}: no {MANIFEST} (not a reference pack)") from None
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ReferencePackError(f"{path}: not valid JSON ({exc})") from None
    return check_manifest(data, str(path))


def _ipc_bytes(table: pa.Table) -> bytes:
    sink = io.BytesIO()
    with pa.ipc.new_file(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue()


def write_pack(
    directory: str | os.PathLike[str],
    *,
    name: str,
    pack_version: str,
    source: str,
    retrieved: str,
    license: str,
    attribution: str,
    transformation_version: str,
    sensitivity: str,
    tables: Mapping[str, pa.Table],
) -> dict[str, Any]:
    """Write a pack: one Arrow IPC file per table in ``tables`` (dataset name to table) and
    ``pack.json`` with their checksums. Returns the manifest. The same inputs give the same
    bytes."""
    datasets = []
    files: dict[str, bytes] = {}
    for dataset, table in tables.items():
        if not (isinstance(dataset, str) and _DATASET_NAME.fullmatch(dataset)):
            raise ReferencePackError(
                f"reference pack {name}: dataset name {dataset!r} must be letters, digits and "
                "underscores"
            )
        data = _ipc_bytes(table)
        file = f"{dataset}.arrow"
        files[file] = data
        datasets.append(
            {
                "name": dataset,
                "file": file,
                "fields": list(table.column_names),
                "rows": table.num_rows,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    manifest = {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "name": name,
        "pack_version": pack_version,
        "source": source,
        "retrieved": retrieved,
        "license": license,
        "attribution": attribution,
        "transformation_version": transformation_version,
        "sensitivity": sensitivity,
        "datasets": datasets,
    }
    check_manifest(manifest, f"pack {name!r}")
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    for file, data in files.items():
        (out / file).write_bytes(data)
    (out / MANIFEST).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


_cache_lock = threading.Lock()
_table_cache: dict[tuple[str, str, int, int], pa.Table] = {}


@dataclass(frozen=True)
class Pack:
    """A pack found on disk: its directory, validated manifest and where it came from
    (``shipped`` or ``search path``)."""

    path: Path
    manifest: dict[str, Any]
    origin: str

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def datasets(self) -> list[dict[str, Any]]:
        return list(self.manifest["datasets"])

    def dataset_entry(self, name: str) -> dict[str, Any] | None:
        return next((d for d in self.manifest["datasets"] if d["name"] == name), None)

    def table(self, dataset: str) -> pa.Table:
        """The data of ``dataset``, read after its checksum, field names and row count have been
        checked against the manifest."""
        entry = self.dataset_entry(dataset)
        if entry is None:
            raise ReferencePackError(f"reference pack {self.name}: no dataset {dataset!r}")
        path = self.path / entry["file"]
        try:
            st = path.stat()
        except FileNotFoundError:
            raise ReferencePackError(
                f"reference pack {self.name}: {entry['file']} is missing"
            ) from None
        key = (str(path), entry["sha256"], st.st_mtime_ns, st.st_size)
        with _cache_lock:
            hit = _table_cache.get(key)
        if hit is not None:
            return hit
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ReferencePackError(
                f"reference pack {self.name}: {entry['file']} does not match its checksum"
            )
        try:
            table = pa.ipc.open_file(io.BytesIO(data)).read_all()
        except pa.ArrowInvalid:
            raise ReferencePackError(
                f"reference pack {self.name}: {entry['file']} is not an Arrow IPC file"
            ) from None
        if table.column_names != entry["fields"] or table.num_rows != entry["rows"]:
            raise ReferencePackError(
                f"reference pack {self.name}: {entry['file']} has fields "
                f"{table.column_names} and {table.num_rows} rows, but its manifest says fields "
                f"{entry['fields']} and {entry['rows']} rows"
            )
        with _cache_lock:
            _table_cache[key] = table
        return table


@dataclass
class Discovery:
    """What a search found: the packs, and the directories that looked like packs but were not
    readable (``problems``: a :class:`Pack`-less path with its message)."""

    packs: list[Pack] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


@dataclass(frozen=True)
class Problem:
    path: Path
    message: str


def shipped_roots() -> list[Path]:
    """The directories of shipped packs: the core's, then each installed package with an entry
    point in :data:`SHIPPED_GROUP` that has a ``reference_packs`` directory."""
    roots = [Path(__file__).parent / "data"]
    for package in _shipping_packages():
        spec = importlib.util.find_spec(package)
        if spec is None or not spec.submodule_search_locations:
            continue
        for location in spec.submodule_search_locations:
            candidate = Path(location) / SHIPPED_DIR
            if candidate.is_dir():
                roots.append(candidate)
    return roots


def _shipping_packages() -> list[str]:
    """The top-level packages of the installed :data:`SHIPPED_GROUP` entry points, sorted."""
    from importlib.metadata import entry_points

    points = entry_points(group=SHIPPED_GROUP)
    return sorted({ep.value.split(":")[0].split(".")[0] for ep in points})


def _pack_dirs(root: Path) -> list[Path]:
    """``root`` itself when it holds ``pack.json``, and each of its subdirectories that does."""
    found = [root] if (root / MANIFEST).is_file() else []
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return found
    return found + [c for c in children if (c / MANIFEST).is_file()]


def discover_packs(directories: Iterable[str | os.PathLike[str]] | None = None) -> Discovery:
    """Every pack in ``directories`` (default: the search paths of
    :mod:`shape.generation.reference`), then the shipped packs. Earlier packs come first; a
    pack whose manifest cannot be read is reported in ``problems`` and does not stop the
    search."""
    if directories is None:
        from shape.generation.reference import search_directories

        search = search_directories()
    else:
        search = [Path(d) for d in directories]
    found = Discovery()
    seen: set[Path] = set()
    for origin, roots in (("search path", search), ("shipped", shipped_roots())):
        for root in roots:
            for directory in _pack_dirs(Path(root)):
                resolved = directory.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    found.packs.append(Pack(directory, read_manifest(directory), origin))
                except ReferencePackError as exc:
                    found.problems.append(Problem(directory, str(exc)))
    return found


def find_pack(name: str, directories: Sequence[str | os.PathLike[str]] | None = None) -> Pack:
    """The first pack called ``name``; :class:`UnknownPackError` when there is none."""
    if not _PACK_NAME.fullmatch(name):
        raise UnknownPackError(f"{name!r} is not a plain pack name (lowercase letters, digits, -)")
    found = discover_packs(directories)
    for pack in found.packs:
        if pack.name == name:
            return pack
    known = ", ".join(sorted({p.name for p in found.packs})) or "none"
    raise UnknownPackError(f"no reference pack {name!r} (found: {known})")


def find_dataset(name: str, directories: Sequence[Path]) -> tuple[Pack, dict[str, Any]] | None:
    """The first pack dataset called ``name``: the search ``directories`` first, then the shipped
    packs. A pack that cannot be read is skipped here (``shape reference list`` reports it)."""
    for pack in discover_packs(directories).packs:
        entry = pack.dataset_entry(name)
        if entry is not None:
            return pack, entry
    return None
