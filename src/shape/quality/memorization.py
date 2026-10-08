"""The memorization gate: does generated data reproduce rows of the data it was made from?

``MemorizationGate`` compares each generated table with the source table of the same name and
reports, per table, the exact-match rate and the nearest-neighbour distance between generated rows
and source rows. It fails on any reproduced source row in a column classified ``CONFIDENTIAL`` or
above (``fail_at`` moves the threshold). The report names the rows that matched (their index in the
generated table) and the columns, never a value.

How a row counts as reproduced:

* The key is the table's restricted columns (classified at or above ``fail_at``) that both tables
  have. Text, integer, date and other non-numeric columns form the key; numeric columns join it
  only when no restricted column is non-numeric, because a noise-added copy (the ``bootstrap``
  strategy jitters its numbers) has different numbers and the same text. Numbers still count in the
  nearest-neighbour distance. With no restricted column the key is every shared column and the
  result is informational: it cannot fail the gate, and a warning says so.
* A generated row is reproduced when its key equals the key of a source row. Null and NaN never
  match. A key that more than one source row has does not identify anyone, so it does not count.
* The nearest-neighbour distance is Euclidean over the numeric columns both tables share, each
  standardised by the source's standard deviation (nulls filled with the source median). It is
  reported always; ``min_nn_distance`` makes it a second failure criterion.

The row cap ``max_rows`` limits only the nearest-neighbour search (an even stride over the
generated rows, and over the source above 50,000 rows); exact matching sees every row.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.privacy.classification import DEFAULT_TAXONOMY

from .gates import GateResult, ValidationContext, ValidationGate

DEFAULT_FAIL_AT = "CONFIDENTIAL"
DEFAULT_MAX_ROWS = 5000
SOURCE_ROW_CAP = 50_000
_LISTED_ROWS = 1000
_CHUNK = 256


def _is_numeric(t: pa.DataType) -> bool:
    return bool(pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t))


def _plain(col: pa.ChunkedArray) -> pa.ChunkedArray:
    """A column without NaN (as null), dictionary encoding or nesting, ready to join on."""
    t = col.type
    if pa.types.is_dictionary(t):
        return _plain(col.cast(t.value_type))
    if pa.types.is_floating(t):
        return pc.if_else(pc.is_nan(col), pa.scalar(None, t), col)
    if pa.types.is_nested(t):
        values = [json.dumps(v, default=str, sort_keys=True) for v in col.to_pylist()]
        return pa.chunked_array([pa.array(values, pa.string())])
    return col


def _comparable(g: pa.ChunkedArray, s: pa.ChunkedArray) -> tuple[pa.ChunkedArray, pa.ChunkedArray]:
    g, s = _plain(g), _plain(s)
    if _is_numeric(g.type) and _is_numeric(s.type):
        return g.cast(pa.float64()), s.cast(pa.float64())
    if pa.types.is_string(g.type) or pa.types.is_large_string(g.type):
        if pa.types.is_string(s.type) or pa.types.is_large_string(s.type):
            return g.cast(pa.string()), s.cast(pa.string())
    if g.type == s.type:
        return g, s
    try:
        return g.cast(s.type), s
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return g.cast(pa.string()), s.cast(pa.string())


def reproduced_rows(
    generated: pa.Table, source: pa.Table, columns: list[str], *, identifying_only: bool = True
) -> list[int]:
    """Indices of the generated rows whose values in ``columns`` equal a source row's.
    A source key that several source rows share is skipped when ``identifying_only``."""
    gen_cols: dict[str, pa.ChunkedArray] = {}
    src_cols: dict[str, pa.ChunkedArray] = {}
    for i, name in enumerate(columns):
        gen_cols[f"k{i}"], src_cols[f"k{i}"] = _comparable(
            generated.column(name), source.column(name)
        )
    keys = list(gen_cols)
    src = pa.table(src_cols)
    if identifying_only:
        counted = src.append_column("n", pa.array(np.ones(src.num_rows, dtype=np.int64)))
        grouped = counted.group_by(keys).aggregate([("n", "sum")])
        grouped = grouped.filter(pc.equal(grouped["n_sum"], 1)).select(keys)
    else:
        grouped = src.group_by(keys).aggregate([])
    gen = pa.table(gen_cols).append_column("g", pa.array(np.arange(generated.num_rows)))
    joined = gen.join(grouped, keys=keys, join_type="inner")
    return sorted(int(i) for i in joined["g"].to_pylist())


def _numeric_matrix(
    generated: pa.Table, source: pa.Table, columns: list[str]
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], list[str]]:
    """The shared numeric columns of both tables as standardised float matrices."""
    g_parts: list[npt.NDArray[np.float64]] = []
    s_parts: list[npt.NDArray[np.float64]] = []
    used: list[str] = []
    for name in columns:
        if not (
            _is_numeric(source.schema.field(name).type)
            and _is_numeric(generated.schema.field(name).type)
        ):
            continue
        g, s = _plain(generated.column(name)), _plain(source.column(name))
        sv = np.asarray(s.cast(pa.float64()).to_numpy(zero_copy_only=False), dtype=np.float64)
        valid = ~np.isnan(sv)
        if not valid.any():
            continue
        mean, std, median = (
            float(sv[valid].mean()),
            float(sv[valid].std()),
            float(np.median(sv[valid])),
        )
        if not std > 0:
            continue
        gv = np.asarray(g.cast(pa.float64()).to_numpy(zero_copy_only=False), dtype=np.float64)
        g_parts.append((np.where(np.isnan(gv), median, gv) - mean) / std)
        s_parts.append((np.where(np.isnan(sv), median, sv) - mean) / std)
        used.append(name)
    if not used:
        return np.empty((0, 0)), np.empty((0, 0)), []
    return np.column_stack(g_parts), np.column_stack(s_parts), used


def _stride(n: int, cap: int) -> npt.NDArray[np.int64]:
    if n <= cap:
        return np.arange(n, dtype=np.int64)
    return np.unique(np.linspace(0, n - 1, cap).astype(np.int64))


def nearest_distances(
    g: npt.NDArray[np.float64], s: npt.NDArray[np.float64]
) -> npt.NDArray[np.float64]:
    """Euclidean distance from each row of ``g`` to its nearest row of ``s`` (exact for the
    neighbour found, so a copy is at distance 0.0)."""
    out = np.empty(len(g), dtype=np.float64)
    s_sq = (s * s).sum(axis=1)
    for start in range(0, len(g), _CHUNK):
        block = g[start : start + _CHUNK]
        d2 = (block * block).sum(axis=1)[:, None] + s_sq[None, :] - 2.0 * block @ s.T
        nearest = d2.argmin(axis=1)
        diff = block - s[nearest]
        out[start : start + _CHUNK] = np.sqrt((diff * diff).sum(axis=1))
    return out


class MemorizationGate(ValidationGate):
    """Fails when a generated row reproduces a source row in a column classified at or above
    ``memorization.fail_at`` (default ``CONFIDENTIAL``). Needs ``source_tables`` in the context;
    reads ``classifications`` (``"table.column"`` to level) and ``memorization`` (``fail_at``,
    ``min_nn_distance``, ``max_rows``) from ``config``."""

    name = "memorization"

    def check(self, context: ValidationContext) -> GateResult:
        options: dict[str, Any] = dict(context.config.get("memorization") or {})
        fail_at = DEFAULT_TAXONOMY.canonical(options.get("fail_at", DEFAULT_FAIL_AT))
        min_nn = options.get("min_nn_distance")
        max_rows = int(options.get("max_rows", DEFAULT_MAX_ROWS))
        classes: dict[str, str] = dict(context.config.get("classifications") or {})
        errors: list[str] = []
        warnings: list[str] = []
        tables: dict[str, Any] = {}
        for name, gen in context.tables.items():
            src = context.source_tables.get(name)
            if src is None:
                warnings.append(f"{name}: no source table of that name; not compared")
                continue
            shared = [c for c in gen.column_names if c in src.column_names]
            if not shared or gen.num_rows == 0 or src.num_rows == 0:
                warnings.append(f"{name}: no shared columns or rows with the source; not compared")
                continue
            tables[name] = self._table(
                name, gen, src, shared, classes, fail_at, min_nn, max_rows, errors, warnings
            )
        return GateResult(
            self.name,
            not errors,
            errors,
            warnings,
            {"fail_at": fail_at, "min_nn_distance": min_nn, "tables": tables},
        )

    @staticmethod
    def _table(
        name: str,
        gen: pa.Table,
        src: pa.Table,
        shared: list[str],
        classes: dict[str, str],
        fail_at: str,
        min_nn: float | None,
        max_rows: int,
        errors: list[str],
        warnings: list[str],
    ) -> dict[str, Any]:
        restricted = [
            c
            for c in shared
            if f"{name}.{c}" in classes
            and DEFAULT_TAXONOMY.at_least(classes[f"{name}.{c}"], fail_at)
        ]
        pool = restricted or shared
        textual = [c for c in pool if not _is_numeric(src.schema.field(c).type)]
        key = textual or pool
        rows = reproduced_rows(gen, src, key)
        detail: dict[str, Any] = {
            "rows": gen.num_rows,
            "source_rows": src.num_rows,
            "columns": sorted(key),
            "restricted": bool(restricted),
            "reproduced_rows": len(rows),
            "exact_match_rate": len(rows) / gen.num_rows,
            "reproduced_row_indices": rows[:_LISTED_ROWS],
        }
        if len(rows) > _LISTED_ROWS:
            detail["reproduced_row_indices_truncated"] = True
        if restricted and rows:
            shown = ", ".join(str(i) for i in rows[:10]) + (", ..." if len(rows) > 10 else "")
            errors.append(
                f"{name}: {len(rows)} of {gen.num_rows} generated rows reproduce a source row "
                f"on the {fail_at}+ columns [{', '.join(sorted(key))}] (rows {shown})"
            )
        if not restricted:
            warnings.append(
                f"{name}: no column is classified {fail_at} or above, so reproduced rows are "
                'reported but cannot fail the gate (set "classifications" in the verify '
                "configuration)"
            )
        g_mat, s_mat, used = _numeric_matrix(gen, src, shared)
        if used:
            g_idx = _stride(len(g_mat), max_rows)
            s_idx = _stride(len(s_mat), SOURCE_ROW_CAP)
            dist = nearest_distances(g_mat[g_idx], s_mat[s_idx])
            detail["nn_distance"] = {
                "columns": used,
                "rows_checked": int(len(g_idx)),
                "min": float(dist.min()),
                "p05": float(np.percentile(dist, 5)),
                "median": float(np.median(dist)),
            }
            if min_nn is not None and detail["nn_distance"]["min"] < min_nn:
                closer = int((dist < min_nn).sum())
                errors.append(
                    f"{name}: nearest-neighbour distance {detail['nn_distance']['min']:.6g} is "
                    f"below the minimum {min_nn:g} "
                    f"({closer} of {len(g_idx)} rows checked are closer)"
                )
        else:
            detail["nn_distance"] = None
        return detail
