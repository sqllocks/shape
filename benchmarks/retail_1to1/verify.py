"""Equivalence verifier: Spindle retail vs the vectorized port.

Run with the Spindle venv (needs pandas + scipy + Spindle importable):

    /tmp/claude-0/spindle-venv/bin/python verify.py --scale medium --seed 42

Produces verify_report.json and verify_summary.txt next to this file.

Every per-column statistic is also computed for Spindle(seed) vs Spindle(seed+1)
("baseline"), i.e. how much Spindle differs from *itself* under a different seed.
A port column is flagged NOT EQUIVALENT when its distance to Spindle is clearly
larger than both the sampling-noise critical value and that self-baseline.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import port  # noqa: E402


def load_spindle(root: str):
    sys.path.insert(0, root)
    from sqllocks_spindle import Spindle
    from sqllocks_spindle.domains.retail import RetailDomain
    return Spindle, RetailDomain


def spindle_generate(root, scale, seed):
    Spindle, RetailDomain = load_spindle(root)
    return Spindle().generate(domain=RetailDomain(), scale=scale, seed=seed)


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def arrow_type_of(df: pd.DataFrame) -> dict[str, str]:
    """Arrow type each column gets when written to Parquet (what df.to_parquet does)."""
    t = pa.Table.from_pandas(df, preserve_index=False)
    out = {}
    for f in t.schema:
        ty = f.type
        if pa.types.is_large_string(ty):
            ty = pa.string()
        out[f.name] = str(ty)
    return out


def same_values(a: pd.Series, b: pd.Series) -> bool:
    if len(a) != len(b):
        return False
    na, nb = a.isna().to_numpy(), b.isna().to_numpy()
    if not (na == nb).all():
        return False
    av, bv = a[~na].to_numpy(), b[~nb].to_numpy()
    try:
        if pd.api.types.is_datetime64_any_dtype(a.dtype):
            return bool((a[~na].astype("datetime64[ns]").to_numpy() == b[~nb].astype("datetime64[ns]").to_numpy()).all())
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
# vocabularies (pools the port must draw from)
# ─────────────────────────────────────────────────────────────────────────────

def vocabularies(root):
    load_spindle(root)
    from sqllocks_spindle.engine.data import names
    from sqllocks_spindle.engine.strategies import native
    from sqllocks_spindle.engine.strategies.reference_data import _load_dataset
    dp = Path(root) / "sqllocks_spindle" / "domains" / "retail"
    zips = _load_dataset("us_zip_locations", dp)
    cats = _load_dataset("categories", dp)
    return {
        "first_names": set(names.FIRST_NAMES),
        "last_names": set(names.LAST_NAMES),
        "first_l": {s.lower().replace(" ", "") for s in names.FIRST_NAMES},
        "last_l": {s.lower().replace(" ", "") for s in names.LAST_NAMES},
        "email_domains": set(names.EMAIL_DOMAINS),
        "street_names": set(names.STREET_NAMES),
        "street_suffixes": set(native._STREET_SUFFIXES.tolist()),
        "us_cities": set(native._US_CITIES),
        "us_states": set(native._US_STATES),
        "zip_records": zips,
        "zip_tuples": {(z["city"], z["state"], z["zip"]) for z in zips},
        "zip_full": {z["zip"]: (z["city"], z["state"], z["lat"], z["lng"]) for z in zips},
        "zip_cities": {z["city"] for z in zips},
        "zip_states": {z["state"] for z in zips},
        "zip_zips": {z["zip"] for z in zips},
        "categories": {c["name"] for c in cats},
        "product_names": set(_load_dataset("product_names", dp)),
        "promo_names": set(_load_dataset("promo_names", dp)),
    }


COLUMN_POOL = {
    ("customer", "first_name"): "first_names",
    ("customer", "last_name"): "last_names",
    ("address", "city"): "zip_cities",
    ("address", "state"): "zip_states",
    ("address", "zip_code"): "zip_zips",
    ("store", "city"): "us_cities",
    ("store", "state"): "us_states",
    ("product_category", "category_name"): "categories",
    ("product", "product_name"): "product_names",
    ("promotion", "promo_name"): "promo_names",
}

EMAIL_RE = re.compile(r"^([^.@]+)\.([^@]*?)(\d{1,3})@(.+)$")
STREET_RE = re.compile(r"^(\d+) (.+) (\S+)$")


def component_overlap(table, col, s: pd.Series, V) -> dict | None:
    """Row-weighted fraction of values whose *components* all come from Spindle's pools."""
    vals = s.dropna().astype(str)
    if (table, col) == ("customer", "email"):
        ex = vals.str.extract(EMAIL_RE)
        ok = (ex[0].isin(V["first_l"]) & ex[1].isin(V["last_l"])
              & ex[2].astype(float).between(1, 998) & ex[3].isin(V["email_domains"]))
        return {"component_overlap": float(ok.mean()),
                "rule": "first.lower+'.'+last.lower+suffix(1..998)+'@'+EMAIL_DOMAINS"}
    if (table, col) == ("address", "street"):
        ex = vals.str.extract(STREET_RE)
        ok = (ex[0].astype(float).between(100, 9998) & ex[1].isin(V["street_names"])
              & ex[2].isin(V["street_suffixes"]))
        return {"component_overlap": float(ok.mean()),
                "rule": "number(100..9998)+' '+STREET_NAMES+' '+_STREET_SUFFIXES"}
    if (table, col) == ("store", "store_name"):
        ok = vals.str.fullmatch(r"Store #\d{4}")
        return {"component_overlap": float(ok.mean()), "rule": "Store #{seq:4}"}
    return None


# ─────────────────────────────────────────────────────────────────────────────
# column comparison
# ─────────────────────────────────────────────────────────────────────────────

def baseline_distances(sp: pd.Series, bl: pd.Series) -> dict:
    """Distance of another Spindle seed's column to the reference Spindle column."""
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
    return {k: max(b[k] for b in bs if not math.isnan(b[k])) if any(not math.isnan(b[k]) for b in bs) else float("nan")
            for k in bs[0]}


def compare_column(table, col, sp: pd.Series, po: pd.Series, B: dict, V) -> dict:
    """B = max over baseline seeds of Spindle-vs-Spindle distances for this column."""
    k = kind(sp)
    r: dict = {"kind": k}
    n, m = len(sp), len(po)
    # null rates
    ns_, np_ = float(sp.isna().mean()), float(po.isna().mean())
    p = max(ns_, 1.0 / max(n, 1))
    null_tol = max(5 * math.sqrt(p * (1 - p) * (1 / n + 1 / m)), 1.5 * B["null"], 1e-12)
    r["null_rate"] = {"spindle": ns_, "port": np_, "baseline_max_abs_diff": B["null"], "tol": null_tol}
    ok_null = abs(np_ - ns_) <= null_tol
    checks = {"null_rate": ok_null}

    if k in ("numeric", "datetime"):
        a, b = as_num(sp), as_num(po)
        is_dt = k == "datetime"
        d = ks(a, b)
        db = B["ks"]
        crit = ks_crit(len(a), len(b))
        tol = max(crit, 1.5 * db + 0.002) if not math.isnan(db) else crit
        r["ks"] = d
        r["ks_baseline"] = db
        r["ks_tol"] = tol
        r["stats"] = {
            name: {"spindle": fmt_num(f(a), is_dt) if len(a) else None,
                   "port": fmt_num(f(b), is_dt) if len(b) else None}
            for name, f in (("mean", np.mean), ("min", np.min), ("max", np.max))
        }
        r["stats"]["std"] = {"spindle": float(np.std(a)) / (1e9 * 86400 if is_dt else 1),
                             "port": float(np.std(b)) / (1e9 * 86400 if is_dt else 1),
                             "unit": "days" if is_dt else ""}
        checks["ks"] = bool(d <= tol) if not math.isnan(d) else True
        r["distinct"] = {"spindle": int(sp.nunique()), "port": int(po.nunique())}
    else:
        fs, fp = freq(sp), freq(po)
        ds, dp_ = len(fs), len(fp)
        r["distinct"] = {"spindle": ds, "port": dp_,
                         "ratio": dp_ / max(ds, 1), "baseline_max_ratio_dev": B["dratio_dev"]}
        top = list(dict.fromkeys(list(fs.index[:10]) + list(fp.index[:10])))[:15]
        r["top10"] = [{"value": v, "spindle": float(fs.get(v, 0.0)), "port": float(fp.get(v, 0.0))} for v in top]
        # vocabulary overlap: fraction of port rows whose value occurs in Spindle output or pool
        vocab = set(fs.index)
        pool_key = COLUMN_POOL.get((table, col))
        if pool_key:
            vocab |= {str(x) for x in V[pool_key]}
        pv = po.dropna().astype(str)
        r["vocab_overlap"] = float(pv.isin(vocab).mean()) if len(pv) else 1.0
        r["vocab_source"] = "spindle_output" + (f"+pool:{pool_key}" if pool_key else "")
        comp = component_overlap(table, col, po, V)
        comp_sp = component_overlap(table, col, sp, V)
        if comp:
            r["component_overlap"] = comp["component_overlap"]
            r["component_overlap_spindle"] = comp_sp["component_overlap"]
            r["component_rule"] = comp["rule"]
        high_card = ds > 1000 or comp is not None
        if high_card:
            vo = r.get("component_overlap", r["vocab_overlap"])
            checks["vocab"] = vo >= 0.999
            dr_tol = max(0.02, 1.5 * B["dratio_dev"])
            r["distinct_ratio_tol"] = dr_tol
            checks["distinct_ratio"] = abs(dp_ / max(ds, 1) - 1) <= dr_tol
        else:
            d, db = tvd(fs, fp), B["tvd"]
            # multinomial noise: E[TVD] ~ sqrt(k/(2*pi*n)) per sample
            noise = math.sqrt(max(ds, 2) / (2 * math.pi)) * (1 / math.sqrt(max(n, 1)) + 1 / math.sqrt(max(m, 1)))
            tol = max(3 * noise, 1.5 * db + 0.002)
            r["tvd"], r["tvd_baseline"], r["tvd_tol"] = d, db, tol
            checks["tvd"] = d <= tol
            checks["vocab"] = r["vocab_overlap"] >= 0.999
    r["checks"] = {k2: bool(v) for k2, v in checks.items()}
    r["equivalent"] = all(checks.values())
    return r


# ─────────────────────────────────────────────────────────────────────────────
# FK / fan-out / coherence / rules
# ─────────────────────────────────────────────────────────────────────────────

FKS = [  # (child, child_col, parent, parent_col)
    ("address", "customer_id", "customer", "customer_id"),
    ("product", "category_id", "product_category", "category_id"),
    ("product_category", "parent_category_id", "product_category", "category_id"),
    ("order", "customer_id", "customer", "customer_id"),
    ("order", "store_id", "store", "store_id"),
    ("order", "shipping_address_id", "address", "address_id"),
    ("order", "promotion_id", "promotion", "promotion_id"),
    ("order_line", "order_id", "order", "order_id"),
    ("order_line", "product_id", "product", "product_id"),
    ("order_line", "promotion_id", "promotion", "promotion_id"),
    ("return", "order_id", "order", "order_id"),
]


def fk_checks(T: dict[str, pd.DataFrame]) -> dict:
    out = {}
    for c, cc, p, pc_ in FKS:
        vals = pd.to_numeric(T[c][cc].dropna())
        ok = vals.isin(set(T[p][pc_].tolist()))
        out[f"{c}.{cc}->{p}.{pc_}"] = {"integrity": float(ok.mean()) if len(ok) else 1.0, "n": int(len(vals))}
    return out


def fanout(T: dict[str, pd.DataFrame]) -> dict:
    out = {}
    for c, cc, p, pc_ in FKS:
        if c == p:
            continue
        vals = pd.to_numeric(T[c][cc].dropna()).astype("int64")
        cnt = vals.value_counts().reindex(T[p][pc_].to_numpy(), fill_value=0).to_numpy()
        q = np.quantile(cnt, [0.5, 0.9, 0.99])
        out[f"{p}->{c}.{cc}"] = {"p50": float(q[0]), "p90": float(q[1]), "p99": float(q[2]),
                                 "max": int(cnt.max()), "mean": float(cnt.mean()),
                                 "zero_frac": float((cnt == 0).mean()), "_counts": cnt}
    return out


def coherence(T, V) -> dict:
    a = T["address"]
    tup = list(zip(a["city"].astype(str), a["state"].astype(str), a["zip_code"].astype(str)))
    in_ref = np.mean([t in V["zip_tuples"] for t in tup])
    zf = V["zip_full"]
    ll = np.mean([zf.get(z, (None, None, None, None))[2:] == (la, ln)
                  for z, la, ln in zip(a["zip_code"].astype(str), a["lat"], a["lng"])])
    # weighting: record_sample is uniform over ZIP records -> P(state) ∝ #zips in state
    st = pd.Series([z["state"] for z in V["zip_records"]]).value_counts(normalize=True)
    tv = tvd(st, freq(a["state"]))
    return {"city_state_zip_in_reference": float(in_ref), "lat_lng_match_zip_record": float(ll),
            "state_tvd_vs_uniform_over_zip_records": tv}


def rule_checks(T: dict[str, pd.DataFrame], root: str, schema) -> dict:
    load_spindle(root)
    from sqllocks_spindle.engine.rules.business_rules import BusinessRulesEngine
    viol = BusinessRulesEngine().validate(T, schema)
    res = {"spindle_BusinessRulesEngine_violations": {v.rule_name: v.violation_count for v in viol}}
    o, ol, r, c, pr, pm, ad = (T["order"], T["order_line"], T["return"], T["customer"], T["product"],
                               T["promotion"], T["address"])
    sums = ol.groupby("order_id")["line_total"].sum()
    tot = o.set_index("order_id")["order_total"]
    exp = sums.reindex(tot.index, fill_value=0.0).round(2)
    res["order_total_eq_sum_lines"] = float((np.abs(tot - exp) <= 0.0051).mean())
    res["orders_without_lines_frac"] = float((~tot.index.isin(sums.index)).mean())
    res["order_total_zero_frac"] = float((tot == 0).mean())
    om = o.set_index("order_id")
    rd = pd.to_datetime(r["return_date"]).to_numpy()
    od = pd.to_datetime(om["order_date"].reindex(r["order_id"]).to_numpy())
    res["return_date_gt_order_date"] = float((rd > od).mean())
    rdays = (rd - od) / np.timedelta64(1, "D")
    res["return_lag_days_quantiles"] = [float(x) for x in np.quantile(rdays, [0.1, 0.5, 0.9, 0.99])]
    res["return_lag_eq_1day_frac"] = float((np.abs(rdays - 1) < 1 / 86400).mean())
    sd = pd.to_datetime(c.set_index("customer_id")["signup_date"].reindex(o["customer_id"]).to_numpy())
    odd = pd.to_datetime(o["order_date"]).to_numpy()
    res["order_date_ge_signup"] = float((odd >= sd).mean())
    gap = (odd - sd) / np.timedelta64(1, "D")
    res["order_date_eq_signup_plus_1d_frac"] = float((np.abs(gap - 1) < 1e-6).mean())
    res["refund_le_order_total"] = float((r["refund_amount"].to_numpy() <=
                                          om["order_total"].reindex(r["order_id"]).to_numpy() + 1e-9).mean())
    res["refund_zero_frac"] = float((r["refund_amount"] == 0).mean())
    res["cost_lt_unit_price"] = float((pr["cost"] < pr["unit_price"]).mean())
    res["line_total_positive"] = float((ol["line_total"] > 0).mean())
    pp = pr.set_index("product_id")["unit_price"]
    res["line_unit_price_eq_product"] = float((ol["unit_price"].to_numpy() == pp.reindex(ol["product_id"]).to_numpy()).mean())
    opromo = pd.to_numeric(om["promotion_id"]).reindex(ol["order_id"]).to_numpy()
    lpromo = pd.to_numeric(ol["promotion_id"]).to_numpy()
    res["line_promotion_eq_order_promotion"] = float(((opromo == lpromo) | (np.isnan(opromo) & np.isnan(lpromo))).mean())
    disc = pm.set_index("promotion_id")["discount_pct"].reindex(lpromo).to_numpy()
    expd = np.where(np.isnan(lpromo), 0.0, disc)
    res["discount_eq_promotion_pct_or_0"] = float((ol["discount_percent"].to_numpy() == expd).mean())
    lt = np.round(ol["quantity"] * ol["unit_price"] * (1 - ol["discount_percent"] / 100), 2)
    res["line_total_formula"] = float((np.abs(ol["line_total"] - lt) < 1e-9).mean())
    ed = (pd.to_datetime(pm["end_date"]) - pd.to_datetime(pm["start_date"])) / pd.Timedelta(days=1)
    res["promo_duration_days_in_3_30"] = float(ed.between(3, 30).mean())
    res["promo_duration_integral_days"] = float((ed == ed.round()).mean())
    first = ~ad["customer_id"].duplicated(keep="first")
    res["is_primary_eq_first_per_customer"] = float((ad["is_primary"].astype(bool) == first).mean())
    amap = ad.set_index("address_id")["customer_id"]
    sa = pd.to_numeric(o["shipping_address_id"])
    has = sa.notna()
    res["shipping_address_belongs_to_customer"] = float(
        (amap.reindex(sa[has].astype("int64")).to_numpy() == o.loc[has, "customer_id"].to_numpy()).mean())
    cust_with_addr = set(ad["customer_id"].tolist())
    res["shipping_null_iff_customer_has_no_address"] = float(
        ((~has) == ~o["customer_id"].isin(cust_with_addr)).mean())
    pc_ = T["product_category"]
    lvl = pc_.set_index("category_id")["level"]
    par = pd.to_numeric(pc_["parent_category_id"])
    ok = np.where(par.isna(), pc_["level"] == 1, lvl.reindex(par.fillna(-1).astype("int64")).to_numpy() == pc_["level"].to_numpy() - 1)
    res["category_parent_level_consistent"] = float(np.mean(ok))
    res["category_level_counts"] = {str(k): int(v) for k, v in pc_["level"].value_counts().sort_index().items()}
    em = c["email"].dropna()
    fl = c.loc[em.index]
    ok = [e.startswith(f"{f.lower().replace(' ', '')}.{l.lower().replace(' ', '')}")
          for e, f, l in zip(em, fl["first_name"], fl["last_name"])]
    res["email_built_from_row_first_last"] = float(np.mean(ok))
    mpc = o["customer_id"].value_counts()
    res["max_orders_per_customer"] = int(mpc.max())
    od = pd.to_datetime(o["order_date"])
    res["order_hour_dist"] = [round(float(x), 4) for x in od.dt.hour.value_counts(normalize=True).sort_index().tolist()]
    res["order_dow_dist"] = [round(float(x), 4) for x in od.dt.dayofweek.value_counts(normalize=True).sort_index().tolist()]
    res["order_month_dist"] = [round(float(x), 4) for x in od.dt.month.value_counts(normalize=True).sort_index().tolist()]
    return res


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", default="medium")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--port-seed", type=int, default=None,
                    help="seed for the port (default: --seed + 1000, so no RNG stream is shared with Spindle)")
    ap.add_argument("--baseline-seeds", default="43,44,45,46",
                    help="other Spindle seeds used to measure Spindle's own seed-to-seed variation")
    ap.add_argument("--spindle-root", default=port.DEFAULT_SPINDLE_ROOT)
    ap.add_argument("--out", default=str(HERE / "verify_report.json"))
    a = ap.parse_args()
    root = a.spindle_root
    t0 = time.time()
    res_sp = spindle_generate(root, a.scale, a.seed)
    SP = res_sp.tables
    bseeds = [int(x) for x in a.baseline_seeds.split(",") if x]
    load_spindle(root)
    from sqllocks_spindle.inference.comparator import FidelityComparator
    fc = FidelityComparator()
    per_seed: dict = {}
    fc_base = []
    for bs in bseeds:  # one baseline in memory at a time
        BL = spindle_generate(root, a.scale, bs).tables
        for tn in SP:
            for c in SP[tn].columns:
                per_seed.setdefault((tn, c), []).append(baseline_distances(SP[tn][c], BL[tn][c]))
        fc_base.append(fc.compare(SP, BL))
        fanout_base = fanout(BL)
        for k, v in fanout(SP).items():
            per_seed.setdefault(("fanout", k), []).append(ks(v["_counts"].astype(float), fanout_base[k]["_counts"].astype(float)))
        del BL
    B = {k: (merge_baselines(v) if k[0] != "fanout" else max(v)) for k, v in per_seed.items()}
    port_seed = a.seed + 1000 if a.port_seed is None else a.port_seed
    port_tabs = port.generate(a.scale, port_seed, root)
    PO = {k: v.to_pandas() for k, v in port_tabs.items()}
    V = vocabularies(root)
    print(f"generated in {time.time() - t0:.1f}s", file=sys.stderr)

    report: dict = {"scale": a.scale, "seed": a.seed, "port_seed": port_seed, "baseline_seeds": bseeds, "tables": {}}
    # how many port columns are bit-identical to Spindle (only possible with a shared seed)
    report["bit_identical_columns"] = [f"{t}.{c}" for t in SP for c in SP[t].columns
                                       if c in PO[t].columns and same_values(SP[t][c], PO[t][c])]
    flagged = []
    n_cols = n_pass = 0
    # (a) structure
    report["table_order"] = {"spindle": list(SP.keys()), "port": list(PO.keys())}
    for tn in SP:
        sp, po = SP[tn], PO[tn]
        ts, tp = arrow_type_of(sp), {f.name: str(f.type) for f in port_tabs[tn].schema}
        tr = {"rows": {"spindle": len(sp), "port": len(po)},
              "columns_spindle": list(sp.columns), "columns_port": list(po.columns),
              "columns_identical_and_ordered": list(sp.columns) == list(po.columns),
              "arrow_types": {c: {"spindle": ts.get(c), "port": tp.get(c)} for c in sp.columns},
              "columns": {}}
        tr["types_identical"] = all(ts.get(c) == tp.get(c) for c in sp.columns)
        tr["row_counts_identical"] = len(sp) == len(po)
        for c in sp.columns:
            if c not in po.columns:
                flagged.append(f"{tn}.{c}: missing in port")
                continue
            # (b) per-column distribution comparison
            cr = compare_column(tn, c, sp[c], po[c], B[(tn, c)], V)
            cr["arrow_type_match"] = ts.get(c) == tp.get(c)
            cr["equivalent"] = cr["equivalent"] and cr["arrow_type_match"]
            tr["columns"][c] = cr
            n_cols += 1
            n_pass += cr["equivalent"]
            if not cr["equivalent"]:
                flagged.append(f"{tn}.{c}: failed {[k for k, v in cr['checks'].items() if not v]}"
                               + ("" if cr["arrow_type_match"] else " type"))
        report["tables"][tn] = tr

    # (c) FK integrity + fan-out
    report["fk_integrity"] = {"spindle": fk_checks(SP), "port": fk_checks(PO)}
    fs, fp_ = fanout(SP), fanout(PO)
    fo = {}
    for k in fs:
        cs, cp = fs[k].pop("_counts"), fp_[k].pop("_counts")
        d = ks(cs.astype(float), cp.astype(float))
        tol = max(ks_crit(len(cs), len(cp)), 1.5 * B[("fanout", k)] + 0.002)
        fo[k] = {"spindle": fs[k], "port": fp_[k], "ks_counts": d,
                 "ks_baseline_max": B[("fanout", k)], "ks_tol": tol, "equivalent": bool(d <= tol)}
        if not d <= tol:
            flagged.append(f"fan-out {k}: KS {d:.4f} > tol {tol:.4f}")
    report["fanout"] = fo
    # (d) address coherence
    report["address_coherence"] = {"spindle": coherence(SP, V), "port": coherence(PO, V)}
    # (e) business rules
    report["business_rules"] = {"spindle": rule_checks(SP, root, res_sp.schema),
                                "port": rule_checks(PO, root, res_sp.schema)}
    # (f) Spindle's own fidelity comparator: real = Spindle, synthetic = port
    rep = fc.compare(SP, PO)
    report["spindle_fidelity_comparator"] = {
        "overall_port": rep.overall_score,
        "overall_baseline_spindle_vs_spindle": [b.overall_score for b in fc_base],
        "tables": {t: {"port": rep.tables[t].score,
                       "baseline": [b.tables[t].score for b in fc_base],
                       "columns": {c: {"port": rep.tables[t].columns[c].score,
                                       "baseline_min": min(b.tables[t].columns[c].score for b in fc_base)}
                                   for c in rep.tables[t].columns}}
                   for t in rep.tables},
    }
    report["summary"] = {"columns_total": n_cols, "columns_equivalent": n_pass, "flagged": flagged}

    Path(a.out).write_text(json.dumps(report, indent=1, default=str))
    txt = summary_text(report)
    Path(a.out).with_name(Path(a.out).stem.replace("report", "summary") + ".txt").write_text(txt)
    print(txt)


def summary_text(R) -> str:
    L = []
    L.append(f"Spindle retail (seed {R['seed']}) vs port (seed {R['port_seed']}) — scale={R['scale']}  "
             f"(baseline = max distance of Spindle seeds {R['baseline_seeds']} to Spindle seed {R['seed']})")
    L.append(f"bit-identical columns: {len(R['bit_identical_columns'])}")
    L.append(f"table order identical: {R['table_order']['spindle'] == R['table_order']['port']}")
    L.append("")
    L.append(f"{'table.column':34s} {'kind':11s} {'type':5s} {'null s/p':>15s} {'dist(port)':>10s} {'baseline':>9s} {'tol':>7s} {'vocab':>6s}  verdict")
    for tn, tr in R["tables"].items():
        L.append(f"-- {tn}: rows {tr['rows']['spindle']:,} / {tr['rows']['port']:,}  "
                 f"cols identical+ordered={tr['columns_identical_and_ordered']}  types identical={tr['types_identical']}")
        for c, cr in tr["columns"].items():
            if "ks" in cr:
                d, b, t = cr["ks"], cr["ks_baseline"], cr["ks_tol"]; tag = "KS"
            elif "tvd" in cr:
                d, b, t = cr["tvd"], cr["tvd_baseline"], cr["tvd_tol"]; tag = "TVD"
            else:
                d = abs(cr["distinct"]["ratio"] - 1); b = cr["distinct"]["baseline_max_ratio_dev"]; t = cr["distinct_ratio_tol"]; tag = "|dR-1|"
            vo = cr.get("component_overlap", cr.get("vocab_overlap"))
            L.append(f"{tn + '.' + c:34s} {cr['kind']:11s} {'ok' if cr['arrow_type_match'] else 'DIFF':5s} "
                     f"{cr['null_rate']['spindle']:.4f}/{cr['null_rate']['port']:.4f} "
                     f"{tag:>3s} {d:7.4f} {b:9.4f} {t:7.4f} {'' if vo is None else f'{vo:6.4f}':>6s}  "
                     f"{'EQUIVALENT' if cr['equivalent'] else 'NOT EQUIVALENT'}")
    L.append("")
    L.append("FK integrity (spindle / port):")
    for k in R["fk_integrity"]["spindle"]:
        L.append(f"  {k:55s} {R['fk_integrity']['spindle'][k]['integrity']:.4f} / {R['fk_integrity']['port'][k]['integrity']:.4f}")
    L.append("FK fan-out (children per parent)  spindle p50/p90/p99/max/zero% | port | KS (baseline max, tol)")
    for k, v in R["fanout"].items():
        s, p = v["spindle"], v["port"]
        L.append(f"  {k:40s} {s['p50']:.0f}/{s['p90']:.0f}/{s['p99']:.0f}/{s['max']}/{s['zero_frac']:.3f} | "
                 f"{p['p50']:.0f}/{p['p90']:.0f}/{p['p99']:.0f}/{p['max']}/{p['zero_frac']:.3f} | {v['ks_counts']:.4f} ({v['ks_baseline_max']:.4f}, {v['ks_tol']:.4f})")
    L.append("Address coherence (spindle / port):")
    for k in R["address_coherence"]["spindle"]:
        L.append(f"  {k:45s} {R['address_coherence']['spindle'][k]:.4f} / {R['address_coherence']['port'][k]:.4f}")
    L.append("Business rules / cross-table semantics (spindle / port):")
    for k in R["business_rules"]["spindle"]:
        L.append(f"  {k:45s} {json.dumps(R['business_rules']['spindle'][k])} / {json.dumps(R['business_rules']['port'][k])}")
    fc = R["spindle_fidelity_comparator"]
    L.append(f"Spindle FidelityComparator (real=Spindle seed {R['seed']}, synthetic=port): overall port={fc['overall_port']:.2f}  "
             f"Spindle-vs-Spindle baselines={[round(x, 2) for x in fc['overall_baseline_spindle_vs_spindle']]}")
    for t, v in fc["tables"].items():
        L.append(f"  {t:18s} port {v['port']:6.2f}   baselines {[round(x, 2) for x in v['baseline']]}")
    s = R["summary"]
    L.append("")
    L.append(f"COLUMNS EQUIVALENT: {s['columns_equivalent']}/{s['columns_total']}")
    for f in s["flagged"]:
        L.append(f"  NOT EQUIVALENT: {f}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
