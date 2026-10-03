"""A learned joint model of mixed columns: plausibility, joint sampling, joint fidelity (#47).

``fit_joint`` reads columns (text, numbers, booleans) and learns a **Chow-Liu tree**: the
spanning tree of the columns that keeps the most pairwise mutual information, with a smoothed
contingency table on every edge. Numbers are cut into quantile bins first, so one model covers
mixed types. The tree gives:

* a **per-row plausibility score**, the negative log-likelihood of the row (``score``): a row whose
  values are each common but never occur together scores high;
* a report of **impossible and rare combinations** (``report``): value pairs of a tree edge that
  never occurred in the data the model learned from, found in the data being checked;
* **joint sampling** (``sample``): rows drawn root first, each column given its parent's value, so
  the pairwise structure survives (a state given the city, a category given the department);
* a **joint fidelity check** (``joint_fidelity``): total variation and Hellinger distance between
  the target's and a generated table's distributions of each column pair.

Everything is bounded: at most ``max_columns`` columns, ``max_levels`` levels per column (the rest
share one ``other`` level) and the pair tables are dense only up to ``max_levels ** 2`` cells.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.profile.joint import measures as M

Ints = npt.NDArray[np.int64]
Floats = npt.NDArray[np.float64]

OTHER = "__other__"
MISSING = "__missing__"
DEFAULT_LEVELS = 40
DEFAULT_BINS = 12
DEFAULT_COLUMNS = 24
DEFAULT_ALPHA = 0.5  # smoothing: pseudo-counts spread over a node's levels by the column's marginal
MAX_TRAIN_ROWS = 200_000


def _as_arrow(values: Any) -> pa.Array:
    if isinstance(values, pa.ChunkedArray):
        return values.combine_chunks()
    if isinstance(values, pa.Array):
        return values
    return pa.array(list(values) if not isinstance(values, np.ndarray) else values)


@dataclass
class _Encoder:
    """A column as small integer codes: the most frequent values (or quantile bins) and ``other``;
    a missing value has its own level."""

    name: str
    numeric: bool
    labels: list[Any]  # level labels (a bin's label is its ``[lo, hi)`` text)
    values: list[Any] = field(default_factory=list)  # level -> value (category) / bin edges
    bin_samples: list[Floats] = field(default_factory=list)  # numeric: training values per bin
    index: dict[Any, int] = field(default_factory=dict)
    edges: Floats = field(default_factory=lambda: np.empty(0))
    has_other: bool = True
    has_missing: bool = False
    other_pool: list[Any] = field(default_factory=list)  # rarer values, drawn for the other level

    @property
    def k(self) -> int:
        return len(self.labels)

    def labels_known(self, codes: Ints) -> npt.NDArray[np.bool_]:
        """True where the level is a real value or bin (not the catch-all, not missing)."""
        known = np.ones(len(codes), dtype=bool)
        if self.has_other and OTHER in self.index:
            known &= codes != self.index[OTHER]
        if self.has_missing:
            known &= codes != self.index[MISSING]
        return known

    def decode(self, codes: Ints, rng: np.random.Generator) -> list[Any]:
        """Values for level codes: a category's own value, a number drawn from the training
        values of its bin, a value drawn from the rarer ones for the catch-all level."""
        out: list[Any] = []
        if not self.numeric:
            other = self.index.get(OTHER, -1)
            for c in codes.tolist():
                if c == other:
                    pool = self.other_pool
                    out.append(pool[int(rng.integers(len(pool)))] if pool else None)
                else:
                    out.append(self.values[c])
            return out
        for c in codes.tolist():
            if self.has_missing and c == self.index[MISSING]:
                out.append(None)
                continue
            pool_x = self.bin_samples[c]
            out.append(float(pool_x[rng.integers(len(pool_x))]) if len(pool_x) else None)
        return out

    def encode(self, values: Any) -> Ints:
        arr = _as_arrow(values)
        n = len(arr)
        if self.numeric:
            x = pc.cast(arr, pa.float64()).to_numpy(zero_copy_only=False).astype(np.float64)
            codes = np.searchsorted(self.edges, x, side="right").astype(np.int64)
            codes[np.isnan(x)] = self.index[MISSING] if MISSING in self.index else self.k - 1
            return np.asarray(codes, dtype=np.int64)
        d = pc.dictionary_encode(arr, null_encoding="encode")
        names = d.dictionary.to_pylist()
        # NaN equals no NaN, so dict.get misses the level a NaN was fitted under: find it apart
        nan_code = next((c for v, c in self.index.items() if _is_nan(v)), self.index.get(OTHER))
        lut = np.array(
            [nan_code if _is_nan(v) else self.index.get(v, self.index[OTHER]) for v in names],
            dtype=np.int64,
        )
        out = lut[d.indices.to_numpy(zero_copy_only=False).astype(np.int64)] if n else lut[:0]
        return out


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and value != value


def _fit_encoder(name: str, values: Any, max_levels: int, bins: int) -> _Encoder:
    arr = _as_arrow(values)
    numeric = pa.types.is_integer(arr.type) or pa.types.is_floating(arr.type)
    if numeric:
        x = pc.cast(arr, pa.float64()).to_numpy(zero_copy_only=False).astype(np.float64)
        x = np.where(np.isfinite(x), x, np.nan)
        distinct = len(np.unique(x[~np.isnan(x)]))
        if distinct > max_levels:
            return _numeric_encoder(name, x, bins)
        numeric = False  # few distinct numbers: each is a level
    d = pc.dictionary_encode(arr, null_encoding="encode")
    names = d.dictionary.to_pylist()
    counts = np.bincount(
        d.indices.to_numpy(zero_copy_only=False).astype(np.int64), minlength=len(names)
    )
    order = np.argsort(-counts, kind="stable")[: max_levels - 1]
    kept = [names[int(i)] for i in order]
    enc = _Encoder(name, False, [str(v) for v in kept] + [OTHER], values=kept)
    rare = [
        names[int(i)]
        for i in np.argsort(-counts, kind="stable")[max_levels - 1 :]
        if names[int(i)] is not None
    ]
    enc.other_pool = rare[:1000]
    enc.index = {v: i for i, v in enumerate(kept)}
    enc.index[OTHER] = len(kept)
    return enc


def _numeric_encoder(name: str, x: Floats, bins: int) -> _Encoder:
    ok = x[~np.isnan(x)]
    edges = np.unique(np.quantile(ok, np.linspace(0, 1, bins + 1)[1:-1]))
    codes = np.searchsorted(edges, ok, side="right")
    k = len(edges) + 1
    labels = []
    lo = -math.inf
    for i in range(k):
        hi = edges[i] if i < len(edges) else math.inf
        labels.append(f"[{lo:g}, {hi:g})")
        lo = hi
    samples = [ok[codes == i][:200] for i in range(k)]
    enc = _Encoder(name, True, labels, bin_samples=samples, edges=edges, has_other=False)
    if np.isnan(x).any():
        enc.labels.append(MISSING)
        enc.index[MISSING] = k
        enc.has_missing = True
        enc.bin_samples.append(np.empty(0))
    return enc


@dataclass
class ChowLiuModel:
    """A Chow-Liu tree over encoded columns (see the module docstring)."""

    columns: tuple[str, ...]
    encoders: dict[str, _Encoder]
    parent: dict[str, str | None]  # tree: column -> parent (None for the root)
    order: tuple[str, ...]  # parents before children
    root_logp: Floats
    cond_logp: dict[str, Floats]  # child -> log P(child | parent), shape (k_parent, k_child)
    pair_counts: dict[str, Ints]  # child -> training count table, shape (k_parent, k_child)
    rows: int
    alpha: float
    train_nll: Floats = field(default_factory=lambda: np.empty(0))

    def _codes(self, data: Mapping[str, Any] | pa.Table) -> dict[str, Ints]:
        if isinstance(data, pa.Table):
            data = {n: data[n] for n in data.column_names}
        missing = [c for c in self.columns if c not in data]
        if missing:
            raise ValueError(f"the data has no column {missing[0]!r}")
        return {c: self.encoders[c].encode(data[c]) for c in self.columns}

    def score(self, data: Mapping[str, Any] | pa.Table) -> Floats:
        """The negative log-likelihood of each row (nats): higher is less plausible."""
        codes = self._codes(data)
        nll = np.zeros(len(codes[self.columns[0]]), dtype=np.float64)
        for col in self.order:
            p = self.parent[col]
            if p is None:
                nll -= self.root_logp[codes[col]]
            else:
                nll -= self.cond_logp[col][codes[p], codes[col]]
        return nll

    def threshold(self, quantile: float = 0.999) -> float:
        """The score above which a row is rarer than ``quantile`` of the training rows."""
        return float(np.quantile(self.train_nll, quantile)) if len(self.train_nll) else math.inf

    def report(
        self, data: Mapping[str, Any] | pa.Table, *, quantile: float = 0.999, top: int = 20
    ) -> dict[str, Any]:
        """The share of implausible rows, the impossible combinations (a value pair of a tree edge
        never seen in training) and the least plausible rows."""
        codes = self._codes(data)
        scores = self.score(data)
        cutoff = self.threshold(quantile)
        flagged = scores > cutoff
        combos: list[dict[str, Any]] = []
        impossible_rows = np.zeros(len(scores), dtype=bool)
        for col in self.order:
            p = self.parent[col]
            if p is None:
                continue
            counts = self.pair_counts[col]
            unseen = counts[codes[p], codes[col]] == 0
            # a pair of two values that are each known (not the catch-all level) and never met
            known = (self.encoders[p].labels_known(codes[p])) & (
                self.encoders[col].labels_known(codes[col])
            )
            bad = unseen & known
            impossible_rows |= bad
            if not bad.any():
                continue
            pairs, n = np.unique(
                np.stack([codes[p][bad], codes[col][bad]], axis=1), axis=0, return_counts=True
            )
            for (a, b), cnt in zip(pairs, n, strict=True):
                combos.append(
                    {
                        "columns": [p, col],
                        "values": [
                            self.encoders[p].labels[int(a)],
                            self.encoders[col].labels[int(b)],
                        ],
                        "rows": int(cnt),
                        "training_rows": int(counts[int(a)].sum()),
                    }
                )
        combos.sort(key=lambda c: (-c["rows"], c["columns"], c["values"]))
        worst = np.argsort(-scores, kind="stable")[:top]
        return {
            "rows": int(len(scores)),
            "implausible_rows": int(flagged.sum()),
            "implausible_rate": float(flagged.mean()) if len(scores) else 0.0,
            "impossible_rows": int(impossible_rows.sum()),
            "impossible_rate": float(impossible_rows.mean()) if len(scores) else 0.0,
            "threshold_nll": cutoff,
            "mean_nll": float(scores.mean()) if len(scores) else 0.0,
            "impossible_combinations": combos[:top],
            "least_plausible_rows": [int(i) for i in worst],
        }

    def sample(self, n: int, seed: int = 0) -> dict[str, list[Any]]:
        """``n`` rows drawn from the tree, root first; numbers are drawn from the training values
        of their bin. Deterministic for a seed."""
        rng = np.random.default_rng(seed)
        codes: dict[str, Ints] = {}
        for col in self.order:
            p = self.parent[col]
            if p is None:
                prob = np.exp(self.root_logp)
                codes[col] = rng.choice(len(prob), size=n, p=prob / prob.sum()).astype(np.int64)
                continue
            # draw from what was observed: a level seen with this parent is drawn in proportion to
            # its count; only a parent never seen falls back to the smoothed table
            counts = self.pair_counts[col].astype(np.float64)
            table = np.where(
                counts.sum(axis=1, keepdims=True) > 0, counts, np.exp(self.cond_logp[col])
            )
            cum = np.cumsum(table, axis=1)
            cum /= cum[:, -1:]
            u = rng.random(n)
            parent_codes = codes[p]
            picked = np.empty(n, dtype=np.int64)
            for level in np.unique(parent_codes):
                m = parent_codes == level
                picked[m] = np.minimum(
                    np.searchsorted(cum[level], u[m], side="right"), cum.shape[1] - 1
                )
            codes[col] = picked
        out: dict[str, list[Any]] = {}
        for col in self.columns:
            out[col] = self.encoders[col].decode(codes[col], rng)
        return out


def _marginal(codes: Ints, k: int) -> Floats:
    """The smoothed share of each level (every level keeps a little mass)."""
    counts = np.bincount(codes, minlength=k) + 0.5
    return np.asarray(counts / counts.sum(), dtype=np.float64)


def fit_joint(
    data: Mapping[str, Any],
    columns: Sequence[str] | None = None,
    *,
    max_levels: int = DEFAULT_LEVELS,
    bins: int = DEFAULT_BINS,
    max_columns: int = DEFAULT_COLUMNS,
    alpha: float = DEFAULT_ALPHA,
) -> ChowLiuModel:
    """Learn the joint model of ``columns`` (default: every column, up to ``max_columns``) of
    ``data`` (a mapping of name to values, or a ``pyarrow.Table``)."""
    if isinstance(data, pa.Table):
        data = {n: data[n] for n in data.column_names}
    names = list(columns) if columns is not None else list(data)
    names = names[:max_columns]
    if len(names) < 1:
        raise ValueError("fit_joint needs at least one column")
    n_rows = len(_as_arrow(data[names[0]]))
    idx: Any = None
    if n_rows > MAX_TRAIN_ROWS:
        idx = np.sort(np.random.RandomState(7).choice(n_rows, MAX_TRAIN_ROWS, replace=False))
    sampled = {
        c: (_as_arrow(data[c]) if idx is None else _as_arrow(data[c]).take(pa.array(idx)))
        for c in names
    }
    enc = {c: _fit_encoder(c, sampled[c], max_levels, bins) for c in names}
    codes = {c: enc[c].encode(sampled[c]) for c in names}
    n = len(codes[names[0]])
    # pairwise mutual information -> maximum spanning tree (Prim), rooted at the first column
    k = len(names)
    mi = np.zeros((k, k))
    for i in range(k):
        for j in range(i + 1, k):
            t = M.contingency(codes[names[i]], codes[names[j]], enc[names[i]].k, enc[names[j]].k)
            mi[i, j] = mi[j, i] = M.mutual_information(t)
    parent: dict[str, str | None] = {names[0]: None}
    order = [names[0]]
    best = {j: (mi[0, j], 0) for j in range(1, k)}
    while best:
        j = max(best, key=lambda x: (best[x][0], -x))
        parent[names[j]] = names[best[j][1]]
        order.append(names[j])
        del best[j]
        for m in best:
            if mi[j, m] > best[m][0]:
                best[m] = (mi[j, m], j)
    root = names[0]
    root_logp = np.log(_marginal(codes[root], enc[root].k))
    cond: dict[str, Floats] = {}
    pair_counts: dict[str, Ints] = {}
    for col in order[1:]:
        p = parent[col]
        assert p is not None
        t = M.contingency(codes[p], codes[col], enc[p].k, enc[col].k)
        pair_counts[col] = t
        sm = t + alpha * _marginal(codes[col], enc[col].k)[None, :]
        cond[col] = np.log(sm / sm.sum(axis=1, keepdims=True))
    model = ChowLiuModel(
        tuple(names), enc, parent, tuple(order), root_logp, cond, pair_counts, n, alpha
    )
    model.train_nll = model.score({c: sampled[c] for c in names})
    return model


def _pair_distribution(a: Ints, b: Ints, ka: int, kb: int) -> Floats:
    t = M.contingency(a, b, ka, kb).astype(np.float64)
    total = t.sum()
    return t / total if total > 0 else t


def joint_fidelity(
    target: Mapping[str, Any],
    generated: Mapping[str, Any],
    pairs: Sequence[tuple[str, str]] | None = None,
    *,
    max_levels: int = DEFAULT_LEVELS,
    bins: int = DEFAULT_BINS,
    max_pairs: int = 100,
) -> dict[str, Any]:
    """How close the generated table's column pairs are to the target's: for each pair the total
    variation distance (``tvd``, 0 to 1) and the Hellinger distance (0 to 1) between the two
    joint distributions, on the levels and bins the target defines (a value the target never
    holds shares one ``other`` level). ``pairs`` default to every pair of the shared columns."""
    if isinstance(target, pa.Table):
        target = {n: target[n] for n in target.column_names}
    if isinstance(generated, pa.Table):
        generated = {n: generated[n] for n in generated.column_names}
    shared = [c for c in target if c in generated]
    chosen = (
        list(pairs)
        if pairs is not None
        else [(a, b) for i, a in enumerate(shared) for b in shared[i + 1 :]]
    )
    chosen = chosen[:max_pairs]
    needed = sorted({c for p in chosen for c in p})
    enc = {c: _fit_encoder(c, target[c], max_levels, bins) for c in needed}
    t_codes = {c: enc[c].encode(target[c]) for c in needed}
    g_codes = {c: enc[c].encode(generated[c]) for c in needed}
    rows: list[dict[str, Any]] = []
    for a, b in chosen:
        p = _pair_distribution(t_codes[a], t_codes[b], enc[a].k, enc[b].k)
        q = _pair_distribution(g_codes[a], g_codes[b], enc[a].k, enc[b].k)
        tvd = 0.5 * float(np.abs(p - q).sum())
        hell = float(math.sqrt(0.5 * float(((np.sqrt(p) - np.sqrt(q)) ** 2).sum())))
        rows.append({"a": a, "b": b, "tvd": round(tvd, 6), "hellinger": round(hell, 6)})
    worst = max(rows, key=lambda r: r["tvd"]) if rows else None
    return {
        "pairs": rows,
        "mean_tvd": round(sum(r["tvd"] for r in rows) / len(rows), 6) if rows else 0.0,
        "max_tvd": worst["tvd"] if worst else 0.0,
        "max_hellinger": max((r["hellinger"] for r in rows), default=0.0),
        "worst_pair": [worst["a"], worst["b"]] if worst else None,
    }
