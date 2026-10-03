"""The worker of the multiprocess option: one chunk of one table, written as a Parquet part file.

The engine makes any row range of a table on its own (a chunk depends only on its row range and on
the tables it points at), so a worker process needs only the schema, the seed and the row counts,
not data from the other chunks, and writes its file itself: no rows cross the process boundary.
It applies to schemas with no post-pass (see :mod:`shape.scale.chunked`); a post-pass needs whole
tables.

Everything here is importable at the top level and takes plain data, as ``ProcessPoolExecutor``
needs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from shape.scale.sinks.parquet import PART_NAME, part_rows_ok

_ENGINES: dict[str, Any] = {}


def _engine(spec: dict[str, Any]) -> Any:
    """The engine of ``spec``, built once per worker process."""
    key = str(spec["key"])
    engine = _ENGINES.get(key)
    if engine is None:
        from shape.generation.engine import Engine
        from shape.generation.schema import GenSchema

        engine = Engine(
            GenSchema.from_dict(spec["schema"]),
            seed=int(spec["seed"]),
            row_counts=dict(spec["row_counts"]),
            chunk_rows=int(spec["chunk_rows"]),
        )
        _ENGINES.clear()
        _ENGINES[key] = engine
    return engine


def generate_chunk_file(
    spec: dict[str, Any], table: str, index: int, start: int, rows: int, out_dir: str, resume: bool
) -> tuple[str, int, int, bool]:
    """Make rows ``start .. start + rows - 1`` of ``table`` and write part ``index``.

    Returns ``(table, index, rows, skipped)``; ``skipped`` is True when ``resume`` found the part
    already complete."""
    path = Path(out_dir) / table / PART_NAME.format(index)
    if resume and part_rows_ok(path, rows):
        return table, index, rows, True
    from shape.plugins.host import default_host

    engine = _engine(spec)
    raw = engine.generate_chunk(table, start, rows, chunk=start // int(spec["chunk_rows"]))
    batch = engine.finalize(table, raw)  # the declared output types, as every other path has
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    default_host().get("shape.sinks", "parquet").write(
        str(tmp), table, iter([batch]), schema=batch.schema
    )
    os.replace(tmp, path)
    return table, index, rows, False
