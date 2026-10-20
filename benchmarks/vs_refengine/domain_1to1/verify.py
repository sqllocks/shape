"""T-21 a-f against the release's fixed seed set, with unchanged statistical bounds."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import generate  # noqa: E402 (harness path bootstrap)
from paths import (  # noqa: E402 (harness path bootstrap)
    BASELINE_PY,
    BASELINE_VERSION,
    BENCH_OUT_DIR,
    SHAPE_PY,
)
from paths import HERE as BENCH  # noqa: E402 (harness path bootstrap)
from recorded_store import (  # noqa: E402 (harness path bootstrap)
    RecordingError,
    load,
    manifest,
    sha256,
)
from reference_names import for_domain  # noqa: E402

REF_SEED = 42
BASELINE_SEEDS = (43, 44, 45, 46)
IMPL_SEED = 1042
MODE = "3nf"
FANOUT_TOL_ABS = 0.002
validate_seed = generate.validate_seed


def load_run(impl: str, domain: str, scale: str, seed: int, tables: list[str]) -> Any:
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


def column_pool(
    pools: Pools,
    raw: dict[str, Any],
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
    declared = raw["tables"][table]["columns"][column]["generator"]
    return pools.pool_for(pools.effective(table, column, declared), table)


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


def as_num(s: pd.Series) -> np.ndarray[Any, Any]:
    s = s.dropna()
    if pd.api.types.is_datetime64_any_dtype(s.dtype):
        # compare in ns so ns/us columns are commensurable
        return cast(
            np.ndarray[Any, Any],
            s.astype("datetime64[ns]").to_numpy().astype("int64").astype(np.float64),
        )
    return cast(np.ndarray[Any, Any], pd.to_numeric(s).to_numpy(dtype=np.float64))


def ks(a: np.ndarray[Any, Any], b: np.ndarray[Any, Any]) -> float:
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


def fmt_num(x: Any, is_dt: Any) -> Any:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    if is_dt:
        return str(pd.Timestamp(int(x)))
    return float(x)


EMAIL_RE = re.compile(r"^([^.@]+)\.([^@]*?)(\d{1,3})@(.+)$")

STREET_RE = re.compile(r"^(\d+) (.+) (\S+)$")

PATTERN_TOKEN = re.compile(r"\{(\w+)(?::(\d+))?\}")


class Pools:
    """the baseline's own reference data, loaded lazily (read only)."""

    def __init__(self, domain: str, identifier_forms: list[dict[str, Any]]) -> None:
        self.identifier_forms = identifier_forms
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        r = subprocess.run(
            [
                str(BASELINE_PY),
                str(BENCH / "record_baseline.py"),
                "--worker",
                "pools",
                "--domain",
                domain,
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        if r.returncode:
            raise RecordingError("baseline reference pools are unavailable")
        data = json.loads(r.stdout)
        self.names = SimpleNamespace(**data["names"])
        self.native = SimpleNamespace(**{k: np.array(v) for k, v in data["native"].items()})
        self._datasets = {(None, k): v for k, v in data["datasets"].items()}
        self.generators = data.get("generators", {})
        self.children = [domain]
        self.composite = False
        self.domain_path = domain
        self.first_l = {v.lower().replace(" ", "") for v in self.names.FIRST_NAMES}
        self.last_l = {v.lower().replace(" ", "") for v in self.names.LAST_NAMES}

    generators: dict[str, dict[str, dict[str, Any]]] = {}

    def effective(self, table: str, column: str, declared: dict[str, Any]) -> dict[str, Any]:
        """The generator the release applies to a column: its domain overrides can replace the
        fixture's declared one (a nested reference field is generated as a constant)."""
        return self.generators.get(table, {}).get(column, declared)

    def domain_of(self, table: str | None) -> str | None:
        """The domain a table of a composite belongs to (the longest domain name it starts with)."""
        if not self.composite or table is None:
            return None
        owners = [c for c in self.children if table.startswith(c + "_")]
        return max(owners, key=len) if owners else None

    def dataset(self, name: str, table: str | None = None) -> Any:
        return self._datasets[(None, name)]

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

    def pool_for(self, gen: dict[str, Any], table: str | None = None) -> set[str] | None:
        """Values the strategy can draw from, or None when the baseline's output is the pool."""
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
                    # The release provider prefers an explicit existing field, then name/value.
                    field = gen.get("field")
                    if not field or field not in item:
                        field = next((f for f in ("name", "value") if f in item), None)
                    if field is None:
                        raise RecordingError(f"{gen['dataset']}: no baseline value field")
                    out.add(str(item[field]))
                else:
                    out.add(str(item))
            return out
        if st in ("record_sample", "record_field"):
            return {str(r[gen["field"]]) for r in self.dataset(gen["dataset"], table)}
        return None

    def component_fn(self, gen: dict[str, Any]) -> tuple[Callable[[pd.Series], float], str] | None:
        """Row-weighted component-level check for generators that compose their value."""
        st = gen.get("strategy")
        if st in ("faker", "native") and gen.get("provider") in (
            "email",
            "company_email",
            "phone_number",
            "uri",
        ):
            # Approved FRZ-01 correction: exact forms come from the recorded release provider.
            if not isinstance(self.identifier_forms, list) or any(
                not isinstance(f, dict) for f in self.identifier_forms
            ):
                raise RecordingError(f"{self.domain_path}: invalid identifier forms")
            forms = [f for f in self.identifier_forms if f.get("generator") == gen]
            if len(forms) != 1:
                raise RecordingError(
                    f"{self.domain_path}: identifier {gen.get('provider')} form missing"
                )
            form = forms[0]
            try:
                literals, slots, components = form["literals"], form["slots"], form["components"]
                if len(literals) != len(slots) + 1 or not slots:
                    raise ValueError("invalid template")
                pieces = []
                for i, (slot, width) in enumerate(slots):
                    values = {str(value).zfill(width) for value in components[slot]}
                    if not values:
                        raise ValueError("empty component")
                    choices = "|".join(
                        re.escape(v) for v in sorted(values, key=lambda v: (-len(v), v))
                    )
                    pieces.append(re.escape(literals[i]) + "(?:" + choices + ")")
                pattern = re.compile("".join(pieces) + re.escape(literals[-1]))
            except (KeyError, TypeError, ValueError, IndexError, re.error) as exc:
                raise RecordingError(f"{self.domain_path}: invalid identifier form") from exc

            def identifier(s: pd.Series) -> float:
                return float(s.dropna().astype(str).str.fullmatch(pattern).mean())

            return identifier, "exact recorded baseline identifier components and template"
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


def baseline_distances(sp: pd.Series, bl: pd.Series) -> dict[str, Any]:
    """Distance of another the baseline seed's column to the reference the baseline column."""
    k = kind(sp)
    out = {"null": abs(float(bl.isna().mean()) - float(sp.isna().mean()))}
    if k in ("numeric", "datetime"):
        out["ks"] = ks(as_num(sp), as_num(bl))
    else:
        fs, fb = freq(sp), freq(bl)
        out["tvd"] = tvd(fs, fb)
        out["dratio_dev"] = abs(len(fb) / max(len(fs), 1) - 1)
    return out


def merge_baselines(bs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        k: max(b[k] for b in bs if not math.isnan(b[k]))
        if any(not math.isnan(b[k]) for b in bs)
        else float("nan")
        for k in bs[0]
    }


def compare_column(
    sp: pd.Series,
    im: pd.Series,
    B: dict[str, Any],
    pool: set[str] | None,
    comp: tuple[Callable[[pd.Series], float], str] | None,
) -> dict[str, Any]:
    """B = max over baseline seeds of the baseline-vs-the baseline distances for this column."""
    k = kind(sp)
    r: dict[str, Any] = {"kind": k}
    n, m = len(sp), len(im)
    ns_, ni_ = float(sp.isna().mean()), float(im.isna().mean())
    p = max(ns_, 1.0 / max(n, 1))
    null_tol = max(5 * math.sqrt(p * (1 - p) * (1 / n + 1 / m)), 1.5 * B["null"], 1e-12)
    r["null_rate"] = {
        "baseline": ns_,
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
                "baseline": fmt_num(f(a), is_dt) if len(a) else None,
                "impl": fmt_num(f(b), is_dt) if len(b) else None,
            }
            for name, f in (("mean", np.mean), ("min", np.min), ("max", np.max))
        }
        r["stats"]["std"] = {
            "baseline": float(np.std(a)) / (1e9 * 86400 if is_dt else 1),
            "impl": float(np.std(b)) / (1e9 * 86400 if is_dt else 1),
            "unit": "days" if is_dt else "",
        }
        checks["ks"] = bool(d <= tol) if not math.isnan(d) else True
        r["distinct"] = {"baseline": int(sp.nunique()), "impl": int(im.nunique())}
    else:
        fs, fp = freq(sp), freq(im)
        ds, di = len(fs), len(fp)
        r["distinct"] = {
            "baseline": ds,
            "impl": di,
            "ratio": di / max(ds, 1),
            "baseline_max_ratio_dev": B["dratio_dev"],
        }
        top = list(dict.fromkeys(list(fs.index[:10]) + list(fp.index[:10])))[:15]
        r["top10"] = [
            {"value": v, "baseline": float(fs.get(v, 0.0)), "impl": float(fp.get(v, 0.0))}
            for v in top
        ]
        # Vocabulary overlap: fraction of impl rows occurring in baseline output or pool.
        vocab = set(fs.index) | (pool or set())
        pv = im.dropna().astype(str)
        r["vocab_overlap"] = float(pv.isin(vocab).mean()) if len(pv) else 1.0
        r["vocab_source"] = "baseline_output" + ("+pool" if pool else "")
        if comp:
            fn, rule = comp
            r["component_overlap"] = fn(im)
            r["component_overlap_baseline"] = fn(sp)
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


def _fk_values(column: pd.Series) -> pd.Series:
    """The non-null values of a key column: numbers for a numeric key, text for a text key
    (``capital_markets`` keys its tables by ticker)."""
    values = column.dropna()
    if pd.api.types.is_numeric_dtype(values) or pd.api.types.is_bool_dtype(values):
        return pd.to_numeric(values)
    return values


def fk_checks(T: dict[str, pd.DataFrame], fks: Any) -> dict[str, Any]:
    out = {}
    for c, cc, p, pc_ in fks:
        vals = _fk_values(T[c][cc])
        ok = vals.isin(set(T[p][pc_].tolist()))
        out[f"{c}.{cc}->{p}.{pc_}"] = {
            "integrity": float(ok.mean()) if len(ok) else 1.0,
            "n": int(len(vals)),
        }
    return out


def fanout(T: dict[str, pd.DataFrame], fks: Any) -> dict[str, Any]:
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


def _norm_val(v: Any) -> Any:
    return float(v) if isinstance(v, (int, float, np.integer, np.floating)) else str(v)


def record_coherence(
    T: dict[str, pd.DataFrame], raw: dict[str, Any], pools: Pools
) -> dict[str, float]:
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


def semantic_rates(T: dict[str, pd.DataFrame], raw: dict[str, Any]) -> dict[str, float]:
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


def load_schema_json(domain: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((BENCH / "fixtures" / "schemas" / (domain + "_3nf.json")).read_text()),
    )


def ensure_run(impl: str, domain: str, scale: str, seed: int, tables: list[str]) -> bool:
    validate_seed(impl, seed)
    dest = generate.out_dir(impl, domain, scale, seed)
    if impl == "baseline" and generate.is_complete(dest, tables):
        return True
    py = BASELINE_PY if impl == "baseline" else SHAPE_PY
    env = dict(os.environ)
    if impl == "baseline":
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
    r = subprocess.run(
        [
            str(py),
            str(HERE / "generate.py"),
            "--impl",
            "shape",
            "--domain",
            domain,
            "--scale",
            scale,
            "--seed",
            str(seed),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    return r.returncode == 0 and bool(generate.is_complete(dest, tables))


def ensure_cli_run(impl: str, domain: str, scale: str, seed: int, tables: list[str]) -> bool:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "domain_recorded_cli_commands", HERE / "bench_cli.py"
    )
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    dest = generate.out_dir(impl + "-cli", domain, scale, seed)
    if impl == "baseline" and generate.is_complete(dest, tables):
        return True
    import shutil

    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    env = dict(os.environ)
    if impl == "baseline":
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
    proc = subprocess.run(
        cli.command(impl, domain, scale, dest), env=env, capture_output=True, text=True
    )
    if proc.returncode:
        return False
    rows = {p.stem: pq.read_metadata(p).num_rows for p in sorted(dest.glob("*.parquet"))}
    (dest / generate.SUCCESS).write_text(json.dumps({"rows": rows, "via": "cli"}))
    return bool(generate.is_complete(dest, tables))


def run_digests(directory: Path) -> dict[str, Any]:
    from shape.repro import dataset_id

    tables = {p.stem: pq.read_table(p) for p in sorted(directory.glob("*.parquet"))}
    return {
        "tables": {
            name: {
                "rows": t.num_rows,
                "columns": [{"name": f.name, "type": str(f.type)} for f in t.schema],
                "dataset_id": dataset_id({name: t}),
                "parquet_sha256": sha256(directory / (name + ".parquet")),
                "parquet_bytes": (directory / (name + ".parquet")).stat().st_size,
            }
            for name, t in tables.items()
        },
        "dataset_id": dataset_id(tables),
    }


def authenticate_run(
    domain: str, scale: str, seed: int, record: dict[str, Any], impl: str = "baseline"
) -> None:
    actual = run_digests(generate.out_dir(impl, domain, scale, seed))
    expected = record["seeds"][str(seed)]
    if set(actual["tables"]) != set(expected["tables"]):
        raise RecordingError(f"generate:{domain}:{scale}: seed{seed}: table set differs")
    for table, data in actual["tables"].items():
        old = expected["tables"][table]
        if any(data[k] != old[k] for k in ("rows", "columns", "dataset_id")):
            raise RecordingError(
                f"generate:{domain}:{scale}: seed{seed} table {table}: dataset_id or shape differs"
            )
    if actual["dataset_id"] != expected["dataset_id"]:
        raise RecordingError(f"generate:{domain}:{scale}: seed{seed}: dataset_id differs")


def compare_runs(
    raw: dict[str, Any],
    ref: tuple[Any, ...],
    candidate: tuple[Any, ...],
    others: list[tuple[Any, ...]],
    pools: Any,
    aliases: Any = None,
) -> dict[str, Any]:
    if aliases is not None:
        ref = (aliases.normalize_frames(ref[0]), *ref[1:])
        candidate = (aliases.normalize_frames(candidate[0]), *candidate[1:])
        others = [(aliases.normalize_frames(o[0]), *o[1:]) for o in others]
        pools = aliases.wrap_pools(pools)
    SP, ts, order_sp = ref
    IM, ti, order_im = candidate
    fks = foreign_keys(raw)
    fp = fk_parent_map(fks)
    report: dict[str, Any] = {
        "clauses": list("abcdef"),
        "baseline": {"shape_version": BASELINE_VERSION},
        "tables": {},
        "flagged": [],
    }
    flagged = report["flagged"]
    if list(SP) != list(IM) or order_sp != order_im:
        flagged.append("(a) table names/order differ")
    for tn, frame in SP.items():
        if tn not in IM:
            continue
        current = IM[tn]
        columns = {}
        if len(frame) != len(current):
            flagged.append(f"(a) {tn}: row count differs")
        if list(frame) != list(current):
            flagged.append(f"(a) {tn}: column names/order differ")
        for col in frame:
            if col not in current:
                continue
            B = merge_baselines([baseline_distances(frame[col], o[0][tn][col]) for o in others])
            gen = raw["tables"][tn]["columns"][col]["generator"]
            result = compare_column(
                frame[col],
                current[col],
                B,
                column_pool(pools, raw, fp, tn, col),
                pools.component_fn(gen),
            )
            if ts[tn][col] != ti[tn].get(col):
                flagged.append(f"(a) {tn}.{col}: type differs")
            for key, ok in result["checks"].items():
                clause = {"null_rate": "b", "ks": "c"}.get(key, "d")
                if result["kind"] == "categorical" and len(freq(frame[col])) > 1000:
                    clause = "e"
                if not ok:
                    flagged.append(f"({clause}) {tn}.{col}: {key}")
            columns[col] = result
        report["tables"][tn] = {
            "columns": columns,
            "row_counts_identical": len(frame) == len(current),
            "columns_identical_and_ordered": list(frame) == list(current),
            "types_identical": ts[tn] == ti[tn],
        }
    integrity = fk_checks(IM, fks)
    for key, value in integrity.items():
        if value["integrity"] < 1.0:
            flagged.append("(f) FK integrity " + key)
    report["fk_integrity"] = integrity
    fan_sp = fanout(SP, fks)
    fan_im = fanout(IM, fks)
    fan_others = [fanout(o[0], fks) for o in others]
    report["fanout"] = {}
    for key in fan_sp:
        a = fan_sp[key]["_counts"]
        b = fan_im[key]["_counts"]
        db = max(ks(a.astype(float), f[key]["_counts"].astype(float)) for f in fan_others)
        distance = ks(a.astype(float), b.astype(float))
        tol = max(ks_crit(len(a), len(b)), 1.5 * db + FANOUT_TOL_ABS)
        report["fanout"][key] = {
            "ks_counts": distance,
            "ks_tol": tol,
            "equivalent": distance <= tol,
        }
        if distance > tol:
            flagged.append("(f) fan-out " + key)
    semantic_functions: dict[str, Callable[[dict[str, pd.DataFrame]], dict[str, float]]] = {
        "strategy_semantics": lambda frames: semantic_rates(frames, raw),
        "record_coherence": lambda frames: record_coherence(frames, raw, pools),
    }
    for label, fn in semantic_functions.items():
        current = fn(IM)
        baseline = [fn(o[0]) for o in others]
        report[label] = {}
        for key, value in current.items():
            floor = min(x[key] for x in baseline) - 0.005
            report[label][key] = {"impl": value, "floor": floor}
            if value < floor:
                flagged.append("(f) " + label + " " + key)
    return report


def fk_parent_map(fks: list[Any]) -> dict[tuple[str, str], tuple[str, str]]:
    return {(child, cc): (parent, pc) for child, cc, parent, pc in fks}


def negative_controls(
    raw: dict[str, Any],
    ref: tuple[Any, ...],
    candidate: tuple[Any, ...],
    others: list[tuple[Any, ...]],
    pools: Any,
    aliases: Any = None,
) -> dict[str, bool]:
    frames, types, _ = candidate
    numeric = [
        (t, c)
        for t, f in frames.items()
        for c in f
        if kind(f[c]) == "numeric" and f[c].notna().any()
    ]
    category = [
        (t, c)
        for t, f in frames.items()
        for c in f
        if kind(f[c]) == "categorical" and f[c].notna().any()
    ]
    controls = {}

    def check(name: str, mutate: Callable[[Any], Any], clause: str) -> bool:
        bad = copy.deepcopy(candidate)
        mutate(bad)
        try:
            result = compare_runs(raw, ref, bad, others, pools, aliases)
        except RecordingError as exc:
            if clause == "d" and "unknown reference NAME" in str(exc):
                return True
            raise
        return any("(" + clause + ")" in message for message in result["flagged"])

    def scaled(b: Any, t: str, c: str) -> None:
        b[0][t][c] = b[0][t][c] * 1.05

    def renamed(b: Any, t: str, c: str) -> None:
        col = b[0][t][c]
        b[0][t][c] = col.replace({col.dropna().value_counts().index[0]: "__changed_category__"})

    def nulls(b: Any, t: str, c: str) -> None:
        f = b[0][t]
        idx = f[c].dropna().index[: max(1, math.ceil(len(f) * 0.1))]
        f.loc[idx, c] = np.nan

    controls["numeric scaled by 1.05"] = any(
        check("numeric", partial(scaled, t=t, c=c), "c") for t, c in numeric
    )
    controls["category renamed"] = any(
        check("category", partial(renamed, t=t, c=c), "d") for t, c in category
    )
    controls["null rate raised by 10 points"] = any(
        check("null", partial(nulls, t=t, c=c), "b") for t, c in numeric
    )
    fks = foreign_keys(raw)
    child, cc, parent, pc = fks[0]
    controls["child rows dropped by 2%"] = check(
        "rows",
        lambda b: b[0].__setitem__(
            child, b[0][child].iloc[: max(0, len(b[0][child]) - math.ceil(len(b[0][child]) * 0.02))]
        ),
        "a",
    )

    def corrupt_fk(b: tuple[Any, ...]) -> None:
        f = b[0][child]
        idx = f.index[: max(1, math.ceil(len(f) * 0.01))]
        if pd.api.types.is_numeric_dtype(f[cc]):
            value = max(b[0][parent][pc].max(), f[cc].max()) + 1
        else:
            value = "__missing_parent__"
        f.loc[idx, cc] = value

    controls["foreign keys corrupted by 1%"] = check("fk", corrupt_fk, "f")
    table = next(t for t, f in frames.items() if len(f.columns) > 1)
    controls["columns reordered"] = check(
        "order",
        lambda b: b[0].__setitem__(
            table,
            b[0][table][[b[0][table].columns[1], b[0][table].columns[0], *b[0][table].columns[2:]]],
        ),
        "a",
    )
    t, c = next((t, c) for t, cols in types.items() for c, ty in cols.items() if ty == "int64")
    controls["int64 changed to float64"] = check(
        "type", lambda b: b[1][t].__setitem__(c, "double"), "a"
    )
    return controls


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--impl", choices=("shape",), default="shape")
    ap.add_argument("--domain", default="retail")
    ap.add_argument("--scale", default="small")
    ap.add_argument("--no-generate", action="store_true")
    ap.add_argument("--cli", action="store_true")
    ap.add_argument("--negative-control", action="store_true")
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    try:
        raw = load_schema_json(a.domain)
        tables = list(raw["tables"])
        record = load(
            "generate",
            a.domain + ":" + a.scale,
            inputs={
                "fixtures/schemas/" + a.domain + "_3nf.json": sha256(
                    BENCH / "fixtures" / "schemas" / (a.domain + "_3nf.json")
                )
            },
        )
        for seed in (REF_SEED, *BASELINE_SEEDS):
            if not a.no_generate and not ensure_run("baseline", a.domain, a.scale, seed, tables):
                raise RecordingError(
                    f"generate:{a.domain}:{a.scale}: baseline seed{seed} unavailable"
                )
            authenticate_run(a.domain, a.scale, seed, record)
        if not a.no_generate and not ensure_run("shape", a.domain, a.scale, IMPL_SEED, tables):
            raise RecordingError(f"generate:{a.domain}:{a.scale}: candidate unavailable")
        reference_impl = "baseline-cli" if a.cli else "baseline"
        candidate_impl = "shape-cli" if a.cli else "shape"
        if a.cli:
            if not a.no_generate:
                for impl, seed in (("baseline", 42), ("shape", 1042)):
                    if not ensure_cli_run(impl, a.domain, a.scale, seed, tables):
                        raise RecordingError(
                            "generate:" + a.domain + ":" + a.scale + ": CLI output unavailable"
                        )
            authenticate_run(a.domain, a.scale, 42, record, "baseline-cli")
        ref = load_run(reference_impl, a.domain, a.scale, 42, tables)
        others = [load_run("baseline", a.domain, a.scale, s, tables) for s in BASELINE_SEEDS]
        candidate = load_run(candidate_impl, a.domain, a.scale, 1042, tables)
        pools = Pools(a.domain, record.get("identifier_forms", []))
        aliases = for_domain(a.domain, pools)
        report = compare_runs(raw, ref, candidate, others, pools, aliases)
        report["baseline"]["commit"] = manifest()["shape_commit"]
        if a.negative_control:
            controls = negative_controls(raw, ref, candidate, others, pools, aliases)
            report["negative_control"] = controls
            for name, caught in controls.items():
                print(name + ": " + ("caught" if caught else "MISSED"))
            ok = not report["flagged"] and all(controls.values())
        else:
            ok = not report["flagged"]
        dest = (
            Path(a.report)
            if a.report
            else BENCH_OUT_DIR / "verify" / (a.domain + "_" + a.scale + ".json")
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(report, sort_keys=True, indent=2, default=str) + "\n")
        n = sum(len(t["columns"]) for t in report["tables"].values())
        passed = sum(
            c["equivalent"] for t in report["tables"].values() for c in t["columns"].values()
        )
        print(
            f"{passed}/{n} columns EQUIVALENT; clauses a-f; Shape {BASELINE_VERSION} seeds 42 to 46"
        )
        for flag in report["flagged"]:
            print(flag)
        return 0 if ok else 1
    except (RecordingError, OSError, KeyError) as exc:
        print(
            str(exc)
            if isinstance(exc, RecordingError)
            else f"generate:{a.domain}:{a.scale}: unavailable input",
            file=sys.stderr,
        )
        return 2


def foreign_keys(raw: dict[str, Any]) -> list[tuple[str, str, str, str]]:
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


if __name__ == "__main__":
    raise SystemExit(main())
