"""Reference datasets for the ``reference_data``, ``record_sample`` and ``record_field``
strategies: named lists of values or of records (dicts), found by name.

A dataset is looked up in this order:

1. datasets registered in this process (:func:`register_dataset`; a domain plugin registers the
   ``reference_data`` tables of its ``DomainDefinition`` this way);
2. ``<name>.json`` in every search directory (:func:`add_search_path`, then the directories of the
   ``SHAPE_REFERENCE_PATH`` environment variable, separated by ``os.pathsep``);
3. a dataset of a reference pack (``docs/REFERENCE_PACKS.md``): the packs in the search directories
   (a directory that holds ``pack.json``, or a subdirectory of it that does), then the packs
   that ship with Shape and with ``sqllocks-shape-domains``. A pack file is checked against the
   checksum in its manifest before it is read.

A JSON dataset is a list of strings, or a list of objects whose keys are the fields (the first
object names the fields; a later object without a field reads as null there). A registered dataset
may also be a ``pyarrow.Table`` (one field per column). Loaded files are cached by path and
modification time, so editing a file between runs is seen.

Stable interface: ``Dataset``, ``register_dataset``, ``unregister_dataset``, ``add_search_path``,
``clear_search_paths``, ``search_directories``, ``load_dataset``, ``DatasetNotFoundError`` and
``REFERENCE_PATH_ENV``.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.generation.arrowkit import array as arrow_array
from shape.security.names import is_safe_name

REFERENCE_PATH_ENV = "SHAPE_REFERENCE_PATH"


class DatasetNotFoundError(ShapeError, LookupError):
    """No registered dataset or ``<name>.json`` file has the name asked for."""


def _arrow_column(values: list[Any]) -> pa.Array:
    """``values`` as one Arrow array; a column that mixes types (a number and a string) becomes
    text rather than failing."""
    try:
        return arrow_array(values)
    except (pa.ArrowInvalid, pa.ArrowTypeError):
        return arrow_array([None if v is None else str(v) for v in values], type=pa.string())


@dataclass(frozen=True, slots=True)
class Dataset:
    """A reference dataset as Arrow columns. ``records`` is true for a dataset of objects (it
    has named fields) and false for a plain list of values (one field, :attr:`VALUE`)."""

    name: str
    fields: tuple[str, ...]
    columns: Mapping[str, pa.Array]
    records: bool

    VALUE = "value"

    def __len__(self) -> int:
        return len(next(iter(self.columns.values()))) if self.columns else 0

    def column(self, field: str) -> pa.Array:
        return self.columns[field]

    @classmethod
    def from_rows(cls, name: str, rows: Sequence[Any]) -> Dataset:
        """A dataset from a JSON-style list: strings, or dicts (the first dict names the fields)."""
        if rows and all(isinstance(r, Mapping) for r in rows):
            fields = tuple(str(k) for k in rows[0])
            cols = {f: _arrow_column([r.get(f) for r in rows]) for f in fields}
            return cls(name, fields, cols, True)
        if all(isinstance(r, str) for r in rows):
            return cls(
                name, (cls.VALUE,), {cls.VALUE: arrow_array(list(rows), type=pa.string())}, False
            )
        raise ValueError(
            f"reference dataset {name!r} must be a list of strings or a list of objects"
        )

    @classmethod
    def from_table(cls, name: str, table: pa.Table) -> Dataset:
        cols = {f: _one_chunk(table[f]) for f in table.column_names}
        return cls(name, tuple(table.column_names), cols, True)


def _one_chunk(column: pa.ChunkedArray) -> pa.Array:
    """``column`` as one array; a column that already is one is not copied."""
    return column.chunk(0) if column.num_chunks == 1 else column.combine_chunks()


_lock = threading.Lock()
_registered: dict[str, Dataset] = {}
_search_paths: list[Path] = []
_file_cache: dict[tuple[str, int, int], Dataset] = {}


def register_dataset(name: str, data: Iterable[Any] | pa.Table | Dataset) -> None:
    """Make ``data`` (a list of strings, a list of dicts, a table or a :class:`Dataset`) the
    dataset ``name``, replacing an earlier registration."""
    if isinstance(data, Dataset):
        ds = data
    elif isinstance(data, pa.Table):
        ds = Dataset.from_table(name, data)
    else:
        ds = Dataset.from_rows(name, list(data))
    with _lock:
        _registered[name] = ds


def unregister_dataset(name: str) -> None:
    with _lock:
        _registered.pop(name, None)


def add_search_path(path: str | os.PathLike[str]) -> None:
    """Search ``path`` (a directory) for ``<name>.json`` files, before the earlier paths."""
    p = Path(path)
    with _lock:
        if p in _search_paths:
            _search_paths.remove(p)
        _search_paths.insert(0, p)


def clear_search_paths() -> None:
    """Forget every path added with :func:`add_search_path` (registered datasets stay)."""
    with _lock:
        _search_paths.clear()


def search_directories() -> list[Path]:
    """The directories searched, in order: those of :func:`add_search_path` (the latest first),
    then ``SHAPE_REFERENCE_PATH``."""
    with _lock:
        added = list(_search_paths)
    env = [Path(p) for p in os.environ.get(REFERENCE_PATH_ENV, "").split(os.pathsep) if p]
    return added + env


def _from_file(name: str, path: Path) -> Dataset:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _file_cache.get(key)
    if hit is not None:
        return hit
    ds = Dataset.from_rows(name, json.loads(path.read_text(encoding="utf-8")))
    with _lock:
        _file_cache[key] = ds
    return ds


def _from_pack(name: str, pack: Any, entry: Mapping[str, Any]) -> Dataset:
    return Dataset.from_table(name, pack.table(entry["name"]))


def load_dataset(name: str) -> Dataset:
    """The dataset ``name``; raises :class:`DatasetNotFoundError` listing where it looked."""
    with _lock:
        found = _registered.get(name)
    if found is not None:
        return found
    searched = []
    if not is_safe_name(name):
        # A dataset name is a file stem in the search path, never a path (P7-04).
        raise DatasetNotFoundError(f"reference dataset name {name!r} is not a plain name")
    for directory in search_directories():
        candidate = directory / f"{name}.json"
        searched.append(str(candidate))
        if candidate.is_file():
            return _from_file(name, candidate)
    from shape.reference.packs import find_dataset

    in_pack = find_dataset(name, search_directories())
    if in_pack is not None:
        pack, entry = in_pack
        return _from_pack(name, pack, entry)
    where = ", ".join(searched) if searched else "no search path is set"
    raise DatasetNotFoundError(
        f"reference dataset {name!r} is not registered and has no <name>.json file ({where}), "
        "and no reference pack has it (`shape reference list`)"
    )
