"""Tables with nested columns (list, struct, map, nested in nested), written as Parquet, Delta and
JSON Lines, for the nested-column tests (GitHub issue #1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

ROWS = 60

# Nested column name -> the Arrow type it must come back as.
NESTED_TYPES: dict[str, pa.DataType] = {
    "li": pa.list_(pa.int64()),
    "ls": pa.list_(pa.string()),
    "st": pa.struct([("a", pa.int64()), ("b", pa.string())]),
    "mp": pa.map_(pa.string(), pa.int64()),
    "lst": pa.list_(pa.struct([("p", pa.float64()), ("q", pa.list_(pa.int32()))])),
    "ss": pa.struct([("l", pa.list_(pa.int64())), ("s", pa.struct([("z", pa.bool_())]))]),
}
# JSON Lines has no map type (an object is a struct), so its tables leave ``mp`` out.
JSONL_NESTED = tuple(n for n in NESTED_TYPES if n != "mp")


def _every(i: int, n: int) -> bool:
    return i % n == 0


def nested_array(name: str) -> pa.Array:
    """The values of one nested column: ``ROWS`` rows, a null every fifth row, list lengths
    cycling 0 to 4 (a map's 0 to 3)."""
    vals: list[Any] = []
    for i in range(ROWS):
        if _every(i, 5):
            vals.append(None)
        elif name == "li":
            vals.append(list(range(i % 5)))
        elif name == "ls":
            vals.append([f"v{j}" for j in range(i % 5)])
        elif name == "st":
            vals.append({"a": i, "b": None if _every(i, 3) else f"b{i}"})
        elif name == "mp":
            vals.append([(f"k{j}", j + i) for j in range(i % 4)])
        elif name == "lst":
            vals.append([{"p": float(j), "q": list(range(j))} for j in range(i % 3)])
        else:
            vals.append({"l": list(range(i % 3)), "s": {"z": _every(i, 2)}})
    return pa.array(vals, NESTED_TYPES[name])


def flat_table() -> pa.Table:
    return pa.table(
        {
            "id": pa.array(range(ROWS), pa.int64()),
            "amount": pa.array([round(i * 1.5, 2) for i in range(ROWS)], pa.float64()),
            "label": pa.array([f"row{i % 7}" for i in range(ROWS)], pa.string()),
            "flag": pa.array([i % 3 == 0 for i in range(ROWS)], pa.bool_()),
        }
    )


def table_with(*nested: str) -> pa.Table:
    """The flat columns first, then the named nested columns."""
    flat = flat_table()
    return pa.table(
        {**{n: flat[n] for n in flat.column_names}, **{n: nested_array(n) for n in nested}}
    )


def nested_only(name: str) -> pa.Table:
    return pa.table({name: nested_array(name)})


def write_parquet(path: Path, table: pa.Table) -> Path:
    pq.write_table(table, str(path))
    return path


def write_delta(path: Path, table: pa.Table) -> Path:
    from deltalake import write_deltalake

    write_deltalake(str(path), table)
    return path


def write_jsonl(path: Path, table: pa.Table) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for row in table.to_pylist():
            fh.write(json.dumps(row) + "\n")
    return path


def write(kind: str, directory: Path, table: pa.Table, stem: str = "t") -> Path:
    """``table`` as ``kind`` (``parquet``, ``delta`` or ``jsonl``) under ``directory``."""
    if kind == "parquet":
        return write_parquet(directory / f"{stem}.parquet", table)
    if kind == "delta":
        return write_delta(directory / stem, table)
    return write_jsonl(directory / f"{stem}.jsonl", table)
