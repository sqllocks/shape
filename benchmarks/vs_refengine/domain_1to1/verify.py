"""Generation equivalence verifier (T-21): an implementation vs RefEngine, for any domain.

Runs in the *RefEngine* venv (needs pandas, scipy and RefEngine importable):

    source scripts/env.sh && "$REFENGINE_PY" benchmarks/vs_refengine/domain_1to1/verify.py \\
        --domain retail --scale small --impl reference_port|shape

* It reads Parquet only, from ``$BENCH_OUT_DIR/<impl>/<domain>/<scale>/seed<N>/``.
  Any missing run directory is generated first with ``generate.py`` (each impl in its own
  venv): ``refengine`` seeds 42, 43, 44, 45, 46 and ``<impl>`` seed 1042. The seed set is
  fixed (T-21) and has no option; a verdict from another set counts for nothing.
* Tables, FKs and business rules come from the ``dump_schema.py`` output
  (``$BENCH_OUT_DIR/schemas/<domain>_3nf.json``). No domain logic is hard-coded: pools and
  component rules are derived from each column's generator, and the cross-table semantic
  checks from the schema's strategies (``computed``, ``lookup``, ``derived``).
* It asserts every T-21 clause (a)-(h) and exits 1 unless all of them hold. It exits 2 when
  the seed-42 or seed-43..46 outputs (or the impl's) are missing or incomplete after
  generation, or the impl cannot generate the domain.

Every per-column statistic is also computed for RefEngine(42) vs RefEngine(43..46), i.e. how
much RefEngine differs from *itself* under a different seed. A column is flagged NOT
EQUIVALENT when its distance to RefEngine is clearly larger than both the sampling-noise
critical value and that self-baseline.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _refpkg  # noqa: E402
import allowlist  # noqa: E402
import composites  # noqa: E402
import dump_schema  # noqa: E402
import generate  # noqa: E402
from domain_differences import DELIBERATE  # noqa: E402
from paths import BENCH_OUT_DIR, REFENGINE_PY, REFENGINE_ROOT, SHAPE_PY  # noqa: E402

REF_SEED = 42
BASELINE_SEEDS = (43, 44, 45, 46)
IMPL_SEED = 1042
MODE = "3nf"
FANOUT_TOL_ABS = 0.002


# ─────────────────────────────────────────────────────────────────────────────
# run directories
# ─────────────────────────────────────────────────────────────────────────────


def ensure_run(impl: str, domain: str, scale: str, seed: int, tables: list[str]) -> bool:
    """Generate the run directory if it is missing or incomplete. True when it is complete."""
    d = generate.out_dir(impl, domain, scale, seed)
    if generate.is_complete(d, tables):
        return True
    py = REFENGINE_PY if impl == "refengine" else SHAPE_PY
    cmd = [
        str(py),
        str(HERE / "generate.py"),
        "--impl",
        impl,
        "--domain",
        domain,
        "--scale",
        scale,
        "--seed",
        str(seed),
    ]
    print(f"generating {impl} {domain}/{scale}/seed{seed}", file=sys.stderr, flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-2000:])
    return generate.is_complete(d, tables)


def load_run(impl: str, domain: str, scale: str, seed: int, tables: list[str]):
    """-> (frames, arrow types per table/column, generation order)"""
    d = generate.out_dir(impl, domain, scale, seed)
    frames, types, order = {}, {}, list(json.loads((d / generate.SUCCESS).read_text())["rows"])
    for t in tables:
        p = d / f"{t}.parquet"
        frames[t] = pd.read_parquet(p)
        sch = pq.read_schema(p)
        types[t] = {f.name: _norm_type(f.type) for f in sch}
    return frames, types, order


def _norm_type(ty: pa.DataType) -> str:
    if pa.types.is_large_string(ty):
        ty = pa.string()
    return str(ty)


# ─────────────────────────────────────────────────────────────────────────────
# schema (from dump_schema.py output)
# ─────────────────────────────────────────────────────────────────────────────


def load_schema_json(domain: str) -> dict:
    p = dump_schema.schema_path(domain, MODE)
    if not p.exists():
        dump_schema.dump(domain, MODE)
    return json.loads(p.read_text())


def rebuild_schema(raw: dict):
    """Rebuild RefEngine's ``RefEngineSchema`` from the dumped JSON (for BusinessRulesEngine)."""
    sys.path.insert(0, str(REFENGINE_ROOT))
    P = _refpkg.mod("schema.parser")

    tables = {
        tn: P.TableDef(
            name=t["name"],
            primary_key=t["primary_key"],
            description=t.get("description", ""),
            cdm_mapping=t.get("cdm_mapping"),
            columns={cn: P.ColumnDef(**c) for cn, c in t["columns"].items()},
        )
        for tn, t in raw["tables"].items()
    }
    return getattr(P, _refpkg.ENGINE_CLASS + "Schema")(
        model=P.ModelDef(**raw["model"]),
        tables=tables,
        relationships=[P.RelationshipDef(**r) for r in raw["relationships"]],
        business_rules=[P.BusinessRuleDef(**r) for r in raw["business_rules"]],
        generation=P.GenerationConfig(**raw["generation"]),
        correlated_columns=raw.get("correlated_columns", {}),
    )


def foreign_keys(raw: dict) -> list[tuple[str, str, str, str]]:
    """(child, child_col, parent, parent_col): schema relationships, self-referencing
    columns, and lookups whose source column is itself a foreign key."""
    fks: list[tuple[str, str, str, str]] = []
    for r in raw["relationships"]:
        for cc, pc_ in zip(r["child_columns"], r["parent_columns"], strict=False):
            fks.append((r["child"], cc, r["parent"], pc_))
    for tn, t in raw["tables"].items():
        for cn, c in t["columns"].items():
            g = c["generator"]
            if g.get("strategy") == "self_referencing":
                fks.append((tn, cn, tn, g["pk_column"]))
    known = {(c, cc): (p, pc_) for c, cc, p, pc_ in fks}
    for tn, t in raw["tables"].items():
        for cn, c in t["columns"].items():
            g = c["generator"]
            if g.get("strategy") == "lookup" and (g["source_table"], g["source_column"]) in known:
                p, pc_ = known[(g["source_table"], g["source_column"])]
                fks.append((tn, cn, p, pc_))
    return list(dict.fromkeys(fks))


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────


def column_pool(
    pools: Pools,
    raw: dict,
    fk_parent: dict[tuple[str, str], tuple[str, str]],
    table: str,
    column: str,
) -> set[str] | None:
    """The values a column can take: its own generator's pool, and for a foreign key the pool of
    the key it points at (a text key sampled from a reference dataset takes different values under
    another seed, but always from that dataset)."""
    seen: set[tuple[str, str]] = set()
    while (table, column) in fk_parent and (table, column) not in seen:
        seen.add((table, column))
        table, column = fk_parent[(table, column)]
    return pools.pool_for(raw["tables"][table]["columns"][column]["generator"], table)


def same_values(a: pd.Series, b: pd.Series) -> bool:
    if len(a) != len(b):
        return False
    na, nb = a.isna().to_numpy(), b.isna().to_numpy()
    if not (na == nb).all():
        return False
    av, bv = a[~na].to_numpy(), b[~nb].to_numpy()
    try:
        if pd.api.types.is_datetime64_any_dtype(a.dtype):
            x = a[~na].astype("datetime64[ns]").to_numpy()
            y = b[~nb].astype("datetime64[ns]").to_numpy()
            return bool((x == y).all())
        return bool((av.astype(np.float64) == bv.astype(np.float64)).all())
    except (TypeError, ValueError):
        return bool((av.astype(str) == bv.astype(str)).all())


def kind(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s.dtype):
        return "categorical"
    if pd.api.types.is_datetime64_any_dtype(s.dtype):
        return "datetime"
    if pd.api.types.is_numeric_dtype(s.dtype):
        return "numeric"
    nn = s.dropna()
    if len(nn) and pd.api.types.is_integer_dtype(pd.Series(nn.tolist()).dtype):
        return "numeric"
    return "categorical"


def as_num(s: pd.Series) -> np.ndarray:
    s = s.dropna()
    if pd.api.types.is_datetime64_any_dtype(s.dtype):
        # compare in ns so ns/us columns are commensurable
        return s.astype("datetime64[ns]").to_numpy().astype("int64").astype(np.float64)
    return pd.to_numeric(s).to_numpy(dtype=np.float64)


def ks(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) == 0 or len(b) == 0:
        return float("nan")
    return float(stats.ks_2samp(a, b, method="asymp").statistic)


def ks_crit(n: int, m: int, alpha: float = 0.001) -> float:
    c = math.sqrt(-0.5 * math.log(alpha / 2))
    return c * math.sqrt((n + m) / (n * m)) if n and m else float("nan")


def freq(s: pd.Series) -> pd.Series:
    return s.dropna().astype(str).value_counts(normalize=True)


def tvd(fa: pd.Series, fb: pd.Series) -> float:
    idx = fa.index.union(fb.index)
    return float(0.5 * np.abs(fa.reindex(idx, fill_value=0) - fb.reindex(idx, fill_value=0)).sum())


def fmt_num(x, is_dt):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    if is_dt:
        return str(pd.Timestamp(int(x)))
    return float(x)


# ─────────────────────────────────────────────────────────────────────────────
# pools and component rules, derived from each column's generator
# ─────────────────────────────────────────────────────────────────────────────

EMAIL_RE = re.compile(r"^([^.@]+)\.([^@]*?)(\d{1,3})@(.+)$")
STREET_RE = re.compile(r"^(\d+) (.+) (\S+)$")
PATTERN_TOKEN = re.compile(r"\{(\w+)(?::(\d+))?\}")


class Pools:
    """RefEngine's own reference data, loaded lazily (read only)."""

    def __init__(self, domain: str):
        sys.path.insert(0, str(REFENGINE_ROOT))
        names = _refpkg.mod("engine.data.names")
        native = _refpkg.mod("engine.strategies.native")
        _load_dataset = _refpkg.mod("engine.strategies.reference_data")._load_dataset

        self.names, self.native, self._load = names, native, _load_dataset
        root = REFENGINE_ROOT / _refpkg.PACKAGE / "domains"
        # A composite reads the reference data of each of its domains: the baseline finds a
        # dataset by its name alone, so a table's own domain is looked at first (``dataset``).
        self.children = (
            composites.children(composites.spec_of(domain))
            if composites.is_composite(domain)
            else [domain]
        )
        self.composite = composites.is_composite(domain)
        self.domain_path: Path | list[Path] = (
            [root / c for c in self.children] if self.composite else root / domain
        )
        self._root = root
        self._datasets: dict[tuple[str | None, str], Any] = {}
        self.first_l = {s.lower().replace(" ", "") for s in names.FIRST_NAMES}
        self.last_l = {s.lower().replace(" ", "") for s in names.LAST_NAMES}

    def domain_of(self, table: str | None) -> str | None:
        """The domain a table of a composite belongs to (the longest domain name it starts with)."""
        if not self.composite or table is None:
            return None
        owners = [c for c in self.children if table.startswith(c + "_")]
        return max(owners, key=len) if owners else None

    def dataset(self, name: str, table: str | None = None):
        domain = self.domain_of(table)
        key = (domain, name)
        if key not in self._datasets:
            own = self._root / domain / "reference_data" / f"{name}.json" if domain else None
            if (
                own is not None and own.exists()
            ):  # the table's own domain's file, not the cached one
                self._datasets[key] = json.loads(own.read_text(encoding="utf-8"))
            else:
                self._datasets[key] = self._load(name, self.domain_path)
        return self._datasets[key]

    def faker(self, provider: str) -> set[str] | None:
        n, nat = self.names, self.native
        table = {
            "first_name": lambda: n.FIRST_NAMES,
            "last_name": lambda: n.LAST_NAMES,
            "company": lambda: n.COMPANY_NAMES,
            "sentence": lambda: n.SENTENCES,
            "city": lambda: nat._US_CITIES,
            "state_abbr": lambda: nat._US_STATES,
        }
        fn = table.get(provider)
        return {str(x) for x in fn()} if fn else None

    def pool_for(self, gen: dict, table: str | None = None) -> set[str] | None:
        """Values the strategy can draw from, or None when RefEngine's output is the pool."""
        st = gen.get("strategy")
        if st == "faker":
            return self.faker(gen.get("provider", ""))
        if st in ("weighted_enum", "enum") and isinstance(gen.get("values"), (dict, list)):
            return {str(v) for v in gen["values"]}  # the declared value set
        if st == "reference_data":
            ds = self.dataset(gen["dataset"], table)
            out: set[str] = set()
            for item in ds:
                if isinstance(item, dict):
                    out.add(str(item.get(gen.get("field", "name"))))
                else:
                    out.add(str(item))
            return out
        if st in ("record_sample", "record_field"):
            return {str(r[gen["field"]]) for r in self.dataset(gen["dataset"], table)}
        return None

    def component_fn(self, gen: dict) -> tuple[Callable[[pd.Series], float], str] | None:
        """Row-weighted component-level check for generators that compose their value."""
        st = gen.get("strategy")
        if st == "faker" and gen.get("provider") == "email":

            def email(s: pd.Series) -> float:
                ex = s.dropna().astype(str).str.extract(EMAIL_RE)
                ok = (
                    ex[0].isin(self.first_l)
                    & ex[1].isin(self.last_l)
                    & ex[2].astype(float).between(1, 998)
                    & ex[3].isin(set(self.names.EMAIL_DOMAINS))
                )
                return float(ok.mean())

            return email, "first.lower+'.'+last.lower+suffix(1..998)+'@'+EMAIL_DOMAINS"
        if st == "faker" and gen.get("provider") == "company_email":
            stems = {
                str(c).lower().replace(" ", "").replace(",", "").replace(".", "")[:20]
                for c in self.names.COMPANY_NAMES
            }

            def company_email(s: pd.Series) -> float:
                ex = s.dropna().astype(str).str.extract(r"^([^.@]+)\.([^@]+)@([^@]+)\.com$")
                ok = ex[0].isin(self.first_l) & ex[1].isin(self.last_l) & ex[2].isin(stems)
                return float(ok.mean())

            return company_email, "first.last@company(lower, no space/comma/dot, 20)+'.com'"
        if st == "faker" and gen.get("provider") == "street_address":
            suffixes = set(self.native._STREET_SUFFIXES.tolist())

            def street(s: pd.Series) -> float:
                ex = s.dropna().astype(str).str.extract(STREET_RE)
                ok = (
                    ex[0].astype(float).between(100, 9998)
                    & ex[1].isin(set(self.names.STREET_NAMES))
                    & ex[2].isin(suffixes)
                )
                return float(ok.mean())

            return street, "number(100..9998)+' '+STREET_NAMES+' '+_STREET_SUFFIXES"
        if st == "faker" and gen.get("provider") == "name":
            firsts, lasts = set(self.names.FIRST_NAMES), set(self.names.LAST_NAMES)

            def full_name(s: pd.Series) -> float:
                def ok(v: str) -> bool:
                    words = v.split(" ")
                    return any(
                        " ".join(words[:i]) in firsts and " ".join(words[i:]) in lasts
                        for i in range(1, len(words))
                    )

                nn = s.dropna().astype(str)
                return float(np.mean([ok(v) for v in nn])) if len(nn) else 1.0

            return full_name, "FIRST_NAMES+' '+LAST_NAMES"
        if st == "faker" and gen.get("provider") == "phone_number":

            def phone(s: pd.Series) -> float:
                ex = s.dropna().astype(str).str.extract(r"^\((\d{3})\) (\d{3})-(\d{4})$")
                ok = (
                    ex[0].astype(float).between(200, 998)
                    & ex[1].astype(float).between(200, 998)
                    & ex[2].astype(float).between(1000, 9998)
                )
                return float(ok.mean())

            return phone, "(AAA) EEE-SSSS, AAA/EEE 200..998, SSSS 1000..9998"
        if st == "faker" and gen.get("provider") == "uri":
            domains, paths = set(self.names.URI_DOMAINS), set(self.names.URI_PATHS)

            def uri(s: pd.Series) -> float:
                ex = s.dropna().astype(str).str.extract(r"^https://([^/]+)/(.*)$")
                return float((ex[0].isin(domains) & ex[1].isin(paths)).mean())

            return uri, "'https://'+URI_DOMAINS+'/'+URI_PATHS"
        if st == "pattern":
            rx = pattern_regex(gen.get("format", ""))
            if rx is not None:
                return (lambda s: float(s.dropna().astype(str).str.fullmatch(rx).mean())), gen[
                    "format"
                ]
        return None


def pattern_regex(fmt: str) -> str | None:
    """Regex for a pattern-strategy format made only of literals, ``{seq:N}``, ``{random:N}``."""
    out, last = [], 0
    for m in PATTERN_TOKEN.finditer(fmt):
        if m.group(1) not in ("seq", "random"):
            return None
        out.append(re.escape(fmt[last : m.start()]))
        w = m.group(2)
        if m.group(1) == "seq":  # zero-padded to a minimum width: a longer number is not cut
            out.append(r"\d" + (f"{{{w},}}" if w else "+"))
        else:
            out.append("[A-Z0-9]" + (f"{{{w}}}" if w else "+"))
        last = m.end()
    out.append(re.escape(fmt[last:]))
    return "".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# column comparison
# ─────────────────────────────────────────────────────────────────────────────


def baseline_distances(sp: pd.Series, bl: pd.Series) -> dict:
    """Distance of another RefEngine seed's column to the reference RefEngine column."""
    k = kind(sp)
    out = {"null": abs(float(bl.isna().mean()) - float(sp.isna().mean()))}
    if k in ("numeric", "datetime"):
        out["ks"] = ks(as_num(sp), as_num(bl))
    else:
        fs, fb = freq(sp), freq(bl)
        out["tvd"] = tvd(fs, fb)
        out["dratio_dev"] = abs(len(fb) / max(len(fs), 1) - 1)
    return out


def merge_baselines(bs: list[dict]) -> dict:
    return {
        k: max(b[k] for b in bs if not math.isnan(b[k]))
        if any(not math.isnan(b[k]) for b in bs)
        else float("nan")
        for k in bs[0]
    }


def allowed_pool_column(
    sp: pd.Series, im: pd.Series, pools: Pools, source: tuple[str, str], gen: dict
) -> dict:
    """CMP-2: the baseline draws the column from the wrong domain's dataset, so its values are not
    the reference. Shape's values must all come from the right dataset (the domain's own file)."""
    domain, name = source
    path = REFENGINE_ROOT / _refpkg.PACKAGE / "domains" / domain / "reference_data" / f"{name}.json"
    field = gen.get("field", "name")
    rows = json.loads(path.read_text(encoding="utf-8"))
    pool = {str(r[field]) if isinstance(r, dict) else str(r) for r in rows}
    shape_in = float(im.dropna().astype(str).isin(pool).mean())
    base_in = float(sp.dropna().astype(str).isin(pool).mean())
    return {
        "kind": "allowed",
        "allowed": "CMP-2",
        "intended_pool": f"{domain}/{name}",
        "shape_values_in_intended_pool": shape_in,
        "baseline_values_in_intended_pool": base_in,
        "null_rate": {"refengine": float(sp.isna().mean()), "impl": float(im.isna().mean())},
        "checks": {"shape_values_in_intended_pool": shape_in >= 0.999},
        "equivalent": shape_in >= 0.999,
    }


def compare_column(
    sp: pd.Series,
    im: pd.Series,
    B: dict,
    pool: set[str] | None,
    comp: tuple[Callable[[pd.Series], float], str] | None,
) -> dict:
    """B = max over baseline seeds of RefEngine-vs-RefEngine distances for this column."""
    k = kind(sp)
    r: dict = {"kind": k}
    n, m = len(sp), len(im)
    ns_, ni_ = float(sp.isna().mean()), float(im.isna().mean())
    p = max(ns_, 1.0 / max(n, 1))
    null_tol = max(5 * math.sqrt(p * (1 - p) * (1 / n + 1 / m)), 1.5 * B["null"], 1e-12)
    r["null_rate"] = {
        "refengine": ns_,
        "impl": ni_,
        "baseline_max_abs_diff": B["null"],
        "tol": null_tol,
    }
    checks = {"null_rate": abs(ni_ - ns_) <= null_tol}

    if k in ("numeric", "datetime"):
        a, b = as_num(sp), as_num(im)
        is_dt = k == "datetime"
        d, db, crit = ks(a, b), B["ks"], ks_crit(len(a), len(b))
        tol = max(crit, 1.5 * db + 0.002) if not math.isnan(db) else crit
        r["ks"], r["ks_baseline"], r["ks_tol"] = d, db, tol
        r["stats"] = {
            name: {
                "refengine": fmt_num(f(a), is_dt) if len(a) else None,
                "impl": fmt_num(f(b), is_dt) if len(b) else None,
            }
            for name, f in (("mean", np.mean), ("min", np.min), ("max", np.max))
        }
        r["stats"]["std"] = {
            "refengine": float(np.std(a)) / (1e9 * 86400 if is_dt else 1),
            "impl": float(np.std(b)) / (1e9 * 86400 if is_dt else 1),
            "unit": "days" if is_dt else "",
        }
        checks["ks"] = bool(d <= tol) if not math.isnan(d) else True
        r["distinct"] = {"refengine": int(sp.nunique()), "impl": int(im.nunique())}
    else:
        fs, fp = freq(sp), freq(im)
        ds, di = len(fs), len(fp)
        r["distinct"] = {
            "refengine": ds,
            "impl": di,
            "ratio": di / max(ds, 1),
            "baseline_max_ratio_dev": B["dratio_dev"],
        }
        top = list(dict.fromkeys(list(fs.index[:10]) + list(fp.index[:10])))[:15]
        r["top10"] = [
            {"value": v, "refengine": float(fs.get(v, 0.0)), "impl": float(fp.get(v, 0.0))}
            for v in top
        ]
        # vocabulary overlap: fraction of impl rows whose value occurs in RefEngine's output or pool
        vocab = set(fs.index) | (pool or set())
        pv = im.dropna().astype(str)
        r["vocab_overlap"] = float(pv.isin(vocab).mean()) if len(pv) else 1.0
        r["vocab_source"] = "refengine_output" + ("+pool" if pool else "")
        if comp:
            fn, rule = comp
            r["component_overlap"] = fn(im)
            r["component_overlap_refengine"] = fn(sp)
            r["component_rule"] = rule
        high_card = ds > 1000 or comp is not None
        if high_card:
            vo = r.get("component_overlap", r["vocab_overlap"])
            checks["vocab"] = vo >= 0.999
            dr_tol = max(0.02, 1.5 * B["dratio_dev"])
            r["distinct_ratio_tol"] = dr_tol
            checks["distinct_ratio"] = abs(di / max(ds, 1) - 1) <= dr_tol
        else:
            d, db = tvd(fs, fp), B["tvd"]
            # multinomial noise: E[TVD] ~ sqrt(k/(2*pi*n)) per sample
            noise = math.sqrt(max(ds, 2) / (2 * math.pi)) * (
                1 / math.sqrt(max(n, 1)) + 1 / math.sqrt(max(m, 1))
            )
            tol = max(3 * noise, 1.5 * db + 0.002)
            r["tvd"], r["tvd_baseline"], r["tvd_tol"] = d, db, tol
            checks["tvd"] = d <= tol
            checks["vocab"] = r["vocab_overlap"] >= 0.999
    r["checks"] = {k2: bool(v) for k2, v in checks.items()}
    r["equivalent"] = all(checks.values())
    return r


# ─────────────────────────────────────────────────────────────────────────────
# FK / fan-out / record coherence / strategy semantics
# ─────────────────────────────────────────────────────────────────────────────


def _fk_values(column: pd.Series) -> pd.Series:
    """The non-null values of a key column: numbers for a numeric key, text for a text key
    (``capital_markets`` keys its tables by ticker)."""
    values = column.dropna()
    if pd.api.types.is_numeric_dtype(values) or pd.api.types.is_bool_dtype(values):
        return pd.to_numeric(values)
    return values


def fk_checks(T: dict[str, pd.DataFrame], fks) -> dict:
    out = {}
    for c, cc, p, pc_ in fks:
        vals = _fk_values(T[c][cc])
        ok = vals.isin(set(T[p][pc_].tolist()))
        out[f"{c}.{cc}->{p}.{pc_}"] = {
            "integrity": float(ok.mean()) if len(ok) else 1.0,
            "n": int(len(vals)),
        }
    return out


def fanout(T: dict[str, pd.DataFrame], fks) -> dict:
    out = {}
    for c, cc, p, pc_ in fks:
        if c == p:
            continue
        vals = _fk_values(T[c][cc])
        if pd.api.types.is_numeric_dtype(vals):
            vals = vals.astype("int64")
        cnt = vals.value_counts().reindex(T[p][pc_].to_numpy(), fill_value=0).to_numpy()
        q = np.quantile(cnt, [0.5, 0.9, 0.99])
        out[f"{p}->{c}.{cc}"] = {
            "p50": float(q[0]),
            "p90": float(q[1]),
            "p99": float(q[2]),
            "max": int(cnt.max()),
            "mean": float(cnt.mean()),
            "zero_frac": float((cnt == 0).mean()),
            "_counts": cnt,
        }
    return out


def _norm_val(v):
    return float(v) if isinstance(v, (int, float, np.integer, np.floating)) else str(v)


def record_coherence(T: dict[str, pd.DataFrame], raw: dict, pools: Pools) -> dict[str, float]:
    """Columns filled from one record of a reference dataset (record_sample / record_field)
    must form a real record: the fraction of rows whose tuple exists in the dataset."""
    out: dict[str, float] = {}
    for tn, t in raw["tables"].items():
        groups: dict[str, dict[str, str]] = {}
        for cn, c in t["columns"].items():
            g = c["generator"]
            if g.get("strategy") in ("record_sample", "record_field"):
                groups.setdefault(g["dataset"], {})[cn] = g["field"]
        for ds, colmap in groups.items():
            cols = list(colmap)
            ref = {tuple(_norm_val(r[colmap[cn]]) for cn in cols) for r in pools.dataset(ds, tn)}
            df = T[tn][cols]
            keys = list(
                zip(*[[_norm_val(v) for v in df[cn].tolist()] for cn in cols], strict=False)
            )
            out[f"{tn}.{'+'.join(cols)} in {ds}"] = float(np.mean([k in ref for k in keys]))
    return out


def semantic_rates(T: dict[str, pd.DataFrame], raw: dict) -> dict[str, float]:
    """Cross-table semantics implied by the schema's strategies: ``computed`` sums,
    ``lookup`` values and ``derived`` date offsets. Each value is the fraction of rows that
    satisfy the strategy's contract."""
    out: dict[str, float] = {}
    rels = raw["relationships"]
    for tn, t in raw["tables"].items():
        df = T[tn]
        pk = t["primary_key"][0] if t["primary_key"] else None
        for cn, c in t["columns"].items():
            g = c["generator"]
            st = g.get("strategy")
            if st == "computed" and g.get("rule") == "sum_children" and pk:
                child, ccol = g["child_table"], g["child_column"]
                rel = next((r for r in rels if r["parent"] == tn and r["child"] == child), None)
                if rel is None:
                    continue
                sums = T[child].groupby(rel["child_columns"][0])[ccol].sum()
                exp = sums.reindex(df[pk], fill_value=0.0).round(2).to_numpy()
                out[f"{tn}.{cn} == sum({child}.{ccol})"] = float(
                    (np.abs(df[cn].to_numpy(dtype=float) - exp) <= 0.0051).mean()
                )
            elif st == "lookup":
                s_tab, s_col, via = g["source_table"], g["source_column"], g["via"]
                s_pk = raw["tables"][s_tab]["primary_key"][0]
                src = T[s_tab].set_index(s_pk)[s_col]
                got = src.reindex(pd.to_numeric(df[via]).to_numpy()).to_numpy()
                mine = df[cn].to_numpy()
                both_nan = pd.isna(got) & pd.isna(mine)
                eq = np.zeros(len(df), dtype=bool)
                ok = ~(pd.isna(got) | pd.isna(mine))
                if pd.api.types.is_numeric_dtype(src) and pd.api.types.is_numeric_dtype(df[cn]):
                    eq[ok] = got[ok].astype(float) == mine[ok].astype(float)
                else:  # a text lookup (pulse ``trip.payment_type``): exact equality
                    eq[ok] = got[ok].astype(str) == mine[ok].astype(str)
                out[f"{tn}.{cn} == lookup {s_tab}.{s_col} via {via}"] = float(
                    (eq | both_nan).mean()
                )
            elif st == "derived" and g.get("rule") == "add_days":
                lo, hi = g["params"].get("min"), g["params"].get("max")
                if lo is None or hi is None:
                    continue
                if "." in g["source"]:
                    s_tab, s_col = g["source"].split(".", 1)
                    s_pk = raw["tables"][s_tab]["primary_key"][0]
                    base = (
                        pd.to_datetime(T[s_tab].set_index(s_pk)[s_col])
                        .reindex(pd.to_numeric(df[g["via"]]).to_numpy())
                        .to_numpy()
                    )
                else:
                    base = pd.to_datetime(df[g["source"]]).to_numpy()
                days = (pd.to_datetime(df[cn]).to_numpy() - base) / np.timedelta64(1, "D")
                out[f"{tn}.{cn} - {g['source']} in [{lo},{hi}] days"] = float(
                    ((days >= lo - 1e-6) & (days <= hi + 1e-6)).mean()
                )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────


def _fail_missing(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", default="retail")
    ap.add_argument("--scale", default="small")
    ap.add_argument("--impl", choices=["reference_port", "shape"], required=True)
    ap.add_argument(
        "--out",
        default=None,
        help="report JSON (default: $BENCH_OUT_DIR/verify/<impl>_<domain>_<scale>.json)",
    )
    ap.add_argument(
        "--cli",
        action="store_true",
        help="compare the CLI runs bench_cli.py wrote (refengine-cli seed 42 against <impl>-cli); "
        "the baseline seeds stay the API runs",
    )
    ap.add_argument(
        "--no-generate",
        action="store_true",
        help="do not generate missing run directories (exit 2 instead)",
    )
    a = ap.parse_args(argv)
    domain, scale, impl = a.domain, a.scale, a.impl
    ref = "refengine-cli" if a.cli else "refengine"
    if a.cli:
        impl = f"{impl}-cli"
    out = Path(a.out) if a.out else BENCH_OUT_DIR / "verify" / f"{impl}_{domain}_{scale}.json"
    t0 = time.time()

    raw = load_schema_json(domain)
    tables = list(raw["tables"])
    fks = foreign_keys(raw)
    fk_parent = {(c, cc): (p, pc_) for c, cc, p, pc_ in fks if (c, cc) != (p, pc_)}

    runs = [(ref, REF_SEED)] + [("refengine", s) for s in BASELINE_SEEDS] + [(impl, IMPL_SEED)]
    for who, seed in runs:
        d = generate.out_dir(who, domain, scale, seed)
        if who.endswith("-cli") and not generate.is_complete(d, tables):
            return _fail_missing(f"{d} is missing: run bench_cli.py first ({who}, seed {seed})")
        if not generate.is_complete(d, tables) and not a.no_generate:
            ensure_run(who, domain, scale, seed, tables)
        if not generate.is_complete(d, tables):
            return _fail_missing(f"{d} is missing or incomplete (impl {who}, seed {seed})")

    SP, sp_types, sp_order = load_run(ref, domain, scale, REF_SEED, tables)
    IM, im_types, im_order = load_run(impl, domain, scale, IMPL_SEED, tables)
    pools = Pools(domain)

    # The named, narrow differences of composites (allowlist.py): columns Shape adds, foreign keys
    # it moves to those columns, and columns drawn from the right domain's dataset.
    extras = allowlist.extra_columns(domain)
    redirected = allowlist.redirected(domain)
    pool_cols = allowlist.pool_columns(domain)
    IM_full = IM
    allowed_report: dict = {}
    if extras:
        IM = {t: (df.drop(columns=[extras[t]]) if t in extras else df) for t, df in IM_full.items()}
        im_types = {
            t: {c: v for c, v in ty.items() if c != extras.get(t)} for t, ty in im_types.items()
        }
        allowed_report["CMP-1"] = {"extra_columns": extras, "redirected_keys": redirected}
        for t, c in extras.items():  # the bridge column is a foreign key: the engine places it
            if c not in IM_full[t].columns:
                allowed_report.setdefault("problems", []).append(
                    f"(a) {t}: the bridge column {c} is missing"
                )
    fks_cmp = [fk for fk in fks if redirected.get(fk[0]) != fk[1]]
    fks_bridge = [
        (c, extras[c], p, pc_) for c, cc, p, pc_ in fks if c in redirected and redirected[c] == cc
    ]
    schema = rebuild_schema(raw)
    BusinessRulesEngine = _refpkg.mod("engine.rules.business_rules").BusinessRulesEngine
    FidelityComparator = _refpkg.mod("inference.comparator").FidelityComparator

    fc = FidelityComparator()
    per_seed: dict = {}
    fc_base = []
    sem_sp = [semantic_rates(SP, raw)]
    coh_sp = [record_coherence(SP, raw, pools)]
    fan_sp = fanout(SP, fks_cmp)
    for bs in BASELINE_SEEDS:  # one baseline in memory at a time
        BL, _, _ = load_run("refengine", domain, scale, bs, tables)
        for tn in tables:
            for c in SP[tn].columns:
                if c in BL[tn].columns:
                    per_seed.setdefault((tn, c), []).append(
                        baseline_distances(SP[tn][c], BL[tn][c])
                    )
        fc_base.append(fc.compare(SP, BL))
        fan_bl = fanout(BL, fks_cmp)
        for k, v in fan_sp.items():
            per_seed.setdefault(("fanout", k), []).append(
                ks(v["_counts"].astype(float), fan_bl[k]["_counts"].astype(float))
            )
        sem_sp.append(semantic_rates(BL, raw))
        coh_sp.append(record_coherence(BL, raw, pools))
        del BL
    B = {k: (merge_baselines(v) if k[0] != "fanout" else max(v)) for k, v in per_seed.items()}
    print(f"loaded and baselined in {time.time() - t0:.1f}s", file=sys.stderr)

    report: dict = {
        "domain": domain,
        "scale": scale,
        "impl": impl,
        "seed": REF_SEED,
        "impl_seed": IMPL_SEED,
        "baseline_seeds": list(BASELINE_SEEDS),
        "tables": {},
    }
    flagged: list[str] = list(allowed_report.get("problems", []))
    n_cols = n_pass = 0

    # (a) structure: tables, column names and order, Arrow types, row counts
    report["table_order"] = {"refengine": sp_order, "impl": im_order}
    if extras:
        # CMP-1: a bridge column is a new dependency edge, so the order may differ; it must still
        # hold the same tables and put every parent before its children.
        position = {t: i for i, t in enumerate(im_order)}
        struct_ok = set(sp_order) == set(im_order) and all(
            position[p] < position[c] for c, _, p, _ in fks_cmp + fks_bridge if c != p
        )
        allowed_report["CMP-1"]["table_order"] = {"refengine": sp_order, "impl": im_order}
    else:
        struct_ok = set(sp_order) == set(im_order) and sp_order == im_order
    if not struct_ok:
        flagged.append(f"(a) table order differs: refengine={sp_order} impl={im_order}")
    for tn in tables:
        sp, im = SP[tn], IM[tn]
        ts, ti = sp_types[tn], im_types[tn]
        tr = {
            "rows": {"refengine": len(sp), "impl": len(im)},
            "columns_refengine": list(sp.columns),
            "columns_impl": list(im.columns),
            "columns_identical_and_ordered": list(sp.columns) == list(im.columns),
            "arrow_types": {c: {"refengine": ts.get(c), "impl": ti.get(c)} for c in sp.columns},
            "columns": {},
        }
        tr["types_identical"] = all(ts.get(c) == ti.get(c) for c in sp.columns)
        tr["row_counts_identical"] = len(sp) == len(im)
        if not tr["columns_identical_and_ordered"]:
            flagged.append(f"(a) {tn}: column names/order differ")
        if not tr["row_counts_identical"]:
            flagged.append(f"(a) {tn}: rows {len(sp)} vs {len(im)}")
        for c in sp.columns:
            if c not in im.columns:
                flagged.append(f"(b) {tn}.{c}: missing in impl")
                n_cols += 1
                continue
            gen = raw["tables"][tn]["columns"][c]["generator"]
            if (tn, c) in pool_cols:
                cr = allowed_pool_column(sp[c], im[c], pools, pool_cols[(tn, c)], gen)
                allowed_report.setdefault("CMP-2", {})[f"{tn}.{c}"] = cr
            else:
                cr = compare_column(
                    sp[c],
                    im[c],
                    B[(tn, c)],
                    column_pool(pools, raw, fk_parent, tn, c),
                    pools.component_fn(gen),
                )
            cr["arrow_type_match"] = ts.get(c) == ti.get(c)
            allowed = DELIBERATE.get((domain, tn, c)) if impl == "shape" else None
            if allowed is not None:
                failing = tuple(k for k, v in cr["checks"].items() if not v)
                if not failing:
                    flagged.append(f"(b-e) {tn}.{c}: stale entry in domain_differences.py")
                elif failing == allowed.fails and allowed.accepts(im[c]):
                    cr["equivalent"] = True
                    cr["deliberate"] = allowed.reason
            cr["equivalent"] = cr["equivalent"] and cr["arrow_type_match"]
            tr["columns"][c] = cr
            n_cols += 1
            n_pass += cr["equivalent"]
            if not cr["equivalent"]:
                flagged.append(
                    f"(b-e) {tn}.{c}: failed {[k for k, v in cr['checks'].items() if not v]}"
                    + ("" if cr["arrow_type_match"] else " type")
                )
        report["tables"][tn] = tr

    # (f) FK integrity + fan-out
    report["fk_integrity"] = {
        "refengine": fk_checks(SP, fks),
        "impl": fk_checks(IM_full, fks_cmp + fks_bridge),
    }
    for k, v in report["fk_integrity"]["impl"].items():
        if v["integrity"] < 1.0:
            flagged.append(f"(f) FK integrity {k}: {v['integrity']:.6f}")
    fi = fanout(IM, fks_cmp)
    fo = {}
    for k in fan_sp:
        cs, ci = fan_sp[k].pop("_counts"), fi[k].pop("_counts")
        d = ks(cs.astype(float), ci.astype(float))
        tol = max(ks_crit(len(cs), len(ci)), 1.5 * B[("fanout", k)] + FANOUT_TOL_ABS)
        fo[k] = {
            "refengine": fan_sp[k],
            "impl": fi[k],
            "ks_counts": d,
            "ks_baseline_max": B[("fanout", k)],
            "ks_tol": tol,
            "equivalent": bool(d <= tol),
        }
        if not d <= tol:
            flagged.append(f"(f) fan-out {k}: KS {d:.4f} > tol {tol:.4f}")
    report["fanout"] = fo

    # record coherence and strategy semantics: impl must not be worse than RefEngine's own worst
    # seed
    sem_im, coh_im = semantic_rates(IM, raw), record_coherence(IM, raw, pools)
    for label, im_r, sp_rs in (
        ("record_coherence", coh_im, coh_sp),
        ("strategy_semantics", sem_im, sem_sp),
    ):
        report[label] = {}
        for k, v in im_r.items():
            floor = min(r[k] for r in sp_rs) - 0.005
            report[label][k] = {"impl": v, "refengine": [r[k] for r in sp_rs], "floor": floor}
            if v < floor:
                flagged.append(
                    f"({'e' if label == 'record_coherence' else 'f'}) {label}: {k} "
                    f"impl {v:.4f} < floor {floor:.4f}"
                )

    # (g) RefEngine's BusinessRulesEngine on the impl output
    viol = BusinessRulesEngine().validate(IM, schema)
    report["business_rules"] = {
        "refengine_BusinessRulesEngine_violations": {v.rule_name: v.violation_count for v in viol},
        "refengine_on_refengine": {
            v.rule_name: v.violation_count for v in BusinessRulesEngine().validate(SP, schema)
        },
    }
    for v in viol:
        flagged.append(f"(g) business rule {v.rule_name}: {v.violation_count} violations")

    # (h) RefEngine's FidelityComparator, asserted per table:
    #     impl >= min over seeds 43-46 of (RefEngine vs RefEngine) - 0.5
    rep = fc.compare(SP, IM)
    fidelity_tables = {}
    for t in rep.tables:
        base = [b.tables[t].score for b in fc_base]
        floor = min(base) - 0.5
        fidelity_tables[t] = {
            "impl": rep.tables[t].score,
            "baseline": base,
            "floor": floor,
            "ok": bool(rep.tables[t].score >= floor),
        }
        if rep.tables[t].score < floor:
            flagged.append(f"(h) fidelity {t}: {rep.tables[t].score:.2f} < floor {floor:.2f}")
    report["refengine_fidelity_comparator"] = {
        "overall_impl": rep.overall_score,
        "overall_baseline_refengine_vs_refengine": [b.overall_score for b in fc_base],
        "tables": fidelity_tables,
    }
    if allowed_report:
        report["allowed_differences"] = {k: v for k, v in allowed_report.items() if k != "problems"}
    report["summary"] = {
        "columns_total": n_cols,
        "columns_equivalent": n_pass,
        "flagged": flagged,
        "passed": not flagged,
        "seconds": time.time() - t0,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str))
    txt = summary_text(report)
    out.with_suffix(".txt").write_text(txt)
    print(txt)
    return 0 if not flagged else 1


def summary_text(R) -> str:
    L = [
        f"RefEngine {R['domain']} (seed {R['seed']}) vs {R['impl']} (seed {R['impl_seed']}) "
        f"- scale={R['scale']}  (baseline = max distance of RefEngine seeds {R['baseline_seeds']} "
        f"to RefEngine seed {R['seed']})",
        f"table order identical: {R['table_order']['refengine'] == R['table_order']['impl']}",
        "",
    ]
    L.append(
        f"{'table.column':34s} {'kind':11s} {'type':5s} {'null s/i':>15s} {'dist':>10s} "
        f"{'baseline':>9s} {'tol':>7s} {'vocab':>6s}  verdict"
    )
    for tn, tr in R["tables"].items():
        L.append(
            f"-- {tn}: rows {tr['rows']['refengine']:,} / {tr['rows']['impl']:,}  "
            f"cols identical+ordered={tr['columns_identical_and_ordered']}  "
            f"types identical={tr['types_identical']}"
        )
        for c, cr in tr["columns"].items():
            if cr["kind"] == "allowed":
                L.append(
                    f"{tn + '.' + c:34s} ALLOWED {cr['allowed']}: Shape values in "
                    f"{cr['intended_pool']} {cr['shape_values_in_intended_pool']:.4f}, baseline's "
                    f"{cr['baseline_values_in_intended_pool']:.4f}  "
                    f"{'EQUIVALENT' if cr['equivalent'] else 'NOT EQUIVALENT'}"
                )
                continue
            if "ks" in cr:
                d, b, t = cr["ks"], cr["ks_baseline"], cr["ks_tol"]
                tag = "KS"
            elif "tvd" in cr:
                d, b, t = cr["tvd"], cr["tvd_baseline"], cr["tvd_tol"]
                tag = "TVD"
            else:
                d = abs(cr["distinct"]["ratio"] - 1)
                b, t = cr["distinct"]["baseline_max_ratio_dev"], cr["distinct_ratio_tol"]
                tag = "|dR-1|"
            vo = cr.get("component_overlap", cr.get("vocab_overlap"))
            L.append(
                f"{tn + '.' + c:34s} {cr['kind']:11s} "
                f"{'ok' if cr['arrow_type_match'] else 'DIFF':5s} "
                f"{cr['null_rate']['refengine']:.4f}/{cr['null_rate']['impl']:.4f} "
                f"{tag:>3s} {d:7.4f} {b:9.4f} {t:7.4f} {'' if vo is None else f'{vo:6.4f}':>6s}  "
                f"{'EQUIVALENT' if cr['equivalent'] else 'NOT EQUIVALENT'}"
                f"{' (deliberate, domain_differences.py)' if cr.get('deliberate') else ''}"
            )
    L += ["", "FK integrity (refengine / impl):"]
    for k in R["fk_integrity"]["refengine"]:
        impl = R["fk_integrity"]["impl"].get(k)  # absent: a key CMP-1 moves to a bridge column
        L.append(
            f"  {k:55s} {R['fk_integrity']['refengine'][k]['integrity']:.4f} / "
            f"{'moved (CMP-1)' if impl is None else format(impl['integrity'], '.4f')}"
        )
    for k in R["fk_integrity"]["impl"]:
        if k not in R["fk_integrity"]["refengine"]:
            L.append(f"  {k:55s} (bridge, CMP-1) / {R['fk_integrity']['impl'][k]['integrity']:.4f}")
    L.append(
        "FK fan-out (children per parent)  "
        "refengine p50/p90/p99/max/zero% | impl | KS (baseline max, tol)"
    )
    for k, v in R["fanout"].items():
        s, p = v["refengine"], v["impl"]
        L.append(
            f"  {k:40s} {s['p50']:.0f}/{s['p90']:.0f}/{s['p99']:.0f}/{s['max']}/"
            f"{s['zero_frac']:.3f} | "
            f"{p['p50']:.0f}/{p['p90']:.0f}/{p['p99']:.0f}/{p['max']}/{p['zero_frac']:.3f} | "
            f"{v['ks_counts']:.4f} ({v['ks_baseline_max']:.4f}, {v['ks_tol']:.4f})"
        )
    for label in ("record_coherence", "strategy_semantics"):
        L.append(f"{label} (impl / min over RefEngine seeds, floor):")
        for k, v in R[label].items():
            L.append(
                f"  {k:60s} {v['impl']:.4f} / {min(v['refengine']):.4f}  floor {v['floor']:.4f}"
            )
    L.append(
        "RefEngine BusinessRulesEngine violations on impl output: "
        f"{json.dumps(R['business_rules']['refengine_BusinessRulesEngine_violations'])}"
    )
    fc = R["refengine_fidelity_comparator"]
    L.append(
        f"RefEngine FidelityComparator (real=RefEngine seed {R['seed']}, synthetic=impl): "
        f"overall impl={fc['overall_impl']:.2f}  "
        "RefEngine-vs-RefEngine baselines="
        f"{[round(x, 2) for x in fc['overall_baseline_refengine_vs_refengine']]}"
    )
    for t, v in fc["tables"].items():
        L.append(
            f"  {t:18s} impl {v['impl']:6.2f}  floor {v['floor']:6.2f}  "
            f"baselines {[round(x, 2) for x in v['baseline']]}  {'ok' if v['ok'] else 'FAIL'}"
        )
    s = R["summary"]
    L += ["", f"COLUMNS EQUIVALENT: {s['columns_equivalent']}/{s['columns_total']}"]
    for f in s["flagged"]:
        L.append(f"  NOT EQUIVALENT: {f}")
    L.append(f"VERDICT: {'PASS' if s['passed'] else 'FAIL'}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
