"""A synthetic duplicates generator that records the true clusters.

``make_duplicates`` keeps every input row in place, then appends damaged copies of a seeded sample
of rows (:mod:`shape.resolve.perturb`). The result carries the truth: one label per row (the index
of the original) and the clusters of two or more rows, so a resolution can be scored with
:func:`shape.resolve.pair_metrics`. The same seed gives the same table and the same clusters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.resolve.perturb import fuzz_table

TRUTH_FORMAT = "shape-duplicate-truth"
TRUTH_VERSION = 1


@dataclass(frozen=True)
class SyntheticDuplicates:
    """The table with duplicates and the truth about them."""

    table: pa.Table
    labels: npt.NDArray[np.int64]
    clusters: list[list[int]]
    true_pairs: int
    seed: int
    source_rows: int


def make_duplicates(
    table: pa.Table,
    *,
    rate: float = 0.2,
    max_copies: int = 2,
    fuzz: float = 0.5,
    seed: int = 0,
    id_column: str | None = None,
    skip: tuple[str, ...] = (),
    numeric_jitter: float = 0.01,
    date_days: int = 2,
) -> SyntheticDuplicates:
    """Duplicate ``round(rows x rate)`` rows, each 1 to ``max_copies`` times.

    Args:
        table: The clean table; its rows stay first and unchanged.
        rate: The share of rows that get duplicates, 0 to 1.
        max_copies: The most copies of one row.
        fuzz: The chance that a cell of a copy is damaged, 0 to 1 (0 gives exact copies).
        seed: The seed.
        id_column: An integer key; copies get fresh keys above the largest one.
        skip: Columns left undamaged.
        numeric_jitter: The largest relative change of a number.
        date_days: The largest shift of a date, in days.
    """
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"rate is 0 to 1, got {rate}")
    if not 0.0 <= fuzz <= 1.0:
        raise ValueError(f"fuzz is 0 to 1, got {fuzz}")
    if max_copies < 1:
        raise ValueError("max_copies must be at least 1")
    if id_column is not None:
        if id_column not in table.column_names:
            raise ValueError(f"no column {id_column!r} to use as the id")
        if not pa.types.is_integer(table.schema.field(id_column).type):
            raise ValueError(f"the id column {id_column!r} must be an integer")
    n = table.num_rows
    rng = np.random.default_rng(seed)
    k = int(round(n * rate))
    sources = (
        np.sort(rng.choice(n, size=k, replace=False)).astype(np.int64)
        if k
        else np.empty(0, np.int64)
    )
    per = rng.integers(1, max_copies + 1, size=k).astype(np.int64)
    src_rows = np.repeat(sources, per)
    copies = table.take(pa.array(src_rows, type=pa.int64()))
    copies = fuzz_table(
        copies,
        rng,
        rate=fuzz,
        skip=(id_column, *skip),
        numeric_jitter=numeric_jitter,
        date_days=date_days,
    )
    if id_column is not None and len(src_rows):
        peak = int(max(table.column(id_column).to_pylist(), default=0) or 0)
        i = copies.column_names.index(id_column)
        copies = copies.set_column(
            i,
            table.schema.field(id_column),
            pa.array(
                range(peak + 1, peak + 1 + len(src_rows)), type=table.schema.field(id_column).type
            ),
        )
    out = pa.concat_tables([table, copies]) if len(src_rows) else table
    labels = np.concatenate([np.arange(n, dtype=np.int64), src_rows])
    clusters: list[list[int]] = []
    start = n
    for s, c in zip(sources.tolist(), per.tolist(), strict=True):
        clusters.append([s, *range(start, start + c)])
        start += c
    pairs = sum(len(m) * (len(m) - 1) // 2 for m in clusters)
    return SyntheticDuplicates(out, labels, clusters, pairs, seed, n)


def write_truth(path: str | Path, dup: SyntheticDuplicates) -> Path:
    """Write the true clusters as JSON (format ``shape-duplicate-truth``, version 1)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "format": TRUTH_FORMAT,
        "version": TRUTH_VERSION,
        "rows": dup.table.num_rows,
        "source_rows": dup.source_rows,
        "seed": dup.seed,
        "clusters": dup.clusters,
    }
    target.write_text(json.dumps(doc, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return target


def read_truth(path: str | Path, rows: int | None = None) -> npt.NDArray[np.int64]:
    """The label of each row (the smallest row of its cluster) from a truth file; ``rows`` checks
    the table size."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("format") != TRUTH_FORMAT:
        raise ValueError(f"{path} is not a duplicate-truth file (format {TRUTH_FORMAT})")
    version = doc.get("version")
    if not isinstance(version, int) or version > TRUTH_VERSION:
        raise ValueError(
            f"{path} has a newer truth version ({version}) than this Shape reads ({TRUTH_VERSION})"
        )
    total = int(doc["rows"])
    if rows is not None and rows != total:
        raise ValueError(f"the truth covers {total} rows but the table has {rows}")
    labels = np.arange(total, dtype=np.int64)
    for members in doc["clusters"]:
        labels[members] = min(members)
    return labels
