"""What a run needs to be compared and replayed: the reproducibility tuple and the dataset id.

``reproducibility_tuple`` is the seven facts that determine a run's output: the generation schema
version and the profile document version of this build, the seed, the scale, the Shape version,
the kernel (``rust`` or ``python``) and the platform. ``dataset_id`` is a content address of
the output tables.

The canonical form behind the dataset id (``docs/REPRODUCIBILITY.md``) is independent of table
order, column order, row order and chunking:

1. Per table, every column is hashed value by value with the kernel's canonical value hash
   (``shape.kernel.hashing``), which is the same for the native and the pure-Python kernel. The
   hash does not tell null from NaN, so a flag (valid, null, NaN) is mixed in; nested values (lists,
   structs) are hashed as their JSON text.
2. The hash of each value is mixed with a salt taken from its column name and type, and the mixed
   values of a row are added (modulo 2**64). Adding makes the row digest independent of column
   order. This is done with two different hash seeds, so a row digest is 128 bits.
3. The row digests are sorted and hashed with SHA-256 together with the row count: the table digest.
4. The id is ``sha256:`` plus the SHA-256 of a JSON document (sorted keys, no spaces) that holds
   the id format version and, per table name, the row count, the sorted ``name: type`` pairs and the
   table digest. Types are Arrow type names, with ``large_string`` read as ``string`` and
   ``large_binary`` as ``binary``, and a dictionary column read as its value type.

Two values are the same to the id when the kernel hash says so: ``0.0`` and ``-0.0``, and an integer
and the float of equal value, differ only through the column type, which the id includes.
"""

from __future__ import annotations

import hashlib
import json
import platform
from collections.abc import Mapping
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

DATASET_ID_PREFIX = "sha256:"
DATASET_ID_VERSION = 1
REPRODUCIBILITY_KEYS = (
    "schema_version",
    "profile_version",
    "seed",
    "scale",
    "shape_version",
    "kernel",
    "platform",
)
_SEEDS = (0x5348415045, 0x44415441)  # two independent hash seeds: 128-bit row digests
_FLAG = (
    np.uint64(0x9E3779B97F4A7C15),
    np.uint64(0xC2B2AE3D27D4EB4F),
    np.uint64(0x165667B19E3779F9),
)
Array = npt.NDArray[np.uint64]


def reproducibility_tuple(seed: int, scale: str) -> dict[str, Any]:
    """The reproducibility tuple of a run made now with ``seed`` and ``scale``."""
    from shape import __version__
    from shape.generation.schema import SCHEMA_VERSION
    from shape.kernel.dispatch import kernel_name
    from shape.profile.engine import SCHEMA_VERSION as PROFILE_VERSION

    return {
        "schema_version": SCHEMA_VERSION,
        "profile_version": PROFILE_VERSION,
        "seed": seed,
        "scale": scale,
        "shape_version": __version__,
        "kernel": kernel_name(),
        "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
    }


def _type_name(t: pa.DataType) -> str:
    if pa.types.is_dictionary(t):
        return _type_name(t.value_type)
    if pa.types.is_large_string(t):
        return "string"
    if pa.types.is_large_binary(t):
        return "binary"
    return str(t)


def _mix(x: Array) -> Array:
    """The splitmix64 finaliser: a bijection that spreads every input bit over the output."""
    with np.errstate(over="ignore"):
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        return x ^ (x >> np.uint64(31))


def _plain(col: pa.ChunkedArray) -> pa.ChunkedArray:
    t = col.type
    if pa.types.is_dictionary(t):
        return _plain(col.cast(t.value_type))
    if pa.types.is_nested(t):
        text = [
            None if v is None else json.dumps(v, default=str, sort_keys=True)
            for v in col.to_pylist()
        ]
        return pa.chunked_array([pa.array(text, pa.string())])
    return col


def _column_hashes(col: pa.ChunkedArray, seed: int) -> tuple[Array, Array]:
    """The kernel hash of every value (0 where null or NaN) and the flag 0/1/2 (valid/null/NaN)."""
    from shape.kernel.hashing import hash_column

    col = _plain(col)
    if len(col) == 0:
        return np.zeros(0, np.uint64), np.zeros(0, np.uint64)
    hashes = hash_column(col, seed)
    h = np.asarray(hashes.fill_null(0).to_numpy(zero_copy_only=False), dtype=np.uint64)
    flag = np.zeros(len(col), dtype=np.uint64)
    flag[np.asarray(pc.is_null(col).to_numpy(zero_copy_only=False), dtype=bool)] = 1
    if pa.types.is_floating(col.type):
        flag[
            np.asarray(pc.is_nan(col).fill_null(False).to_numpy(zero_copy_only=False), dtype=bool)
        ] = 2
    return h, flag


def _salt(name: str, type_name: str) -> np.uint64:
    digest = hashlib.sha256(f"{name}\x00{type_name}".encode()).digest()
    return np.uint64(int.from_bytes(digest[:8], "little"))


def _table_digest(table: pa.Table) -> str:
    n = table.num_rows
    rows: list[Array] = []
    for seed in _SEEDS:
        total = np.zeros(n, dtype=np.uint64)
        for name in sorted(table.column_names):
            col = table.column(name)
            h, flag = _column_hashes(col, seed)
            salt = _salt(name, _type_name(col.type))
            with np.errstate(over="ignore"):
                flag_mix = np.array(_FLAG, dtype=np.uint64)[flag.astype(np.int64)] if n else flag
                total = total + _mix(h ^ flag_mix ^ salt)
        rows.append(total)
    order = np.lexsort((rows[1], rows[0])) if n else np.zeros(0, dtype=np.int64)
    sha = hashlib.sha256(n.to_bytes(8, "little"))
    sha.update(
        np.ascontiguousarray(np.column_stack([rows[0][order], rows[1][order]]) if n else rows[0])
        .astype("<u8")
        .tobytes()
    )
    return sha.hexdigest()


def dataset_id(tables: Mapping[str, pa.Table]) -> str:
    """The content-addressed id of ``tables`` (see the module docstring for the canonical form)."""
    doc = {
        "v": DATASET_ID_VERSION,
        "tables": {
            name: {
                "rows": t.num_rows,
                "columns": sorted(f"{f.name}:{_type_name(f.type)}" for f in t.schema),
                "digest": _table_digest(t),
            }
            for name, t in sorted(tables.items())
        },
    }
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    return DATASET_ID_PREFIX + hashlib.sha256(text.encode()).hexdigest()
