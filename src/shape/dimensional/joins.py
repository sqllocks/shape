"""Order-preserving left joins and first-occurrence de-duplication on Arrow tables."""

from __future__ import annotations

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]


def _key_pair(left: pa.ChunkedArray, right: pa.ChunkedArray) -> tuple[pa.Array, pa.Array]:
    """Both key columns as flat arrays of one type (the left's), or a ValueError."""
    lk, rk = left.combine_chunks(), right.combine_chunks()
    if rk.type != lk.type:
        try:
            rk = pc.cast(rk, lk.type)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
            raise ValueError(
                f"join keys have incompatible types: {lk.type} (left) and {rk.type} (right)"
            ) from exc
    return lk, rk


def match_indices(
    left: pa.ChunkedArray, right: pa.ChunkedArray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Left-join row indices: ``(left_rows, right_rows, matched)``.

    Rows come out in left order; a left row with several right matches repeats once per match, in
    the right table's order; one with none appears once, unmatched. A null key never matches.
    ``right_rows`` is only meaningful where ``matched`` is true.
    """
    lk, rk = _key_pair(left, right)
    n_left = len(lk)
    uniq = pc.unique(pc.drop_null(rk))
    r_codes = pc.index_in(rk, value_set=uniq).to_numpy(zero_copy_only=False)
    l_codes = pc.index_in(lk, value_set=uniq).to_numpy(zero_copy_only=False)
    r_valid = ~np.isnan(r_codes.astype("float64"))
    r_int = np.where(r_valid, r_codes, 0).astype(np.int64)
    l_valid = ~np.isnan(l_codes.astype("float64"))
    l_int = np.where(l_valid, l_codes, 0).astype(np.int64)
    counts = np.bincount(r_int[r_valid], minlength=len(uniq))
    order = np.argsort(np.where(r_valid, r_int, len(uniq)), kind="stable")
    starts = np.concatenate(([0], np.cumsum(counts)))[:-1] if len(uniq) else np.zeros(0, np.int64)
    reps = np.where(l_valid, np.maximum(counts[l_int] if len(uniq) else 0, 1), 1)
    left_rows = np.repeat(np.arange(n_left, dtype=np.int64), reps)
    first = np.cumsum(reps) - reps
    within = np.arange(len(left_rows), dtype=np.int64) - np.repeat(first, reps)
    matched_row = np.repeat(l_valid & (counts[l_int] > 0 if len(uniq) else False), reps)
    base = np.repeat(starts[l_int] if len(uniq) else np.zeros(n_left, np.int64), reps)
    pos = np.where(matched_row, base + within, 0)
    right_rows = order[pos] if len(order) else np.zeros(len(left_rows), np.int64)
    return left_rows, right_rows, matched_row


def left_join(
    left: pa.Table,
    right: pa.Table,
    left_on: str,
    right_on: str,
    *,
    suffix: str | None,
    right_prefix: str = "",
    drop_right_key: bool = True,
    single_match: bool = False,
) -> pa.Table:
    """``left`` plus the columns of ``right``, matched on ``left_on == right_on``.

    The right key column is dropped when ``drop_right_key`` (it equals the left key) and kept
    otherwise. A right column that has the name of a left column takes ``suffix`` (``None``: an
    error); ``right_prefix`` is put in front of every right column first. A name still colliding
    afterwards is a ValueError. With ``single_match`` a left row matching several right
    rows is an error (the join would repeat it).
    """
    left_rows, right_rows, matched = match_indices(left[left_on], right[right_on])
    if single_match and len(left_rows) != left.num_rows:
        raise ValueError(
            f"joining on {left_on} = {right_on} would repeat rows of the left table: "
            f"the key is not unique in the other table"
        )
    taken = left.take(pa.array(left_rows))
    right_cols = [c for c in right.column_names if not (drop_right_key and c == right_on)]
    idx = pa.array(right_rows, type=pa.int64(), mask=~matched)
    out_names = list(left.column_names)
    arrays = list(taken.columns)
    for name in right_cols:
        new = f"{right_prefix}{name}"
        if new in out_names and suffix is not None:
            new = f"{new}{suffix}"
        if new in out_names:
            raise ValueError(
                f"joined column {new!r} collides with an existing column; give the join a prefix"
            )
        out_names.append(new)
        arrays.append(right[name].take(idx))
    return pa.table(arrays, names=out_names)


def first_occurrence(table: pa.Table, column: str) -> pa.Table:
    """Rows kept in order, dropping later rows whose ``column`` value was seen before (nulls
    count as one value)."""
    col = table[column].combine_chunks()
    codes = pc.dictionary_encode(col, null_encoding="encode").indices.to_numpy(zero_copy_only=False)
    _, first = np.unique(codes, return_index=True)
    if len(first) == table.num_rows:
        return table
    return table.take(pa.array(np.sort(first)))
