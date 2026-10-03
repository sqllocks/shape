"""The public dataset library: safe profiles of public datasets, shipped with Shape.

Each dataset is a ``.shape`` profile (a safe capture: statistics and formats, never rows) in
``datasets/``, listed in ``datasets/index.json`` (format ``shape-dataset-library``) with its
source, licence, attribution and the SHA-256 of the file it was built from. No source data is
shipped. ``shape generate --from dataset:NAME`` generates from one with no network.

Only datasets under a licence of :data:`ALLOWED_LICENSES` qualify, and each is credited in
``THIRD_PARTY_NOTICES.md``. ``scripts/build_dataset_library.py`` builds a profile and is the only
code that touches the network. See ``docs/DATASET_LIBRARY.md``. Nothing heavy loads at import time
(T-18).
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from shape.errors import ShapeError

LIBRARY_FORMAT = "shape-dataset-library"
VERSION = 1
PREFIX = "dataset:"
#: The licences a dataset must be under to join the library (SPDX style identifiers).
ALLOWED_LICENSES: dict[str, str] = {
    "CC0-1.0": "Creative Commons CC0 1.0 Universal (public domain dedication)",
    "CC-BY-4.0": "Creative Commons Attribution 4.0 International",
    "public-domain": "public domain",
}
MAX_PROFILE_BYTES = 256 * 1024  # one profile
MAX_TOTAL_BYTES = 2 * 1024 * 1024  # all profiles together
ROOT = Path(__file__).resolve().parent / "datasets"

_NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_KEYS = {
    "name",
    "title",
    "source_url",
    "license",
    "attribution",
    "retrieved",
    "source_sha256",
    "rows",
    "profile",
}


class DatasetLibraryError(ShapeError, ValueError):
    """The index is malformed or from a newer Shape, or a dataset is not in the library."""


class UnknownDatasetError(DatasetLibraryError):
    """A dataset name that is not in the library."""


def _text(entry: dict[str, Any], key: str, what: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DatasetLibraryError(f"{what}: {key!r} must be text")
    return value


def parse_index(doc: Any, what: str) -> list[dict[str, Any]]:
    """The entries of a ``shape-dataset-library`` document. Raises :class:`DatasetLibraryError`
    for another format, a missing, non-integer or newer version, unknown or missing keys, a name
    that is not a slug or is listed twice, a licence that is not in :data:`ALLOWED_LICENSES`, a
    source that is not an https URL, a retrieval date that is not an ISO date, a checksum that is
    not 64 hex digits, and a row count that is not a positive integer."""
    if not isinstance(doc, dict):
        raise DatasetLibraryError(f"{what} must be a JSON object")
    if doc.get("format") != LIBRARY_FORMAT:
        raise DatasetLibraryError(f"{what} is not a {LIBRARY_FORMAT} file")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise DatasetLibraryError(
            f"{what} needs an integer 'version' of 1 or more, got {version!r}"
        )
    if version > VERSION:
        raise DatasetLibraryError(
            f"{what} is {LIBRARY_FORMAT} version {version}, written by a newer Shape; this one "
            f"reads up to version {VERSION}: upgrade Shape to use it"
        )
    unknown = sorted(set(doc) - {"format", "version", "datasets"})
    if unknown:
        raise DatasetLibraryError(f"{what} has unknown keys: {', '.join(unknown)}")
    entries = doc.get("datasets")
    if not isinstance(entries, list) or not entries:
        raise DatasetLibraryError(f"{what} needs a non-empty 'datasets' list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise DatasetLibraryError(f"{what}: a dataset entry must be an object")
        label = f"{what}: dataset {entry.get('name')!r}"
        if set(entry) != _KEYS:
            raise DatasetLibraryError(
                f"{label} has the keys {', '.join(sorted(entry))}; it needs exactly "
                f"{', '.join(sorted(_KEYS))}"
            )
        name = _text(entry, "name", label)
        if not _NAME.match(name):
            raise DatasetLibraryError(f"{label}: 'name' must be a slug such as palmer-penguins")
        if name in seen:
            raise DatasetLibraryError(f"{what}: dataset {name!r} is listed twice")
        seen.add(name)
        for key in ("title", "attribution", "profile"):
            _text(entry, key, label)
        if entry["license"] not in ALLOWED_LICENSES:
            raise DatasetLibraryError(
                f"{label}: licence {entry['license']!r} is not allowed; the library takes "
                f"{', '.join(ALLOWED_LICENSES)}"
            )
        if not _text(entry, "source_url", label).startswith("https://"):
            raise DatasetLibraryError(f"{label}: 'source_url' must be an https URL")
        try:
            dt.date.fromisoformat(_text(entry, "retrieved", label))
        except ValueError:
            raise DatasetLibraryError(f"{label}: 'retrieved' must be an ISO date") from None
        if not isinstance(entry["source_sha256"], str) or not _SHA.match(entry["source_sha256"]):
            raise DatasetLibraryError(f"{label}: 'source_sha256' must be 64 hex digits")
        rows = entry["rows"]
        if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
            raise DatasetLibraryError(f"{label}: 'rows' must be a positive integer")
        if "/" in entry["profile"] or "\\" in entry["profile"] or entry["profile"].startswith("."):
            raise DatasetLibraryError(f"{label}: 'profile' is a file name in the library folder")
    return [dict(e) for e in entries]


def load_index(root: Path | None = None) -> list[dict[str, Any]]:
    """The library index at ``root`` (default: the one shipped with Shape)."""
    import json

    path = (root or ROOT) / "index.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DatasetLibraryError(f"the dataset library index was not found: {path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise DatasetLibraryError(
            f"the dataset library index {path} cannot be read: {exc}"
        ) from None
    except ValueError as exc:
        raise DatasetLibraryError(
            f"the dataset library index {path} is not valid JSON: {exc}"
        ) from None
    return parse_index(doc, "the dataset library index")


def get_dataset(name: str, root: Path | None = None) -> dict[str, Any]:
    """The index entry of ``name``."""
    entries = load_index(root)
    for entry in entries:
        if entry["name"] == name:
            return entry
    raise UnknownDatasetError(
        f"unknown dataset {name!r}; the library has: {', '.join(e['name'] for e in entries)}"
    )


def profile_path(name: str, root: Path | None = None) -> Path:
    """The profile file of dataset ``name``."""
    entry = get_dataset(name, root)
    path = (root or ROOT) / entry["profile"]
    if not path.is_file():
        raise DatasetLibraryError(f"dataset {name!r} lists {entry['profile']}, which is missing")
    return Path(path)


def is_reference(ref: object) -> bool:
    """Whether ``ref`` is written ``dataset:NAME``."""
    return isinstance(ref, str) and ref.startswith(PREFIX)


def resolve_profile_ref(ref: str) -> str:
    """The path to read for ``ref``: the library profile for ``dataset:NAME``, ``ref`` itself for
    anything else (a ``.shape`` file)."""
    if not is_reference(ref):
        return ref
    return str(profile_path(ref[len(PREFIX) :]))


def copy_profile(name: str, destination: str | Path, root: Path | None = None) -> Path:
    """Copy the profile of ``name`` to ``destination``; an existing file is never overwritten."""
    import shutil

    out = Path(destination)
    if out.exists():
        raise DatasetLibraryError(f"{out} already exists: choose a new file")
    shutil.copyfile(profile_path(name, root), out)
    return out
