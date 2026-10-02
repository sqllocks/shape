"""The Gaussian-copula post-pass: induce target correlations without changing any column's
values.

For the numeric columns named in a table's ``correlated_columns`` (``[column_a, column_b, r]``
triples with ``|r| >= threshold``), draw standard normals from a row-addressed stream
(``rng.RowStream``, label ``copula``), correlate them through the Cholesky factor of the target
matrix (eigenvalues clipped to stay positive definite), and reorder each column's own values by
the rank of its normal. The marginals are therefore exactly preserved, and the result depends
only on the table's contents and the seed. Key-like columns (``id``, ``pk`` and names ending
``_id``, ``_pk``, ``_fk``) are never reordered: shuffling one would break the rows' references.
A column with nulls is left alone, unless ``nulls="rank"`` is asked for (the schema's
``generation.output.copula_nulls``, which ``shape generate --from`` sets): then the non-null values
are reordered among the non-null rows and the nulls stay where they are.

Stable interface: :func:`apply_copula`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.rng import RowStream

THRESHOLD = 0.5


def _looks_like_key(name: str) -> bool:
    n = name.lower()
    return n in ("id", "pk") or n.endswith(("_id", "_pk", "_fk"))


def _reorder_with_nulls(col: pa.Array, z: Any) -> pa.Array:
    """``col`` with its non-null values reordered by the rank of ``z`` among the non-null rows;
    null rows keep their nulls."""
    keep = np.asarray(col.is_valid().to_numpy(zero_copy_only=False))
    present = np.flatnonzero(keep)
    values = pc.take(col, pa.array(present))
    ranks = np.argsort(np.argsort(z[present], kind="stable"), kind="stable")
    mixed = pc.take(pc.take(values, pc.sort_indices(values)), pa.array(ranks))
    return pc.replace_with_mask(col, pa.array(keep), mixed)


def apply_copula(
    table: pa.Table,
    pairs: Sequence[Sequence[Any]],
    seed: int,
    table_name: str,
    threshold: float = THRESHOLD,
    nulls: str = "skip",
) -> pa.Table:
    """``table`` with the columns of ``pairs`` reordered to match their target correlations."""
    matrix: dict[str, dict[str, float]] = {}
    for a, b, r in pairs:
        matrix.setdefault(a, {})[b] = float(r)
        matrix.setdefault(b, {})[a] = float(r)
    numeric = [
        c
        for c in table.column_names
        if c in matrix
        and not _looks_like_key(c)
        and (pa.types.is_integer(table[c].type) or pa.types.is_floating(table[c].type))
    ]
    if len(numeric) < 2:
        return table
    active: set[tuple[str, str]] = set()
    for a, row in matrix.items():
        for b, r in row.items():
            if abs(r) >= threshold and a in numeric and b in numeric:
                active.add((a, b) if a <= b else (b, a))
    if not active:
        return table
    cols = sorted({c for pair in active for c in pair})
    k, n = len(cols), table.num_rows
    index = {c: i for i, c in enumerate(cols)}
    target = np.eye(k)
    for a, b in active:
        target[index[a], index[b]] = target[index[b], index[a]] = matrix[a][b]
    vals, vecs = np.linalg.eigh(target)
    target = vecs @ np.diag(np.clip(vals, 1e-8, None)) @ vecs.T
    try:
        chol = np.linalg.cholesky(target)
    except np.linalg.LinAlgError:
        return table
    z = np.stack(
        [RowStream(seed, table_name, "*", f"copula:{i}").normal(0, n) for i in range(k)], axis=1
    )
    z = z @ chol.T
    out = table
    for i, c in enumerate(cols):
        col = table[c].combine_chunks()
        if col.null_count:
            if nulls != "rank":
                continue
            out = out.set_column(out.column_names.index(c), c, _reorder_with_nulls(col, z[:, i]))
            continue
        ranks = np.argsort(np.argsort(z[:, i], kind="stable"), kind="stable")
        ascending = pc.take(col, pc.sort_indices(col))
        out = out.set_column(out.column_names.index(c), c, pc.take(ascending, pa.array(ranks)))
    return out
