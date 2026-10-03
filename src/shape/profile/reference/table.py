"""Table and dataset level profiling: primary keys, foreign keys, correlation."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.io.budget import check_parquet
from shape.profile.joint import analyze_table, detect_placeholders

from ._blas import single_thread_blas
from .column import _combine, _pattern_sample, _profile_column, _Work
from .model import ColumnProfile, DatasetProfile, TableProfile
from .readers import _arrow_cols, _Col, _csv_cols, _n_threads, read_csv

# ---------------------------------------------------------------------------
# table-level: PK, FK, correlation
# ---------------------------------------------------------------------------

_PK_NAMES = ("id", "_id", "pk", "key")

# below this many rows a copy of one column is under a millisecond, which a thread pool costs more
# than it saves (and its threads compete with the column workers)
_FILL_POOL_ROWS = 1_000_000


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


def _correlation(all_cols: list[_Col], row_count: int) -> dict[str, dict[str, float]]:
    """DataFrame.select_dtypes(np.number).corr('pearson') -> nested dict (round 4).

    Pairwise-complete Pearson via BLAS: columns are centred on their global mean (to
    avoid cancellation), then per-pair sums over the rows where *both* are present are
    obtained as matrix products with the null masks of the columns that have nulls."""
    # np.number in pandas 3 also covers timedelta64 (correlated through its integer nanoseconds)
    cols = [c for c in all_cols if c.kind in ("int", "uint64", "float", "objdur")]
    if len(cols) < 2:
        return {}
    k, n = len(cols), row_count
    X = np.empty((n, k), dtype=np.float64, order="F")  # column-contiguous fills / reductions
    has_nan = [False] * k  # a float column's nulls and NaNs are both "missing" (the null count)

    def fill(j: int) -> None:
        a = _combine(cols[j].arr)
        if cols[j].kind == "objdur":
            a = pc.cast(pc.cast(a, pa.duration("ns")), pa.int64()).cast(pa.float64())
        X[:, j] = a.to_numpy(zero_copy_only=False)
        if cols[j].kind in ("float", "objdur"):
            has_nan[j] = bool(np.isnan(X[:, j]).any())

    if n >= _FILL_POOL_ROWS and k > 1:  # numpy and Arrow release the GIL for these big copies
        with ThreadPoolExecutor(max_workers=min(k, os.cpu_count() or 1)) as ex:
            list(ex.map(fill, range(k)))
    else:
        for j in range(k):
            fill(j)
    nulls = [j for j in range(k) if has_nan[j]]
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
    names = [c.name for c in cols]
    keep = _strongest_pairs(r) if k > CORR_FULL_MAX_COLUMNS else None
    out: dict[str, dict[str, float]] = _TruncatedCorr() if keep is not None else {}
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i != j and (keep is None or keep[i, j]):
                v = r[i, j]
                if not np.isnan(v):
                    out.setdefault(a, {})[b] = round(float(v), 4)
    return out


# A table's correlation matrix grows with the square of its numeric columns (4 million entries,
# over 100 MB of JSON, for 2,000 columns). Past ``CORR_FULL_MAX_COLUMNS`` numeric columns it keeps,
# for each column, only its ``CORR_KEEP_PER_COLUMN`` strongest partners (a pair stays when either
# column keeps it) and the table is marked ``correlation_truncated`` (#37).
CORR_FULL_MAX_COLUMNS = 256
CORR_KEEP_PER_COLUMN = 25


class _TruncatedCorr(dict[str, dict[str, float]]):
    """A correlation matrix cut to each column's strongest pairs."""


def _strongest_pairs(r: np.ndarray) -> np.ndarray:
    strength = np.abs(np.where(np.isnan(r), 0.0, r))
    np.fill_diagonal(strength, -1.0)
    top = np.argpartition(-strength, CORR_KEEP_PER_COLUMN, axis=1)[:, :CORR_KEEP_PER_COLUMN]
    keep = np.zeros(r.shape, dtype=bool)
    np.put_along_axis(keep, top, True, axis=1)
    return keep | keep.T


def _quiet_correlation(cols: list[_Col], row_count: int) -> dict[str, dict[str, float]]:
    """``_correlation`` with numpy's floating-point warnings off (errstate is per thread) and
    BLAS on one thread, so its products do not spin the cores the column workers are using."""
    with np.errstate(all="ignore"), single_thread_blas():
        return _correlation(cols, row_count)


def _spawn_correlation(
    cols: list[_Col], row_count: int, threads: int | None
) -> tuple[Callable[[], None], Callable[[], dict[str, dict[str, float]]]]:
    """``(start, result)``: ``start()`` begins the table's correlation on its own thread (when
    the table is large and the run is parallel), ``result()`` returns it, computing it inline
    otherwise."""
    box: list[Future[dict[str, dict[str, float]]]] = []
    ex = ThreadPoolExecutor(max_workers=1)

    def start() -> None:
        if _n_threads(threads) != 1 and row_count >= 100_000:
            box.append(ex.submit(_quiet_correlation, cols, row_count))

    def result() -> dict[str, dict[str, float]]:
        try:
            return box[0].result() if box else _correlation(cols, row_count)
        finally:
            ex.shutdown(wait=False)

    return start, result


def resolve_joint(joint: bool | None, dataset: bool = False) -> bool:
    """Whether the joint analysis (dependencies, associations) runs. An explicit ``joint`` wins;
    else a set ``SHAPE_PROFILE_JOINT`` (``0``/``false``/``no`` off, anything else on); else it is
    on for a single table and off for a dataset (several tables)."""
    if joint is not None:
        return joint
    env = os.environ.get("SHAPE_PROFILE_JOINT", "").strip().lower()
    if env:
        return env not in ("0", "false", "no")
    return not dataset


def _spawn_joint(
    cols: list[_Col], row_count: int, threads: int | None, enabled: bool
) -> tuple[Callable[[], None], Callable[[], dict[str, Any] | None]]:
    """Like ``_spawn_correlation``: the table's joint analysis (``shape.profile.joint``), on its
    own thread beside the column work when the table is large and the run is parallel."""
    box: list[Future[dict[str, Any] | None]] = []
    ex = ThreadPoolExecutor(max_workers=1)

    def run() -> dict[str, Any] | None:
        with np.errstate(all="ignore"):
            return analyze_table(cols, row_count)

    def start() -> None:
        if enabled and _n_threads(threads) != 1 and row_count >= 100_000:
            box.append(ex.submit(run))

    def result() -> dict[str, Any] | None:
        try:
            if box:
                return box[0].result()
            return run() if enabled else None
        finally:
            ex.shutdown(wait=False)

    return start, result


def _fk_values(w: _Work) -> pa.Array:
    u = w.uniques
    if w.col.kind in ("int", "uint64", "float", "bool", "objbool"):
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
    w = _profile_column(cols[i], row_count, keep_uniques=keep)
    return i, w.prof, (w.uniques if keep else None)


def _can_fork() -> bool:
    import multiprocessing as mp

    return "fork" in mp.get_all_start_methods()


def _col_cost(c: _Col) -> int:
    """Scheduling order, most expensive first: float columns run the longest distribution fit."""
    return 0 if c.kind == "float" else 1 if c.kind in ("int", "str") else 2


# Relative cost of a row, by kind, for ordering columns of different tables in one pool: measured
# on the multi-table benchmark (200k-row float, text and integer columns: about 0.4, 0.3 and
# 0.1 microseconds per row). Only the order of work depends on it, never a result.
_ROW_WEIGHT = {"float": 4, "str": 3, "dt64": 2}


def _col_work(c: _Col, row_count: int) -> int:
    """Estimated work of one column, to start the longest ones first."""
    return row_count * _ROW_WEIGHT.get(c.kind, 1)


def _profile_cols(
    cols: list[_Col],
    row_count: int,
    threads: int | None,
    keep_uniques: bool = False,
    on_ready: Callable[[], None] | None = None,
) -> list[_Work]:
    """Profile every column.  threads == 1: sequential.  Otherwise columns are spread over
    a pool: PROFILE_POOL=process (fork, copy-on-write access to the Arrow data; avoids
    GIL contention in the Python-level optimisers), PROFILE_POOL=thread, or auto (default:
    processes for wide tables with >= 3 columns per worker, threads otherwise -- a fork
    pool's start-up and result pickling only pays off when there are many columns).
    ``on_ready`` runs once the workers exist and before the first column is profiled (used to
    start the table's correlation on another thread so it overlaps the column work)."""
    n = _n_threads(threads)
    if n == 1 or len(cols) == 1:
        if on_ready:
            on_ready()
        return [_profile_column(c, row_count, keep_uniques=keep_uniques) for c in cols]
    mode = os.environ.get("PROFILE_POOL", "auto")
    if mode == "auto":
        mode = "process" if len(cols) >= 3 * n else "thread"
    if mode == "process" and not _can_fork():
        mode = "thread"  # Windows has no fork; spawn would re-import and pickle the Arrow data
    # most expensive columns first
    order = sorted(range(len(cols)), key=lambda i: _col_cost(cols[i]))
    if mode == "thread":  # few columns: the slowest one decides the finish, so it starts first
        order = sorted(range(len(cols)), key=lambda i: -_col_work(cols[i], row_count))
    if mode == "process":
        import multiprocessing as mp

        _FORK_STATE["args"] = (cols, row_count, keep_uniques)
        try:
            ctx = mp.get_context("fork")
            out: list[Any] = [None] * len(cols)
            with ctx.Pool(min(n, len(cols))) as pool:
                if on_ready:
                    on_ready()
                for i, prof, uniq in pool.imap_unordered(_fork_task, order, chunksize=1):
                    out[i] = _Work(col=cols[i], prof=prof, uniques=uniq)
            return out
        finally:
            _FORK_STATE.clear()
    if on_ready:
        on_ready()
    if row_count > 1000 and any(c.kind == "str" for c in cols):
        # the pattern sample of a string column is drawn once per row count and every string
        # column waits for it: start the draw now, beside the first columns, not 15 ms in
        threading.Thread(target=_pattern_sample, args=(row_count,), daemon=True).start()
    res: list[Any] = [None] * len(cols)

    def run(i: int) -> None:
        res[i] = _profile_column(cols[i], row_count, keep_uniques=keep_uniques)

    with ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(run, order))
    return res


def _profile_tables(
    cols_by_t: dict[str, tuple[list[_Col], int]],
    threads: int | None,
    on_ready: list[Callable[[], None]],
) -> dict[str, list[_Work]]:
    """Profile the columns of several tables. When they all fit a thread pool, one pool works
    through every column of every table, most expensive first, so small tables do not wait for
    the largest one; otherwise table by table."""
    n = _n_threads(threads)
    mode = os.environ.get("PROFILE_POOL", "auto")
    total = sum(len(c) for c, _ in cols_by_t.values())
    one_pool = (
        len(cols_by_t) > 1
        and n != 1
        and total > 1
        and mode != "process"
        and (mode == "thread" or all(len(c) < 3 * n for c, _ in cols_by_t.values()))
    )
    if not one_pool:
        return {
            t: _profile_cols(c, rc, threads, keep_uniques=True, on_ready=cb)
            for (t, (c, rc)), cb in zip(cols_by_t.items(), on_ready, strict=True)
        }
    for cb in on_ready:
        cb()
    tasks = [(t, i) for t, (c, _) in cols_by_t.items() for i in range(len(c))]
    tasks.sort(key=lambda ti: -_col_work(cols_by_t[ti[0]][0][ti[1]], cols_by_t[ti[0]][1]))
    res: dict[str, list[_Work | None]] = {t: [None] * len(c) for t, (c, _) in cols_by_t.items()}

    def run(ti: tuple[str, int]) -> None:
        t, i = ti
        cols, rc = cols_by_t[t]
        res[t][i] = _profile_column(cols[i], rc, keep_uniques=True)

    with ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(run, tasks))
    return {t: [w for w in ws if w is not None] for t, ws in res.items()}


def _sample_rows(
    cols: list[_Col], row_count: int, sample_rows: int | None
) -> tuple[list[_Col], int]:
    if sample_rows is None or row_count <= sample_rows:
        return cols, row_count
    idx = pa.array(np.random.RandomState(42).choice(row_count, size=sample_rows, replace=False))
    return [_Col(c.name, c.kind, c.arr.take(idx), c.tz, c.strict) for c in cols], sample_rows


def _finish_table(
    name: str,
    works: list[_Work],
    pk: list[str],
    fks: dict[str, str],
    row_count: int,
    corr: dict[str, dict[str, float]],
    joint: dict[str, Any] | None = None,
) -> TableProfile:
    columns = {}
    for w in works:
        p = w.prof
        found = detect_placeholders(
            p.value_counts_ext,
            null_rate=p.null_rate,
            cardinality=p.cardinality,
            row_count=row_count,
        )
        p.placeholders = found or None
        p.is_primary_key = p.name in pk
        p.is_foreign_key = p.name in fks
        p.fk_ref_table = fks.get(p.name)
        columns[p.name] = p
    return TableProfile(
        name=name,
        row_count=row_count,
        columns=columns,
        primary_key=pk,
        detected_fks=fks,
        correlation_matrix=corr if corr else None,
        correlation_truncated=isinstance(corr, _TruncatedCorr),
        joint=joint,
    )


def _profile_cols_table(
    name: str,
    cols: list[_Col],
    row_count: int,
    threads: int | None,
    sample_rows: int | None = None,
    joint: bool | None = None,
) -> TableProfile:
    cols, row_count = _sample_rows(cols, row_count, sample_rows)
    cstart, cresult = _spawn_correlation(cols, row_count, threads)
    jstart, jresult = _spawn_joint(cols, row_count, threads, resolve_joint(joint))

    def start() -> None:
        cstart()
        jstart()

    works = _profile_cols(cols, row_count, threads, on_ready=start)
    pk = _detect_primary_key(works, row_count)
    return _finish_table(name, works, pk, {}, row_count, cresult(), jresult())


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
    check_parquet([path])  # before any data page is read (#286)
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
                check_parquet([t])
                tt = pq.read_table(t, use_threads=_n_threads(threads) != 1)
                cols_by_t[name] = (_arrow_cols(tt), tt.num_rows)
        else:
            cols_by_t[name] = (_arrow_cols(t), t.num_rows)
    return profile_dataset_columns(cols_by_t, threads)


def profile_dataset_columns(
    cols_by_t: dict[str, tuple[list[_Col], int]],
    threads: int | None = None,
    joint: bool | None = None,
) -> DatasetProfile:
    """Multi-table profile (with FK detection) from already-read columns."""
    corr = {n: _spawn_correlation(c, rc, threads) for n, (c, rc) in cols_by_t.items()}
    on = resolve_joint(joint, dataset=True)
    jobs = {n: _spawn_joint(c, rc, threads, on) for n, (c, rc) in cols_by_t.items()}

    # The joint analyses start once every column is profiled, not beside the column work: a table's
    # column pool may fork, and a fork while another table's analysis runs on a thread would copy
    # that thread's locks (a single table forks before its own analysis starts).
    works = _profile_tables(cols_by_t, threads, [corr[n][0] for n in cols_by_t])
    for n in cols_by_t:
        jobs[n][0]()
    pks = {n: _detect_primary_key(w, cols_by_t[n][1]) for n, w in works.items()}
    profiles = {}
    for n, w in works.items():
        fks = _detect_fks(n, w, works, pks)
        profiles[n] = _finish_table(n, w, pks[n], fks, cols_by_t[n][1], corr[n][1](), jobs[n][1]())
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
