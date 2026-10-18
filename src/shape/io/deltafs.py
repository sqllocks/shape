"""Read a local Delta table through Arrow's own file system.

``deltalake`` builds the pyarrow dataset of a table on a ``PyFileSystem``, Arrow's bridge to a
Python object. The scan's last reference to that file system is dropped on an Arrow pool thread,
where freeing the Python object needs the interpreter's lock. When the program ends first, that
thread waits for a lock nobody will release while the main thread waits for the pool to stop: the
process hangs, or aborts with ``terminate called without an active exception`` (GitHub issue #1).
A table on the local disk needs no bridge: ``SubTreeFileSystem(table path, LocalFileSystem())`` is
native Arrow all the way, so no pool thread ever calls into Python.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.fs as pafs  # type: ignore[import-untyped]

#: True once a read has had to use deltalake's Python file system (a table behind an object
#: store). The command line then ends the process by itself when its output is flushed
#: (``shape.cli.main.main``), so that pool thread cannot hold the interpreter's exit.
bridge_in_use = False


def note_bridge() -> None:
    """Record that deltalake's Python file system is in use (see :data:`bridge_in_use`)."""
    global bridge_in_use
    bridge_in_use = True


def local_filesystem(path: str | os.PathLike[str]) -> Any:
    """The native Arrow file system rooted at the Delta table directory ``path``."""
    return pafs.SubTreeFileSystem(Path(path).resolve().as_posix(), pafs.LocalFileSystem())


def local_dataset(table: Any, path: str | os.PathLike[str]) -> Any:
    """The pyarrow dataset of ``table`` (a ``deltalake.DeltaTable`` of the local directory
    ``path``), read through :func:`local_filesystem`."""
    return table.to_pyarrow_dataset(filesystem=local_filesystem(path))


def local_table(table: Any, path: str | os.PathLike[str]) -> pa.Table:
    """All rows of ``table`` (a ``deltalake.DeltaTable`` of the local directory ``path``) as an
    Arrow table: ``table.to_pyarrow_table()`` with Arrow's own file system, not its bridge."""
    result: pa.Table = table.to_pyarrow_table(filesystem=local_filesystem(path))
    return result
