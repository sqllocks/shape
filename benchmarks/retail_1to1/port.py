"""Vectorized 1:1 port of Spindle's retail domain (numpy + pyarrow + stdlib only).

The port does NOT hard-code the retail schema.  At runtime it reads, from the
Spindle checkout given by ``spindle_root``:

* ``domains/retail/retail.py``            -> the ``schema_dict`` literal inside
  ``RetailDomain._build_schema`` (evaluated with a stub ``self`` whose ``_dist`` /
  ``_ratio`` resolve against ``profiles/default.json`` exactly like
  ``Domain._dist`` / ``Domain._ratio``).  Scales, derived counts, weights,
  distribution params, business rules: all come from there.
* ``domains/retail/reference_data/*.json`` and ``domains/_shared/reference_data``
  (same search order as ``reference_data._load_dataset``).
* ``engine/data/names.py``                 -> FIRST_NAMES, LAST_NAMES, STREET_NAMES,
  EMAIL_DOMAINS pools (loaded from source, bytecode writing disabled).
* ``engine/strategies/native.py``          -> _US_CITIES, _US_STATES,
  _STREET_SUFFIXES pools (extracted with ``ast``; the module itself needs Spindle).

It then runs a small vectorized engine that mirrors Spindle's engine:
table order (DependencyResolver: Kahn + sorted queue), per-table column order
(TableGenerator._order_columns), per-strategy semantics, null masking
(Strategy.apply_nulls), compute phase (sum_children back-fill) and
BusinessRulesEngine.fix_violations.  Every strategy below cites the Spindle
function it replicates.  No per-row Python loops on the hot path.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

DEFAULT_SPINDLE_ROOT = "/home/user/sqllocks/spindle"

NS_PER_DAY = 86_400 * 1_000_000_000
US_PER_DAY = 86_400 * 1_000_000
_UNIT_PER_DAY = {"ns": NS_PER_DAY, "us": US_PER_DAY}
_UNIT_TO_NS = {"ns": 1, "us": 1000}


# ═══════════════════════════════════════════════════════════════════════════
# Loading Spindle's config / pools / reference data (read-only)
# ═══════════════════════════════════════════════════════════════════════════

class _DomainStub:
    """Stands in for ``self`` inside RetailDomain._build_schema (Domain base)."""

    def __init__(self, profile: dict, schema_mode: str = "3nf"):
        self._profile = profile
        self._schema_mode = schema_mode

    def _dist(self, key, default=None):
        return self._profile.get("distributions", {}).get(key, default)

    def _ratio(self, key, default=1.0):
        return self._profile.get("ratios", {}).get(key, default)


def load_schema(spindle_root: str | Path, profile: str = "default",
                schema_mode: str = "3nf") -> dict:
    pkg = Path(spindle_root) / "sqllocks_spindle"
    dom = pkg / "domains" / "retail"
    json_schema = dom / f"retail_{schema_mode}.spindle.json"
    if json_schema.exists():  # Domain.get_schema prefers a JSON file if present
        return json.loads(json_schema.read_text())
    prof = json.loads((dom / "profiles" / f"{profile}.json").read_text())
    src_path = dom / "retail.py"
    tree = ast.parse(src_path.read_text(), filename=str(src_path))
    node = None
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "_build_schema":
            for st in ast.walk(fn):
                if (isinstance(st, ast.Assign) and len(st.targets) == 1
                        and isinstance(st.targets[0], ast.Name)
                        and st.targets[0].id == "schema_dict"):
                    node = st.value
    if node is None:
        raise RuntimeError("schema_dict not found in retail.py")
    code = compile(ast.Expression(node), str(src_path), "eval")
    return eval(code, {"__builtins__": {}}, {"self": _DomainStub(prof, schema_mode)})


def _load_module_from_file(name: str, path: Path):
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # never write into the Spindle checkout
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.dont_write_bytecode = old


def _ast_constants(path: Path, names: set[str]) -> dict[str, Any]:
    tree = ast.parse(path.read_text(), filename=str(path))
    out = {}
    for st in tree.body:
        if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name):
            nm = st.targets[0].id
            if nm in names:
                v = st.value
                if isinstance(v, ast.Call):  # np.array([...], dtype=object)
                    v = v.args[0]
                out[nm] = ast.literal_eval(v)
    return out


class Pools:
    """Native provider pools — the exact arrays NativeStrategy samples from."""

    def __init__(self, spindle_root: str | Path):
        eng = Path(spindle_root) / "sqllocks_spindle" / "engine"
        names = _load_module_from_file("_spindle_names_port", eng / "data" / "names.py")
        self.first_names = list(names.FIRST_NAMES)
        self.last_names = list(names.LAST_NAMES)
        self.street_names = list(names.STREET_NAMES)
        self.email_domains = list(names.EMAIL_DOMAINS)
        nat = _ast_constants(eng / "strategies" / "native.py",
                             {"_US_STATES", "_US_CITIES", "_STREET_SUFFIXES"})
        self.us_states = list(nat["_US_STATES"])
        self.us_cities = list(nat["_US_CITIES"])
        self.street_suffixes = list(nat["_STREET_SUFFIXES"])


class RefData:
    """reference_data._load_dataset: domain dir -> _shared -> data dir -> any domain."""

    def __init__(self, spindle_root: str | Path, domain: str = "retail"):
        self.pkg = Path(spindle_root) / "sqllocks_spindle"
        self.domain = domain
        self._cache: dict[str, Any] = {}
        self._col_cache: dict[str, dict[str, Any]] = {}

    def load(self, name: str):
        if name in self._cache:
            return self._cache[name]
        cands = [self.pkg / "domains" / self.domain / "reference_data" / f"{name}.json",
                 self.pkg / "domains" / "_shared" / "reference_data" / f"{name}.json",
                 self.pkg / "data" / f"{name}.json"]
        droot = self.pkg / "domains"
        for e in sorted(droot.iterdir()) if droot.exists() else []:
            if e.is_dir() and not e.name.startswith(("_", ".")):
                cands.append(e / "reference_data" / f"{name}.json")
        for p in cands:
            if p.exists():
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                self._cache[name] = data
                return data
        raise FileNotFoundError(name)

    def columns(self, name: str) -> dict[str, Any]:
        """Columnar view of a list-of-dicts dataset (numeric -> numpy, str -> arrow)."""
        if name in self._col_cache:
            return self._col_cache[name]
        data = self.load(name)
        cols = {}
        for f in data[0].keys():
            vals = [r[f] for r in data]
            if isinstance(vals[0], str):
                cols[f] = pa.array(vals, type=pa.string())
            else:  # np.array(values) in record_sample -> float64 / int64
                cols[f] = np.array(vals)
        self._col_cache[name] = cols
        return cols


# ═══════════════════════════════════════════════════════════════════════════
# Column container
# ═══════════════════════════════════════════════════════════════════════════

class Col:
    """A generated column: numpy array (numeric/bool/datetime64) or arrow string
    array, plus an optional null mask (True = null)."""

    __slots__ = ("v", "mask", "src_idx", "src_pool")

    def __init__(self, v, mask=None, src_idx=None, src_pool=None):
        self.v = v
        self.mask = mask
        self.src_idx = src_idx      # pool indices (for email reuse of first/last)
        self.src_pool = src_pool

    def numeric(self) -> np.ndarray:
        """Float view with NaN for nulls (what pandas/eval sees in Spindle)."""
        a = np.asarray(self.v, dtype=np.float64)
        if self.mask is not None and self.mask.any():
            a = a.copy()
            a[self.mask] = np.nan
        return a

    def to_arrow(self) -> pa.Array:
        v = self.v
        if isinstance(v, (pa.Array, pa.ChunkedArray)):
            if self.mask is not None and self.mask.any():
                return pc.if_else(pa.array(self.mask), pa.scalar(None, v.type), v)
            return v
        if np.issubdtype(v.dtype, np.datetime64):
            unit = np.datetime_data(v.dtype)[0]
            return pa.array(v.view(np.int64), type=pa.timestamp(unit),
                            mask=self.mask if self.mask is not None and self.mask.any() else None)
        m = self.mask if self.mask is not None and self.mask.any() else None
        return pa.array(v, mask=m)


_ALIAS_CACHE: dict = {}


def _alias_table(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Walker/Vose alias table (loop over categories only, cached)."""
    key = (len(p), p.tobytes())
    if key in _ALIAS_CACHE:
        return _ALIAS_CACHE[key]
    k = len(p)
    q = (np.asarray(p, dtype=np.float64) / np.sum(p) * k).tolist()
    prob = [1.0] * k
    alias = list(range(k))
    small = [i for i in range(k) if q[i] < 1.0]
    large = [i for i in range(k) if q[i] >= 1.0]
    while small and large:
        s_, l_ = small.pop(), large.pop()
        prob[s_], alias[s_] = q[s_], l_
        q[l_] = q[l_] + q[s_] - 1.0
        (small if q[l_] < 1.0 else large).append(l_)
    out = (np.array(prob), np.array(alias, dtype=np.int64))
    _ALIAS_CACHE[key] = out
    return out


def _categorical(rng: np.random.Generator, p: np.ndarray, n: int) -> np.ndarray:
    """Same distribution as rng.choice(len(p), size=n, p=p) (which is cdf + searchsorted).

    For <= 8 categories the cdf/searchsorted form is used (it is what numpy does and
    is cheap for tiny tables); for larger tables the exact alias method avoids a
    cache-unfriendly binary search per draw."""
    p = np.asarray(p, dtype=np.float64)
    if len(p) <= 8:
        cdf = np.cumsum(p)
        cdf /= cdf[-1]
        return np.minimum(np.searchsorted(cdf, rng.random(n), side="right"), len(p) - 1)
    prob, alias = _alias_table(p)
    x = rng.random(n) * len(p)
    j = x.astype(np.int64)
    np.minimum(j, len(p) - 1, out=j)
    x -= j  # fractional part: an independent U(0,1)
    return np.where(x < prob[j], j, alias[j])


def _truncated_zipf(rng: np.random.Generator, alpha: float, n_max: int, size: int) -> np.ndarray:
    """Values in 1..n_max with P(k) proportional to k^-alpha (== rejection-truncated rng.zipf)."""
    return _categorical(rng, np.arange(1, n_max + 1, dtype=np.float64) ** -float(alpha), size) + 1


def _take_str(pool: pa.Array, idx: np.ndarray) -> pa.Array:
    return pc.take(pool, pa.array(idx))


def _int_to_str(a: np.ndarray) -> pa.Array:
    return pc.cast(pa.array(a), pa.string())


def _join(*parts) -> pa.Array:
    return pc.binary_join_element_wise(*parts, "")


def _dt_to_ns(v: np.ndarray) -> np.ndarray:
    unit = np.datetime_data(v.dtype)[0]
    return v.view(np.int64) * _UNIT_TO_NS[unit]


# ═══════════════════════════════════════════════════════════════════════════
# Engine
# ═══════════════════════════════════════════════════════════════════════════

class Ctx:
    def __init__(self, rng, table_name, n, engine):
        self.rng = rng
        self.table_name = table_name
        self.n = n
        self.cur: dict[str, Col] = {}      # public columns, insertion ordered
        self.hidden: dict[str, Col] = {}   # _rs_ / _sr_ stashes
        self.engine = engine


class Engine:
    def __init__(self, schema: dict, pools: Pools, ref: RefData, seed: int):
        self.schema = schema
        self.pools = pools
        self.ref = ref
        self.seed = seed
        self.model = schema["model"]
        self.rng = np.random.default_rng(seed)   # IDManager / rules RNG
        self.tables: dict[str, dict[str, Col]] = {}
        self._pk_pos_cache: dict[tuple, Any] = {}
        self._arrow_pools: dict[str, Any] = {}
        self.timings: dict[str, float] = {}
        self.col_timings: dict[str, float] = {}

    # ── row counts (generator.calculate_row_counts) ───────────────────────
    def row_counts(self, scale: str) -> dict[str, int]:
        gen = self.schema.get("generation", {})
        counts: dict[str, int] = {}
        for t, c in gen.get("scales", {}).get(scale, {}).items():
            counts[t] = c
        for t, d in gen.get("derived_counts", {}).items():
            if "fixed" in d:
                if isinstance(d["fixed"], int):
                    counts[t] = d["fixed"]
            elif "per_parent" in d:
                counts[t] = int(counts.get(d["per_parent"], 100) * d.get("ratio", d.get("mean", 1.0)))
            elif "per_year" in d:
                dr = self.model.get("date_range")
                if dr:
                    years = int(dr.get("end", "2025")[:4]) - int(dr.get("start", "2022")[:4]) + 1
                    counts[t] = d["per_year"] * years
        for t in self.schema["tables"]:
            counts.setdefault(t, 100)
        return counts

    # ── table order (schema.dependency.DependencyResolver) ────────────────
    @staticmethod
    def _fk_ref_table(col: dict) -> str | None:
        g = col.get("generator", {})
        if g.get("strategy") != "foreign_key":
            return None
        ref = g.get("ref", "")
        return ref.split(".")[0] if "." in ref else None

    def table_order(self) -> list[str]:
        tables = self.schema["tables"]
        graph = {}
        for tn, t in tables.items():
            deps = set()
            for c in t["columns"].values():
                r = self._fk_ref_table(c)
                if r and r != tn:
                    deps.add(r)
            graph[tn] = deps
        for rel in self.schema.get("relationships", []):
            if rel["parent"] != rel["child"] and rel.get("type") != "self_referencing" and rel["child"] in graph:
                graph[rel["child"]].add(rel["parent"])
        indeg = {k: len(v) for k, v in graph.items()}
        queue = [k for k, d in indeg.items() if d == 0]
        out = []
        while queue:
            queue.sort()
            node = queue.pop(0)
            out.append(node)
            for other, deps in graph.items():
                if node in deps:
                    indeg[other] -= 1
                    if indeg[other] == 0:
                        queue.append(other)
        return out

    def level_order(self) -> list[str]:
        """Spindle._group_by_dep_level flattened: the order tables are generated
        in and the insertion order of GenerationResult.tables."""
        tables = self.schema["tables"]
        deps = {tn: {r for c in t["columns"].values() if (r := self._fk_ref_table(c)) and r != tn} & set(tables)
                for tn, t in tables.items()}
        assigned: set[str] = set()
        out: list[str] = []
        remaining = [t for t in self.table_order() if t in tables]
        while remaining:
            level = [t for t in remaining if deps.get(t, set()) <= assigned] or [remaining[0]]
            out.extend(level)
            assigned.update(level)
            remaining = [t for t in remaining if t not in assigned]
        return out

    # ── column order (TableGenerator._order_columns) ──────────────────────
    @staticmethod
    def column_order(table: dict) -> list[str]:
        pk = table.get("primary_key", [])
        pk_cols, fk_cols, ind, dep, comp = [], [], [], [], []
        for cn, c in table["columns"].items():
            s = c.get("generator", {}).get("strategy", "")
            if cn in pk and s in ("sequence", "uuid"):
                pk_cols.append(cn)
            elif s in ("foreign_key", "composite_foreign_key"):
                fk_cols.append(cn)
            elif s in ("formula", "lookup", "derived", "computed", "first_per_parent",
                       "record_field", "self_ref_field", "composite_fk_field",
                       "correlated", "conditional"):
                (comp if s == "computed" else dep).append(cn)
            else:
                ind.append(cn)
        pk_cols.extend(c for c in pk if c not in pk_cols and c not in fk_cols)
        return pk_cols + fk_cols + ind + dep + comp

    # ── PK position lookup (IDManager.lookup_values / reindex semantics) ──
    def _pk_positions(self, table: str, pk_col: str, fk: Col) -> tuple[np.ndarray, np.ndarray]:
        """Return (positions, missing_mask or None) of fk values within table[pk_col]."""
        key = (table, pk_col)
        if key not in self._pk_pos_cache:
            pk = np.asarray(self.tables[table][pk_col].v)
            n = len(pk)
            if n and pk.dtype.kind in "iu" and pk[0] + n - 1 == pk[-1] and np.all(np.diff(pk) == 1):
                self._pk_pos_cache[key] = ("range", int(pk[0]), n)
            else:
                order = np.argsort(pk, kind="stable")
                self._pk_pos_cache[key] = ("sorted", pk[order], order)
        info = self._pk_pos_cache[key]
        fv = np.asarray(fk.v)
        if info[0] == "range" and fk.mask is None and fv.dtype == np.int64 and len(fv):
            pos = fv - info[1]  # fast path: contiguous sequence PKs, no nulls
            if pos.min() >= 0 and pos.max() < info[2]:
                return pos, None
        miss = np.zeros(len(fv), dtype=bool) if fk.mask is None else fk.mask.copy()
        fvi = np.where(miss, 0, fv).astype(np.int64) if fv.dtype.kind in "iuf" else fv
        if info[0] == "range":
            pos = fvi - info[1]
            bad = (pos < 0) | (pos >= info[2])
            miss |= bad
            pos = np.where(miss, 0, pos)
        else:
            srt, order = info[1], info[2]
            p = np.searchsorted(srt, fvi)
            p = np.minimum(p, len(srt) - 1)
            miss |= srt[p] != fvi
            pos = np.where(miss, 0, order[p])
        return pos, miss

    def lookup(self, table: str, pk_col: str, col: str, fk: Col) -> Col:
        pos, miss = self._pk_positions(table, pk_col, fk)
        src = self.tables[table][col]
        if isinstance(src.v, (pa.Array, pa.ChunkedArray)):
            v = _take_str(src.v, pos)
        else:
            v = np.asarray(src.v)[pos]
        m = None if miss is None else miss.copy()
        if src.mask is not None:
            sm = src.mask[pos]
            m = sm if m is None else (m | sm)
        return Col(v, m if m is not None and m.any() else None)

    # ── generate ──────────────────────────────────────────────────────────
    def generate(self, scale: str) -> dict[str, pa.Table]:
        counts = self.row_counts(scale)
        order = self.level_order()
        for tn in order:
            t0 = time.perf_counter()
            tdef = self.schema["tables"][tn]
            child_rng = np.random.default_rng(
                self.seed ^ int.from_bytes(hashlib.sha256(tn.encode("utf-8")).digest()[:8], "little"))
            ctx = Ctx(child_rng, tn, counts.get(tn, 100), self)
            for cn in self.column_order(tdef):
                cdef = tdef["columns"][cn]
                g = cdef.get("generator", {})
                s = g.get("strategy", "")
                if not s:
                    ctx.cur[cn] = Col(np.full(ctx.n, None, dtype=object), np.ones(ctx.n, bool))
                    continue
                tc = time.perf_counter()
                col = getattr(self, "s_" + s)(cn, cdef, g, ctx)
                if cdef.get("nullable", False) and cdef.get("null_rate", 0.0) > 0:
                    # Strategy.apply_nulls
                    m = ctx.rng.random(ctx.n) < cdef["null_rate"]
                    col.mask = m if col.mask is None else (col.mask | m)
                ctx.cur[cn] = col
                self.col_timings[f"{tn}.{cn}"] = time.perf_counter() - tc
            self.tables[tn] = ctx.cur
            self.timings[tn] = time.perf_counter() - t0
        t0 = time.perf_counter()
        self.compute_phase()
        self.timings["_compute_phase"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        self.fix_business_rules()
        self.timings["_business_rules"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        out = {}
        for tn in order:
            cols = self.tables[tn]
            out[tn] = pa.table({k: c.to_arrow() for k, c in cols.items()})
        self.timings["_to_arrow"] = time.perf_counter() - t0
        return out

    # ── strategies ────────────────────────────────────────────────────────
    # sequence.SequenceStrategy
    def s_sequence(self, cn, cdef, g, ctx):
        start, step = g.get("start", 1), g.get("step", 1)
        return Col(np.arange(start, start + ctx.n * step, step, dtype=np.int64))

    def _max_len(self, cdef, arr: pa.Array) -> pa.Array:
        ml = cdef.get("max_length")
        return pc.utf8_slice_codeunits(arr, 0, ml) if ml else arr

    # enum.WeightedEnumStrategy
    def s_weighted_enum(self, cn, cdef, g, ctx):
        vd = g.get("values", {})
        labels = list(vd.keys())
        w = np.array(list(vd.values()), dtype=float)
        if w.sum() > 0:
            w = w / w.sum()
        idx = _categorical(ctx.rng, w, ctx.n)
        try:
            numeric = np.array([float(v) for v in labels], dtype=float)
            return Col(numeric[idx])
        except (TypeError, ValueError):
            return Col(_take_str(pa.array([str(x) for x in labels], pa.string()), idx))

    # lifecycle.LifecycleStrategy
    def s_lifecycle(self, cn, cdef, g, ctx):
        phases = g.get("phases", g.get("values", {}))
        labels = list(phases.keys())
        w = np.array(list(phases.values()), dtype=float)
        w /= w.sum()
        idx = _categorical(ctx.rng, w, ctx.n)
        return Col(_take_str(pa.array(labels, pa.string()), idx))

    # faker_strategy.FakerStrategy -> native.NativeStrategy
    def _apool(self, key: str, values: list[str], max_length: int | None = None) -> pa.Array:
        k = (key, max_length)
        if k not in self._arrow_pools:
            vals = [str(v)[:max_length] if max_length else str(v) for v in values]
            self._arrow_pools[k] = pa.array(vals, pa.string())
        return self._arrow_pools[k]

    def s_faker(self, cn, cdef, g, ctx):
        return self.s_native(cn, cdef, g, ctx)

    def s_native(self, cn, cdef, g, ctx):
        prov = g.get("provider", "word")
        P, rng, n, ml = self.pools, ctx.rng, ctx.n, cdef.get("max_length")
        if prov in ("first_name", "last_name"):
            src = P.first_names if prov == "first_name" else P.last_names
            idx = rng.integers(0, len(src), size=n)  # rng.choice(pool, size=n)
            # NativeStrategy truncates each value to max_length; truncating the pool
            # entries first is identical.
            pool = self._apool(prov, src, ml)
            return Col(_take_str(pool, idx), src_idx=idx, src_pool=prov)
        if prov == "email":
            cur = ctx.cur
            if "first_name" in cur and "last_name" in cur:
                parts = []
                for nm in ("first_name", "last_name"):
                    c = cur[nm]
                    if c.src_pool is not None:
                        src = P.first_names if c.src_pool == "first_name" else P.last_names
                        ml_src = self.schema["tables"][ctx.table_name]["columns"][nm].get("max_length")
                        # str(f).lower().replace(' ', '') computed once per pool entry
                        key = ("emailpart_" + c.src_pool, ml_src)
                        if key not in self._arrow_pools:
                            self._arrow_pools[key] = pa.array(
                                [(str(v)[:ml_src] if ml_src else str(v)).lower().replace(" ", "") for v in src],
                                pa.string())
                        parts.append(_take_str(self._arrow_pools[key], c.src_idx))
                    else:
                        parts.append(pc.replace_substring(pc.utf8_lower(c.v), " ", ""))
                firsts, lasts = parts
            else:
                firsts = _take_str(self._apool("emailpart_first", [s.lower().replace(" ", "") for s in P.first_names]),
                                   rng.integers(0, len(P.first_names), size=n))
                lasts = _take_str(self._apool("emailpart_last", [s.lower().replace(" ", "") for s in P.last_names]),
                                  rng.integers(0, len(P.last_names), size=n))
            doms = _take_str(self._apool("email_domains", P.email_domains),
                             rng.integers(0, len(P.email_domains), size=n))
            suffix = _int_to_str(rng.integers(1, 999, size=n))
            return Col(self._max_len(cdef, _join(firsts, ".", lasts, suffix, "@", doms)))
        if prov == "street_address":
            num = _int_to_str(rng.integers(100, 9999, size=n))
            st = _take_str(self._apool("street_names", P.street_names), rng.integers(0, len(P.street_names), size=n))
            sfx = _take_str(self._apool("street_suffixes", P.street_suffixes),
                            rng.integers(0, len(P.street_suffixes), size=n))
            return Col(self._max_len(cdef, pc.binary_join_element_wise(num, st, sfx, " ")))
        if prov == "city":
            return Col(_take_str(self._apool("us_cities", P.us_cities, ml),
                                 rng.integers(0, len(P.us_cities), size=n)))
        if prov == "state_abbr":
            return Col(_take_str(self._apool("us_states", P.us_states, ml),
                                 rng.integers(0, len(P.us_states), size=n)))
        raise NotImplementedError(f"native provider {prov!r} not needed by retail")

    # temporal.TemporalStrategy
    def _date_range(self, g):
        if g.get("range_ref") == "model.date_range":
            dr = self.model.get("date_range", {})
        else:
            dr = g.get("date_range", g.get("range", {}))
        s = dr.get("start") or g.get("start", "2022-01-01")
        e = dr.get("end") or g.get("end", "2025-12-31")
        return np.datetime64(s, "ns"), np.datetime64(e, "ns")

    def _uniform_ns(self, rng, s, e, n):
        return rng.integers(s.view(np.int64), e.view(np.int64), size=n)

    def s_temporal(self, cn, cdef, g, ctx):
        s, e = self._date_range(g)
        pattern = g.get("pattern", "uniform")
        if pattern != "seasonal":
            # pd.to_datetime(int ns) -> datetime64[ns]
            return Col(self._uniform_ns(ctx.rng, s, e, ctx.n).view("M8[ns]"))
        profiles = dict(g.get("profiles", {}))
        if not profiles.get("month") and g.get("month_weights"):
            profiles["month"] = g["month_weights"]
        if not profiles.get("day_of_week") and g.get("day_of_week_weights"):
            profiles["day_of_week"] = g["day_of_week_weights"]
        mw, dw, hp = profiles.get("month", {}), profiles.get("day_of_week", {}), profiles.get("hour_of_day", {})
        n, rng = ctx.n, ctx.rng
        if not mw and not dw:
            us = self._uniform_ns(rng, s, e, n) // 1000
        else:
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            dows = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            mp = np.array([mw.get(m, 1 / 12) for m in months]); mp /= mp.sum()
            dp = np.array([dw.get(d, 1 / 7) for d in dows]); dp /= dp.sum()
            comb = (mp[:, None] * dp[None, :]).ravel(); comb /= comb.sum()
            # multinomial bucket counts + shuffle  ==  iid categorical bucket per row
            bucket = _categorical(rng, comb, n)
            days = np.arange(s.astype("M8[D]").view(np.int64), e.astype("M8[D]").view(np.int64) + 1)
            dmon = days.view("M8[D]").astype("M8[M]").view(np.int64) % 12
            ddow = (days + 3) % 7  # 1970-01-01 was a Thursday; Mon=0
            bkey = dmon * 7 + ddow
            order = np.argsort(bkey, kind="stable")
            sorted_days = days[order]
            starts = np.searchsorted(bkey[order], np.arange(84), side="left")
            lens = np.searchsorted(bkey[order], np.arange(84), side="right") - starts
            bl = lens[bucket]
            empty = bl == 0
            pick = starts[bucket] + (rng.random(n) * np.maximum(bl, 1)).astype(np.int64)
            day = sorted_days[np.minimum(pick, len(sorted_days) - 1)]
            if hp:
                us = day * US_PER_DAY  # time-of-day replaced by the hour profile below
            else:
                us = day * US_PER_DAY + rng.integers(0, US_PER_DAY, size=n)
            if empty.any():  # (month, dow) combo absent from range -> uniform
                us[empty] = self._uniform_ns(rng, s, e, int(empty.sum())) // 1000
        if hp:
            if hp.get("distribution") == "bimodal":
                peaks = hp.get("peaks", [12, 18]); sd = hp.get("std_dev", 2)
                pk = rng.integers(0, len(peaks), size=n)
                hours = rng.normal(np.asarray(peaks, float)[pk], sd)
                hours = np.mod(np.floor(hours), 24).astype(np.int64)
            else:
                hours = rng.integers(0, 24, size=n)
            minutes = rng.integers(0, 60, size=n)
            seconds = rng.integers(0, 60, size=n)
            us = (us // US_PER_DAY) * US_PER_DAY + (hours * 3600 + minutes * 60 + seconds) * 1_000_000
        return Col(us.view("M8[us]"))

    # record_sample.RecordSampleStrategy / record_field.RecordFieldStrategy
    def s_record_sample(self, cn, cdef, g, ctx):
        ds, field = g["dataset"], g["field"]
        cols = self.ref.columns(ds)
        N = len(next(iter(cols.values())))
        if g.get("unique", False) and ctx.n <= N:
            idx = ctx.rng.permutation(N)[: ctx.n]
        else:
            idx = ctx.rng.integers(0, N, size=ctx.n)
        for f, arr in cols.items():
            v = _take_str(arr, idx) if isinstance(arr, pa.Array) else arr[idx]
            ctx.hidden[f"_rs_{ds}_{f}"] = Col(v)
        return Col(ctx.hidden[f"_rs_{ds}_{field}"].v)

    def s_record_field(self, cn, cdef, g, ctx):
        return Col(ctx.hidden[f"_rs_{g['dataset']}_{g['field']}"].v)

    # reference_data.ReferenceDataStrategy
    def s_reference_data(self, cn, cdef, g, ctx):
        data = self.ref.load(g["dataset"])
        n, rng = ctx.n, ctx.rng
        if all(isinstance(x, str) for x in data):
            return Col(_take_str(self._apool("ref_" + g["dataset"], data), rng.integers(0, len(data), size=n)))
        field = g.get("field")
        if field and field in data[0]:
            return Col(_take_str(pa.array([d.get(field) for d in data]), rng.integers(0, len(data), size=n)))
        names = [d.get("name", d.get("value", "")) for d in data]
        w = np.array([d.get("weight", 1.0) for d in data], dtype=float)
        w = w / w.sum()
        return Col(_take_str(pa.array(names, pa.string()), _categorical(rng, w, n)))

    # self_referencing.SelfReferencingStrategy / SelfRefFieldStrategy
    def s_self_referencing(self, cn, cdef, g, ctx):
        levels = int(g.get("levels", g.get("max_depth", 3)) or 3)
        root_count = int(g.get("root_count", max(1, ctx.n // 10)))
        pks = np.asarray(ctx.cur[g["pk_column"]].v)
        n = len(pks)
        root_count = max(1, min(root_count, n // levels))
        remaining = n - root_count
        rpl = remaining // (levels - 1) if levels > 1 else 0
        extra = remaining - rpl * (levels - 1) if levels > 1 else remaining
        starts = [0]
        cursor = root_count
        for lv in range(2, levels + 1):
            starts.append(cursor)
            cursor += rpl + (1 if lv - 2 < extra else 0)
        ends = starts[1:] + [n]
        lvl = np.zeros(n, dtype=np.int64)
        for lv, (a, b) in enumerate(zip(starts, ends), start=1):
            lvl[a:b] = lv
        parent = np.zeros(n, dtype=np.int64)
        mask = np.zeros(n, dtype=bool)
        mask[:root_count] = True
        for lv in range(2, levels + 1):
            a, b = starts[lv - 1], ends[lv - 1]
            if b - a <= 0:
                continue
            pp = pks[starts[lv - 2]:ends[lv - 2]]
            if len(pp) == 0:
                pp = pks[:root_count]
            parent[a:b] = pp[ctx.rng.integers(0, len(pp), size=b - a)]
        ctx.hidden[f"_sr_{ctx.table_name}_level"] = Col(lvl)
        return Col(parent, mask)

    def s_self_ref_field(self, cn, cdef, g, ctx):
        return Col(ctx.hidden[f"_sr_{ctx.table_name}_{g.get('field', 'level')}"].v)

    # first_per_parent.FirstPerParentStrategy
    def s_first_per_parent(self, cn, cdef, g, ctx):
        default = g.get("default", True)
        parent = np.asarray(ctx.cur[g["parent_column"]].v)
        _, first = np.unique(parent, return_index=True)
        out = np.full(ctx.n, not default, dtype=bool)
        out[first] = default
        return Col(out)

    # foreign_key.ForeignKeyStrategy + id_manager.IDManager
    def s_foreign_key(self, cn, cdef, g, ctx):
        ref_table, ref_col = g["ref"].split(".", 1)
        dist = g.get("distribution", "uniform")
        params = dict(g.get("params") or {})
        for k in ("alpha", "max_per_parent"):
            if k in g and k not in params:
                params[k] = g[k]
        cb = g.get("constrained_by")
        if cb and cb in ctx.cur:
            return self._constrained_fk(ref_table, cb, ctx.cur[cb], cdef.get("nullable", False))
        if g.get("sample_rate") is not None:
            raise NotImplementedError("sample_rate FKs not used by retail")
        if ref_table == ctx.table_name:
            pk = np.asarray(ctx.cur[ref_col].v)
            return Col(pk[ctx.rng.integers(0, len(pk), size=ctx.n)])
        pool = np.asarray(self.tables[ref_table][ref_col].v)
        return Col(pool[self._fk_indices(len(pool), ctx.n, dist, params)])

    def _fk_indices(self, pool_size: int, count: int, dist: str, params: dict) -> np.ndarray:
        rng = self.rng
        if dist == "zipf":
            # Spindle: rng.zipf(alpha) with rejection of draws > pool_size.  Rejection
            # of a Zipf(alpha) tail == Zipf truncated to 1..N, sampled here exactly by
            # inverse CDF (P(k) = k^-alpha / sum_{j<=N} j^-alpha).
            return _truncated_zipf(rng, params.get("alpha", 1.5), pool_size, count) - 1
        if dist == "pareto":
            alpha = params.get("alpha", 1.2)
            mpp = params.get("max_per_parent")
            raw = rng.pareto(alpha, size=count)
            cap = float(np.percentile(raw, 99.5))
            idx = (np.minimum(raw, cap) / (cap + 1e-9) * pool_size).astype(int)
            idx = np.clip(idx, 0, pool_size - 1)
            if mpp is not None:
                idx = self._enforce_max_per_parent(idx, pool_size, int(mpp))
            return idx
        return rng.integers(0, pool_size, size=count)

    def _enforce_max_per_parent(self, idx: np.ndarray, pool_size: int, mpp: int) -> np.ndarray:
        """IDManager._enforce_max_per_parent, vectorized.

        Spindle: for each over-limit parent, rng.choice(its positions, excess,
        replace=False), then reassign all chosen rows uniformly among parents with
        counts < mpp; up to 10 rounds.  Here the uniformly-random subset of size
        `excess` is taken as the rows ranked >= mpp under a random key within each
        parent group (same distribution, no per-parent loop)."""
        rng = self.rng
        idx = idx.copy()
        for _ in range(10):
            counts = np.bincount(idx, minlength=pool_size)
            over = counts > mpp
            if not over.any():
                break
            cand = np.flatnonzero(over[idx])
            # sort by (parent, random key) in one int64 argsort
            key = (idx[cand].astype(np.int64) << 32) | rng.integers(0, 1 << 32, size=len(cand), dtype=np.int64)
            o = np.argsort(key)
            grp = idx[cand][o]
            first = np.searchsorted(grp, grp, side="left")
            rank = np.arange(len(grp)) - first
            to_reassign = cand[o][rank >= mpp]
            under = np.flatnonzero(counts < mpp)
            if len(under) == 0:
                under = np.arange(pool_size)
            idx[to_reassign] = under[rng.integers(0, len(under), size=len(to_reassign))]
        return idx

    def _constrained_fk(self, ref_table: str, cb: str, cons: Col, nullable: bool) -> Col:
        """IDManager.get_constrained_fks: uniform pick among the parent rows whose
        `cb` equals this row's value; None (nullable) or a random PK otherwise."""
        rng = self.rng
        pt = self.tables[ref_table]
        tdef = self.schema["tables"][ref_table]
        pk = np.asarray(pt[tdef["primary_key"][0]].v)
        codes = np.asarray(pt[cb].v)
        key = ("constrained", ref_table, cb)
        cv = np.asarray(cons.v)
        if key not in self._pk_pos_cache:
            o = np.argsort(codes, kind="stable")
            cmin = int(codes.min()) if len(codes) else 0
            R = int(codes.max()) - cmin + 1 if len(codes) else 0
            if codes.dtype.kind in "iu" and R <= 4 * len(codes) + 1024:
                counts = np.bincount(codes - cmin, minlength=R)  # dense group table
                starts = np.cumsum(counts) - counts
                self._pk_pos_cache[key] = ("dense", o, cmin, counts, starts)
            else:
                self._pk_pos_cache[key] = ("sorted", o, codes[o])
        info = self._pk_pos_cache[key]
        o = info[1]
        if info[0] == "dense" and cv.dtype.kind in "iu":
            _, _, cmin, counts, starts = info
            rel = cv - cmin
            inr = (rel >= 0) & (rel < len(counts))
            if not inr.all():
                rel = np.where(inr, rel, 0)
            cnt = np.where(inr, counts[rel], 0)
            lo = starts[rel]
        else:
            sc = info[2]
            lo = np.searchsorted(sc, cv, side="left")
            cnt = np.searchsorted(sc, cv, side="right") - lo
        has = cnt > 0
        if cons.mask is not None:
            has &= ~cons.mask
        pick = lo + (rng.random(len(cv)) * np.maximum(cnt, 1)).astype(np.int64)
        pick = np.minimum(pick, len(o) - 1)
        out = pk[o[pick]]
        if nullable:
            return Col(out, ~has if (~has).any() else None)
        miss = ~has
        if miss.any():
            out[miss] = pk[rng.integers(0, len(pk), size=int(miss.sum()))]
        return Col(out)

    # distribution.DistributionStrategy
    def s_distribution(self, cn, cdef, g, ctx):
        dname = g.get("distribution", "uniform")
        p = g.get("params") or g
        rng, n = ctx.rng, ctx.n
        if dname == "uniform":
            v = rng.uniform(p.get("min", 0), p.get("max", 1), size=n)
        elif dname == "normal":
            sd = p.get("std_dev")
            if sd is None:
                sd = p.get("sigma", p.get("std", 1))
            v = rng.normal(p.get("mean", 0), sd, size=n)
        elif dname == "log_normal":
            sg = p.get("sigma")
            if sg is None:
                sg = p.get("std", 1)
            v = rng.lognormal(p.get("mean", 0), sg, size=n)
        elif dname == "pareto":
            v = (rng.pareto(p.get("alpha", 1.5), size=n) + 1) * p.get("min", 1)
        elif dname == "zipf":
            v = _truncated_zipf(rng, p.get("alpha", 1.5), int(p.get("max", 1000)), n).astype(float)
        elif dname == "geometric":
            v = rng.geometric(p.get("p", 0.5), size=n).astype(float)
        elif dname == "poisson":
            v = rng.poisson(p.get("lambda", 5), size=n).astype(float)
        elif dname == "bernoulli":
            v = rng.binomial(1, p.get("probability", 0.5), size=n).astype(float)
        else:
            raise ValueError(dname)
        if p.get("min") is not None:
            v = np.maximum(v, p["min"])
        if p.get("max") is not None:
            v = np.minimum(v, p["max"])
        if cdef.get("scale") is not None:
            v = np.round(v, cdef["scale"])
        return Col(v)

    # correlated.CorrelatedStrategy
    def s_correlated(self, cn, cdef, g, ctx):
        rule = g.get("rule", g.get("operation", "multiply"))
        p = g.get("params", {})
        src = ctx.cur[g["source_column"]].numeric()
        n = len(src)
        if rule == "multiply":
            r = src * ctx.rng.uniform(float(p.get("factor_min", p.get("min", 0.30))),
                                      float(p.get("factor_max", p.get("max", 0.70))), size=n)
        elif rule == "add":
            r = src + ctx.rng.uniform(float(p.get("offset_min", p.get("min", 0.0))),
                                      float(p.get("offset_max", p.get("max", 10.0))), size=n)
        elif rule == "subtract":
            r = np.maximum(0.0, src - ctx.rng.uniform(float(p.get("offset_min", p.get("min", 0.0))),
                                                      float(p.get("offset_max", p.get("max", 10.0))), size=n))
        else:
            raise ValueError(rule)
        sc = cdef.get("scale") if cdef.get("scale") is not None else 2
        return Col(np.round(r, sc))

    # pattern.PatternStrategy (seq / random tokens)
    _CHARS = np.frombuffer(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", dtype=np.uint8)

    def s_pattern(self, cn, cdef, g, ctx):
        import re
        fmt = g["format"]
        parts, last = [], 0
        for m in re.finditer(r"\{(\w+)(?::(\d+))?\}", fmt):
            if m.start() > last:
                parts.append(fmt[last:m.start()])
            tok, w = m.group(1), int(m.group(2)) if m.group(2) else 0
            if tok == "seq":
                s = _int_to_str(np.arange(1, ctx.n + 1))
                parts.append(pc.utf8_lpad(s, w, "0") if w else s)
            elif tok == "random":
                L = w or 4
                ch = self._CHARS[ctx.rng.integers(0, len(self._CHARS), size=(ctx.n, L))]
                parts.append(pa.array(np.ascontiguousarray(ch).view(f"S{L}").ravel()).cast(pa.string()))
            else:
                raise NotImplementedError("column-reference pattern tokens not used by retail")
            last = m.end()
        if last < len(fmt):
            parts.append(fmt[last:])
        if len(parts) == 1 and isinstance(parts[0], str):
            return Col(pa.array([parts[0]] * ctx.n, pa.string()))
        return Col(_join(*parts))

    # derived.DerivedStrategy
    def s_derived(self, cn, cdef, g, ctx):
        source, via = g.get("source", ""), g.get("via")
        rule = g.get("rule", g.get("operation", "copy"))
        params = dict(g.get("params", {}))
        if "days" in g:
            if rule == "copy":
                rule = "add_days"
            params.setdefault("distribution", "uniform")
            params.setdefault("min", g["days"])
            params.setdefault("max", g["days"])
        if "." in source and via:
            rt, rc = source.split(".", 1)
            src = self.lookup(rt, via, rc, ctx.cur[via])
        else:
            src = ctx.cur[source]
        if rule == "copy":
            return Col(src.v, src.mask)
        if rule != "add_days":
            raise ValueError(rule)
        dist = params.get("distribution", "uniform")
        lo, hi = float(params.get("min", 1)), float(params.get("max", 30))
        n = len(src.v)
        if dist == "log_normal":
            days = np.clip(ctx.rng.lognormal(float(params.get("mean", 2.0)), float(params.get("sigma", 0.8)), size=n), lo, hi)
        elif dist == "normal":
            days = np.clip(ctx.rng.normal(float(params.get("mean", 10.0)), float(params.get("std_dev", 3.0)), size=n), lo, hi)
        else:
            days = ctx.rng.uniform(lo, hi, size=n)
        days = np.round(days).astype(np.int64)
        v = np.asarray(src.v)
        unit = np.datetime_data(v.dtype)[0]
        out = (v.view(np.int64) + days * _UNIT_PER_DAY[unit]).view(v.dtype)
        return Col(out, src.mask)

    # lookup.LookupStrategy
    def s_lookup(self, cn, cdef, g, ctx):
        via = g["via"]
        return self.lookup(g["source_table"], via, g["source_column"], ctx.cur[via])

    # conditional.ConditionalStrategy
    def _cond_mask(self, cond: str, ctx) -> np.ndarray:
        cu = cond.strip().upper()
        def find(up):
            return next((k for k in ctx.cur if k.upper() == up), up.lower())
        if "IS NOT NULL" in cu:
            c = ctx.cur.get(find(cu.replace("IS NOT NULL", "").strip()))
            if c is None:
                return np.ones(ctx.n, bool)
            return np.ones(ctx.n, bool) if c.mask is None else ~c.mask
        if "IS NULL" in cu:
            c = ctx.cur.get(find(cu.replace("IS NULL", "").strip()))
            if c is None:
                return np.zeros(ctx.n, bool)
            return np.zeros(ctx.n, bool) if c.mask is None else c.mask.copy()
        for op in ("!=", "=="):
            if op in cond:
                a, b = cond.split(op, 1)
                a, b = a.strip(), b.strip().strip("'\"")
                if a not in ctx.cur:
                    return np.ones(ctx.n, bool)
                c = ctx.cur[a]
                try:
                    val = float(b)
                    arr = c.numeric()
                    return arr == val if op == "==" else arr != val
                except ValueError:
                    eq = pc.equal(c.v, b).to_numpy(zero_copy_only=False)
                    return eq if op == "==" else ~eq
        return np.ones(ctx.n, bool)

    def _cond_branch(self, gc: dict, ctx) -> np.ndarray:
        if "fixed" in gc:
            return np.full(ctx.n, float(gc["fixed"]))
        if gc.get("strategy") == "lookup":
            via = gc.get("via", "")
            if via not in ctx.cur:
                return np.zeros(ctx.n)
            looked = self.lookup(gc["source_table"], via, gc["source_column"], ctx.cur[via])
            vals = looked.numeric()
            fkm = ctx.cur[via].mask
            if fkm is not None:
                vals = np.where(fkm, 0.0, vals)
            return vals
        return np.zeros(ctx.n)

    def s_conditional(self, cn, cdef, g, ctx):
        mask = self._cond_mask(g.get("condition", ""), ctx)
        t = self._cond_branch(g.get("true_generator", {}), ctx)
        f = self._cond_branch(g.get("false_generator", {}), ctx)
        return Col(np.where(mask, t, f).astype(float))

    # formula.FormulaStrategy (same eval over whole-column numpy arrays)
    def s_formula(self, cn, cdef, g, ctx):
        ns = {k: (c.numeric() if not isinstance(c.v, (pa.Array, pa.ChunkedArray))
                  and np.asarray(c.v).dtype.kind in "iufb" else c.v) for k, c in ctx.cur.items()}
        safe = {"__builtins__": {"abs": abs, "min": min, "max": max, "round": round},
                "np_round": np.round, "np_clip": np.clip, "np_where": np.where,
                "np_maximum": np.maximum, "np_minimum": np.minimum, "np_abs": np.abs,
                "np_sqrt": np.sqrt, "np_log": np.log, "np_exp": np.exp,
                "np_floor": np.floor, "np_ceil": np.ceil, "np_nan": np.nan}
        r = eval(g["expression"], safe, ns)
        r = np.full(ctx.n, r) if np.isscalar(r) else np.asarray(r)
        if cdef.get("scale") is not None:
            r = np.round(r, cdef["scale"])
        return Col(r)

    # computed.ComputedStrategy (placeholder; back-filled in compute_phase)
    def s_computed(self, cn, cdef, g, ctx):
        return Col(np.full(ctx.n, np.nan))

    # ── generator.apply_compute_phase + ComputedStrategy.backfill ─────────
    def compute_phase(self):
        for tn, tdef in self.schema["tables"].items():
            if tn not in self.tables:
                continue
            for cn, cdef in tdef["columns"].items():
                g = cdef.get("generator", {})
                if g.get("strategy") != "computed":
                    continue
                rule = g.get("rule", "sum_children")
                ct, cc = g.get("child_table", ""), g.get("child_column", "")
                if ct not in self.tables or not tdef.get("primary_key"):
                    continue
                pk_col = tdef["primary_key"][0]
                child_fk = next((c for c, d in self.schema["tables"][ct]["columns"].items()
                                 if self._fk_ref_table(d) == tn), None)
                if not child_fk:
                    continue
                child = self.tables[ct]
                pos, miss = self._pk_positions(tn, pk_col, child[child_fk])
                vals = child[cc].numeric()
                ok = ~np.isnan(vals) if miss is None else (~miss & ~np.isnan(vals))
                npar = len(self.tables[tn][pk_col].v)
                if rule == "sum_children":
                    agg = np.bincount(pos[ok], weights=vals[ok], minlength=npar)
                elif rule == "count_children":
                    agg = np.bincount(pos[ok], minlength=npar).astype(float)
                elif rule == "avg_children":
                    s = np.bincount(pos[ok], weights=vals[ok], minlength=npar)
                    c = np.bincount(pos[ok], minlength=npar)
                    agg = np.where(c > 0, s / np.maximum(c, 1), 0.0)
                else:
                    raise NotImplementedError(rule)
                self.tables[tn][cn] = Col(np.round(agg, 2))

    # ── rules.business_rules.BusinessRulesEngine.fix_violations ───────────
    @staticmethod
    def _parse_cmp(rule: str):
        import re
        m = re.match(r"^(.+?)\s*(>=|<=|>|<|==)\s*(.+)$", rule.strip())
        return (m.group(1).strip(), m.group(2), m.group(3).strip()) if m else ("", "", "")

    def fix_business_rules(self):
        for rule in self.schema.get("business_rules", []):
            if rule["type"] == "cross_table":
                self._fix_cross_table(rule)
            elif rule["type"] == "cross_column":
                self._fix_cross_column(rule)

    def _fix_cross_table(self, rule):
        via = rule.get("via")
        l, op, r = self._parse_cmp(rule["rule"])
        if not via or "." not in l or "." not in r:
            return
        lt, lc = l.split(".", 1)
        rt, rc = r.split(".", 1)
        if lt not in self.tables or rt not in self.tables:
            return
        L, R = self.tables[lt], self.tables[rt]
        if via not in L or via not in R:
            return
        # right_df.set_index(via)[rc] mapped through left[via]
        rv = self.lookup(rt, via, rc, L[via])
        lcol = L[lc]
        lv, rvv = np.asarray(lcol.v), np.asarray(rv.v)
        valid = np.ones(len(lv), bool)
        if rv.mask is not None:
            valid &= ~rv.mask
        if lcol.mask is not None:
            valid &= ~lcol.mask
        temporal = rvv.dtype.kind == "M"
        if temporal:
            a, b = _dt_to_ns(lv), _dt_to_ns(rvv)
        else:
            a, b = lv.astype(float), rvv.astype(float)
        if op == ">=":
            mask = valid & (a < b)
        elif op == ">":
            mask = valid & (a <= b)
        elif op == "<=":
            mask = valid & (a > b)
        else:
            return
        if not mask.any():
            return
        if op in (">=", ">"):
            if temporal:
                # right + 1 day, .dt.as_unit("us"); the column ends up datetime64[us]
                new_us = (b[mask] + NS_PER_DAY) // 1000
                lunit = np.datetime_data(lv.dtype)[0]
                nv = lv.view(np.int64).copy()
                nv[mask] = new_us * (_UNIT_TO_NS[lunit] // 1000 if lunit == "ns" else 1)
                L[lc] = Col(nv.view(lv.dtype), lcol.mask)
            else:
                nv = lv.astype(float).copy()
                nv[mask] = b[mask] + 1
                L[lc] = Col(nv, lcol.mask)
        else:  # "<=": scale down to a fraction of the right value
            nv = lv.astype(float).copy()
            nv[mask] = np.round(b[mask] * self.rng.uniform(0.3, 1.0, size=int(mask.sum())), 2)
            L[lc] = Col(nv, lcol.mask)

    def _fix_cross_column(self, rule):
        tn = rule.get("table")
        if not tn or tn not in self.tables:
            return
        T = self.tables[tn]
        l, op, r = self._parse_cmp(rule["rule"])
        if l not in T or r not in T:
            return
        lv, rv = np.asarray(T[l].v), np.asarray(T[r].v)
        if lv.dtype.kind == "M":
            a, b = _dt_to_ns(lv), _dt_to_ns(rv)
            if op == "<":
                mask = a >= b; sign = -1
            elif op == ">":
                mask = a <= b; sign = 1
            else:
                return
            if mask.any():
                unit = np.datetime_data(lv.dtype)[0]
                off = self.rng.integers(1, 30, size=int(mask.sum())) * NS_PER_DAY
                nv = lv.view(np.int64).copy()
                nv[mask] = (b[mask] + sign * off) // _UNIT_TO_NS[unit]
                T[l] = Col(nv.view(lv.dtype), T[l].mask)
        else:
            a, b = T[l].numeric(), T[r].numeric()
            if op == "<":
                mask = a >= b; lo, hi = 0.3, 0.95
            elif op == ">":
                mask = a <= b; lo, hi = 1.05, 2.0
            else:
                return
            if mask.any():
                nv = a.copy()
                nv[mask] = np.round(b[mask] * self.rng.uniform(lo, hi, size=int(mask.sum())), 2)
                T[l] = Col(nv, T[l].mask)


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def generate(scale: str = "medium", seed: int = 42, spindle_root: str | Path = DEFAULT_SPINDLE_ROOT,
             return_engine: bool = False):
    """Generate the 9 retail tables as pyarrow Tables (loads Spindle config/pools/ref data)."""
    t0 = time.perf_counter()
    schema = load_schema(spindle_root)
    pools = Pools(spindle_root)
    ref = RefData(spindle_root)
    eng = Engine(schema, pools, ref, seed)
    eng.timings["_load_config_and_pools"] = time.perf_counter() - t0
    tables = eng.generate(scale)
    return (tables, eng) if return_engine else tables


def write_parquet(tables: dict[str, pa.Table], out_dir: str | Path, compression: str = "snappy") -> list[Path]:
    """Mirror of PandasWriter.to_parquet: one file per table, sequential, pandas'
    default (snappy) compression."""
    import pyarrow.parquet as pq
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, t in tables.items():
        p = out_dir / f"{name}.parquet"
        pq.write_table(t, p, compression=compression)
        paths.append(p)
    return paths


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", default="medium")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--spindle-root", default=DEFAULT_SPINDLE_ROOT)
    a = ap.parse_args()
    t = time.perf_counter()
    tabs, eng = generate(a.scale, a.seed, a.spindle_root, return_engine=True)
    el = time.perf_counter() - t
    tot = sum(x.num_rows for x in tabs.values())
    for k, v in tabs.items():
        print(f"{k:18s} {v.num_rows:>10,}  {eng.timings.get(k, 0):.3f}s  {v.schema.types}")
    print({k: round(v, 3) for k, v in eng.timings.items() if k.startswith("_")})
    print(f"total {tot:,} rows in {el:.3f}s = {tot / el:,.0f} rows/s")
