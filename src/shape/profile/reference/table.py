"""Table and dataset level profiling: primary keys, foreign keys, correlation."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from .column import _combine, _profile_column, _Work
from .model import ColumnProfile, DatasetProfile, TableProfile
from .readers import _arrow_cols, _Col, _csv_cols, _n_threads, read_csv

# ---------------------------------------------------------------------------
# table-level: PK, FK, correlation
# ---------------------------------------------------------------------------

_PK_NAMES = ("id", "_id", "pk", "key")


def _detect_primary_key(works: list[_Work], row_count: int) -> list[str]:
    cands = []
    for w in works:
        p = w.prof
        if p.null_count > 0 or p.cardinality != row_count:
            continue
        if p.dtype == "integer":
            cands.append(p.name)
        elif p.dtype == "string" and p.pattern == "uuid":
            cands.append(p.name)
    for c in cands:
        lower = c.lower()
        if any(lower == pn or lower.endswith(pn) for pn in _PK_NAMES):
            return [c]
    return cands[:1]


def _correlation(works: list[_Work], row_count: int) -> dict[str, dict[str, float]]:
    """DataFrame.select_dtypes(np.number).corr('pearson') -> nested dict (round 4).

    Pairwise-complete Pearson via BLAS: columns are centred on their global mean (to
    avoid cancellation), then per-pair sums over the rows where *both* are present are
    obtained as matrix products with the null masks of the columns that have nulls."""
    cols = [w for w in works if w.col.kind in ("int", "float")]
    if len(cols) < 2:
        return {}
    k, n = len(cols), row_count
    X = np.empty((n, k), dtype=np.float64, order="F")  # column-contiguous fills / reductions
    for j, w in enumerate(cols):
        X[:, j] = _combine(w.col.arr).to_numpy(zero_copy_only=False)
    nulls = [j for j, w in enumerate(cols) if w.col.kind == "float" and w.prof.null_count]
    cnt = np.full(k, float(n))
    masks = {}
    for j in nulls:
        col = X[:, j]
        m = np.isnan(col)
        col[m] = 0.0
        masks[j] = m
        cnt[j] = n - np.count_nonzero(m)
    with np.errstate(invalid="ignore", divide="ignore"):
        gmean = X.sum(axis=0) / np.where(cnt > 0, cnt, 1)
    X -= gmean
    if nulls:
        MB = np.empty((n, len(nulls)), dtype=np.float64, order="F")
        for i, j in enumerate(nulls):
            X[:, j][masks[j]] = 0.0
            np.logical_not(masks[j], out=MB[:, i], casting="unsafe")
    Sxy = X.T @ X
    colsum = X.sum(axis=0)
    N = np.full((k, k), float(n))
    Sx = np.repeat(colsum[:, None], k, axis=1)  # Sx[a, b]: sum of x_a where a & b present
    if nulls:
        N[:, nulls] = cnt[nulls][None, :]
        N[nulls, :] = cnt[nulls][:, None]
        N[np.ix_(nulls, nulls)] = MB.T @ MB
        Sx[:, nulls] = X.T @ MB
    np.square(X, out=X)  # X no longer needed: reuse for x^2
    colsq = X.sum(axis=0)
    Sxx = np.repeat(colsq[:, None], k, axis=1)
    if nulls:
        Sxx[:, nulls] = X.T @ MB
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = Sxy - Sx * Sx.T / N
        va = np.maximum(Sxx - Sx * Sx / N, 0.0)
        div = np.sqrt(va * va.T)
        r = np.where((div != 0) & (N >= 1), cov / div, np.nan)
    names = [w.col.name for w in cols]
    out: dict[str, dict[str, float]] = {}
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i != j:
                v = r[i, j]
                if not np.isnan(v):
                    out.setdefault(a, {})[b] = round(float(v), 4)
    return out


def _fk_values(w: _Work) -> pa.Array:
    u = w.uniques
    if w.col.kind in ("int", "float", "bool", "objbool"):
        return pc.cast(u, pa.float64())
    return u


def _detect_fks(
    tname: str,
    works: list[_Work],
    all_works: dict[str, list[_Work]],
    pks: dict[str, list[str]],
    threshold: float = 0.9,
) -> dict[str, str]:
    if not all_works:
        return {}
    out = {}
    for w in works:
        col = w.col.name
        lower = col.lower()
        if not lower.endswith("_id"):
            continue
        cand = lower.rsplit("_id", 1)[0]
        parent = None
        for t in all_works:
            if t.lower() == cand:
                parent = t
                break
        if parent is None or parent == tname:
            continue
        ppk = pks[parent]
        if not ppk:
            continue
        pw = next(x for x in all_works[parent] if x.col.name == ppk[0])
        child_vals = _fk_values(w)
        if len(child_vals) == 0:
            continue
        parent_vals = _fk_values(pw)
        if child_vals.type != parent_vals.type:
            overlap = 0.0
        else:
            overlap = pc.sum(pc.is_in(child_vals, value_set=parent_vals)).as_py() / len(child_vals)
        if overlap >= threshold:
            out[col] = parent
    return out


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

_FORK_STATE: dict[str, Any] = {}


def _fork_task(i: int) -> tuple[int, ColumnProfile, Any]:
    cols, row_count, keep = _FORK_STATE["args"]
    w = _profile_column(cols[i], row_count)
    return i, w.prof, (w.uniques if keep else None)


def _profile_cols(
    cols: list[_Col], row_count: int, threads: int | None, keep_uniques: bool = False
) -> list[_Work]:
    """Profile every column.  threads == 1: sequential.  Otherwise columns are spread over
    a pool: PROFILE_POOL=process (fork, copy-on-write access to the Arrow data; avoids
    GIL contention in the Python-level optimisers), PROFILE_POOL=thread, or auto (default:
    processes for wide tables with >= 3 columns per worker, threads otherwise -- a fork
    pool's start-up and result pickling only pays off when there are many columns)."""
    n = _n_threads(threads)
    if n == 1 or len(cols) == 1:
        return [_profile_column(c, row_count) for c in cols]
    mode = os.environ.get("PROFILE_POOL", "auto")
    if mode == "auto":
        mode = "process" if len(cols) >= 3 * n else "thread"
    if mode == "process":
        import multiprocessing as mp

        _FORK_STATE["args"] = (cols, row_count, keep_uniques)
        try:
            ctx = mp.get_context("fork")
            out: list[Any] = [None] * len(cols)
            # most expensive columns first (numeric: distribution fitting; strings: hashing)
            order = sorted(
                range(len(cols)), key=lambda i: cols[i].kind not in ("float", "int", "str")
            )
            with ctx.Pool(min(n, len(cols))) as pool:
                for i, prof, uniq in pool.imap_unordered(_fork_task, order, chunksize=1):
                    out[i] = _Work(col=cols[i], prof=prof, uniques=uniq)
            return out
        finally:
            _FORK_STATE.clear()
    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(lambda c: _profile_column(c, row_count), cols))


def _sample_rows(
    cols: list[_Col], row_count: int, sample_rows: int | None
) -> tuple[list[_Col], int]:
    if sample_rows is None or row_count <= sample_rows:
        return cols, row_count
    idx = pa.array(np.random.RandomState(42).choice(row_count, size=sample_rows, replace=False))
    return [_Col(c.name, c.kind, c.arr.take(idx)) for c in cols], sample_rows


def _finish_table(
    name: str, works: list[_Work], row_count: int, pk: list[str], fks: dict[str, str]
) -> TableProfile:
    columns = {}
    for w in works:
        p = w.prof
        p.is_primary_key = p.name in pk
        p.is_foreign_key = p.name in fks
        p.fk_ref_table = fks.get(p.name)
        columns[p.name] = p
    corr = _correlation(works, row_count)
    return TableProfile(
        name=name,
        row_count=row_count,
        columns=columns,
        primary_key=pk,
        detected_fks=fks,
        correlation_matrix=corr if corr else None,
    )


def _profile_cols_table(
    name: str,
    cols: list[_Col],
    row_count: int,
    threads: int | None,
    sample_rows: int | None = None,
) -> TableProfile:
    cols, row_count = _sample_rows(cols, row_count, sample_rows)
    works = _profile_cols(cols, row_count, threads)
    pk = _detect_primary_key(works, row_count)
    return _finish_table(name, works, row_count, pk, {})


def profile_csv(
    path: str | Path,
    table_name: str | None = None,
    threads: int | None = None,
    sample_rows: int | None = None,
) -> TableProfile:
    """Equivalent of DataProfiler.from_csv(path)."""
    t = read_csv(path, threads)
    return _profile_cols_table(
        table_name or Path(path).stem, _csv_cols(t), t.num_rows, threads, sample_rows
    )


def profile_parquet(
    path: str | Path,
    table_name: str | None = None,
    threads: int | None = None,
    sample_rows: int | None = None,
) -> TableProfile:
    """Equivalent of DataProfiler().profile(pd.read_parquet(path), stem)."""
    n = _n_threads(threads)
    t = pq.read_table(path, use_threads=n != 1)
    return _profile_cols_table(
        table_name or Path(path).stem, _arrow_cols(t), t.num_rows, threads, sample_rows
    )


def profile_table(
    table: pa.Table,
    table_name: str = "table",
    threads: int | None = None,
    sample_rows: int | None = None,
) -> TableProfile:
    """Equivalent of DataProfiler().profile(table.to_pandas(), table_name)."""
    return _profile_cols_table(table_name, _arrow_cols(table), table.num_rows, threads, sample_rows)


def profile_dataset(tables: dict[str, Any], threads: int | None = None) -> DatasetProfile:
    """Equivalent of DataProfiler().profile_dataset({name: df}).  Values may be pa.Table,
    or a path to a .csv / .parquet file (read with the matching pandas semantics)."""
    cols_by_t: dict[str, tuple[list[_Col], int]] = {}
    for name, t in tables.items():
        if isinstance(t, (str, Path)):
            if str(t).endswith(".csv"):
                tt = read_csv(t, threads)
                cols_by_t[name] = (_csv_cols(tt), tt.num_rows)
            else:
                tt = pq.read_table(t, use_threads=_n_threads(threads) != 1)
                cols_by_t[name] = (_arrow_cols(tt), tt.num_rows)
        else:
            cols_by_t[name] = (_arrow_cols(t), t.num_rows)
    return profile_dataset_columns(cols_by_t, threads)


def profile_dataset_columns(
    cols_by_t: dict[str, tuple[list[_Col], int]], threads: int | None = None
) -> DatasetProfile:
    """Multi-table profile (with FK detection) from already-read columns."""
    works = {
        n: _profile_cols(c, rc, threads, keep_uniques=True) for n, (c, rc) in cols_by_t.items()
    }
    pks = {n: _detect_primary_key(w, cols_by_t[n][1]) for n, w in works.items()}
    profiles = {}
    for n, w in works.items():
        fks = _detect_fks(n, w, works, pks)
        profiles[n] = _finish_table(n, w, cols_by_t[n][1], pks[n], fks)
    rels = []
    for n, tp in profiles.items():
        for col, parent in tp.detected_fks.items():
            rels.append(
                {
                    "name": f"fk_{n}_{col}",
                    "parent": parent,
                    "child": n,
                    "parent_columns": profiles[parent].primary_key,
                    "child_columns": [col],
                    "type": "one_to_many",
                }
            )
    return DatasetProfile(tables=profiles, relationships=rels)
