"""Comparisons of the two tools' incremental output (runs in the reference venv: pandas, scipy).

Exact where the result is deterministic (counts, key ranges, columns, copies of existing rows,
references that must exist); the T-21 clauses where it is random, with tolerances derived from
the reference's own spread over its seeds. The T-21 functions are the ones the domain verifier uses
(``domain_1to1/verify.py``), loaded from that file, so there is one definition of the rule.
"""

from __future__ import annotations

import importlib.util
import itertools
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
_DOMAIN = HERE.parent / "domain_1to1"
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(_DOMAIN))
_spec = importlib.util.spec_from_file_location("t21_domain_verify", _DOMAIN / "verify.py")
assert _spec and _spec.loader
t21 = importlib.util.module_from_spec(_spec)
sys.modules["t21_domain_verify"] = t21
_spec.loader.exec_module(t21)

DELTA_TYPE, DELTA_TS = "_delta_type", "_delta_timestamp"
MIN_ROWS = 30  # a column of fewer rows than this on either side is reported, not asserted


def normalise(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Shape's delta columns carry its own names; give them the reference's for comparison."""
    out = {}
    for name, df in frames.items():
        out[name] = df.rename(
            columns={"_shape_delta_type": DELTA_TYPE, "_shape_delta_timestamp": DELTA_TS}
        )
    return out


def same_frame(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    if list(a.columns) != list(b.columns) or len(a) != len(b):
        return False
    return all(t21.same_values(a[c], b[c]) for c in a.columns)


# ---- T-21 on one column ------------------------------------------------------------------


def seed_spread(ref: pd.Series, others: list[pd.Series]) -> dict[str, float]:
    """The reference's own seed-to-seed spread: the largest distance (null rate, KS, TVD)
    between any
    two of its runs (seeds 42-46: ten pairs). T-21 states the tolerance from "Spindle's maximum
    seed-to-seed" distance; the domain verifier measures 42 against each of 43-46 (four pairs).
    Here every pair counts, which is the same quantity with a better estimate of its maximum (the
    seed set is unchanged, and the multiplier of 1.5 and the 0.002 term are as in T-21)."""
    runs = [ref, *others]
    return t21.merge_baselines(
        [t21.baseline_distances(a, b) for a, b in itertools.combinations(runs, 2)]
    )


def check_column(
    ref: pd.Series,
    others: list[pd.Series],
    impl: pd.Series,
    pool: set[str] | None = None,
    n_eff: int | None = None,
) -> dict[str, Any]:
    """T-21 (b)-(e) for one column: ``impl`` against ``ref``, with ``others`` (the reference at its
    other seeds) giving the tolerance.

    ``n_eff`` is the effective sample size when the rows are not independent draws: inserted
    rows are
    clones of existing rows, so a column of 2,000 inserts into a 50-row table has 50 independent
    values, not 2,000. The sampling-noise terms of the rule (the KS critical value, the null-rate
    sigma, the multinomial noise) then use ``min(n, n_eff)``; the terms from the reference's seed
    spread are unchanged."""
    if min(len(ref), len(impl)) < MIN_ROWS or any(len(o) < MIN_ROWS for o in others):
        return {
            "skipped": f"fewer than {MIN_ROWS} rows",
            "n": [len(ref), len(impl)],
            "equivalent": True,
        }
    spread = seed_spread(ref, others)
    result: dict[str, Any] = t21.compare_column(ref, impl, spread, pool, None)
    if n_eff is not None and n_eff < min(len(ref), len(impl)):
        _effective_n(result, spread, n_eff)
    return result


def _effective_n(res: dict[str, Any], spread: dict[str, float], n_eff: int) -> None:
    res["n_eff"] = n_eff
    nr = res["null_rate"]
    p = max(nr["spindle"], 1.0 / n_eff)
    nr["tol"] = max(5 * math.sqrt(p * (1 - p) * 2 / n_eff), 1.5 * spread["null"], 1e-12)
    res["checks"]["null_rate"] = bool(abs(nr["impl"] - nr["spindle"]) <= nr["tol"])
    if "ks" in res:
        db = res["ks_baseline"]
        crit = t21.ks_crit(n_eff, n_eff)
        res["ks_tol"] = max(crit, 1.5 * db + 0.002) if not math.isnan(db) else crit
        res["checks"]["ks"] = (
            bool(res["ks"] <= res["ks_tol"]) if not math.isnan(res["ks"]) else True
        )
    if "tvd" in res:
        k = max(res["distinct"]["spindle"], 2)
        noise = math.sqrt(k / (2 * math.pi)) * 2 / math.sqrt(n_eff)
        res["tvd_tol"] = max(3 * noise, 1.5 * res["tvd_baseline"] + 0.002)
        res["checks"]["tvd"] = bool(res["tvd"] <= res["tvd_tol"])
    res["equivalent"] = all(res["checks"].values())


def failed_checks(result: dict[str, Any]) -> list[str]:
    if result.get("skipped"):
        return []
    return [k for k, ok in result["checks"].items() if not ok]


def summarise(result: dict[str, Any]) -> str:
    if result.get("skipped"):
        return "skipped"
    parts = []
    if "ks" in result:
        parts.append(f"ks={result['ks']:.4f}<={result['ks_tol']:.4f}")
    if "tvd" in result:
        parts.append(f"tvd={result['tvd']:.4f}<={result['tvd_tol']:.4f}")
    nr = result["null_rate"]
    parts.append(f"null={nr['impl']:.4f}/{nr['spindle']:.4f}")
    return " ".join(parts)


# ---- keys ---------------------------------------------------------------------------------


def key_set(df: pd.DataFrame, column: str) -> set[Any]:
    return set(df[column].dropna().tolist())


def orphans(child: pd.DataFrame, column: str, valid: set[Any]) -> int:
    values = child[column].dropna()
    return int((~values.isin(valid)).sum()) if len(values) else 0


def is_int_key(df: pd.DataFrame, column: str) -> bool:
    return bool(pd.api.types.is_integer_dtype(df[column]))


# ---- continue -----------------------------------------------------------------------------


def split(combined: dict[str, pd.DataFrame]) -> dict[str, dict[str, pd.DataFrame]]:
    """``{table: {INSERT|UPDATE|DELETE: rows}}`` from the combined delta frames."""
    out: dict[str, dict[str, pd.DataFrame]] = {}
    for table, df in combined.items():
        out[table] = {}
        for kind in ("INSERT", "UPDATE", "DELETE"):
            if DELTA_TYPE in df.columns and len(df):
                out[table][kind] = df[df[DELTA_TYPE] == kind].drop(columns=[DELTA_TYPE, DELTA_TS])
            else:
                out[table][kind] = pd.DataFrame(
                    columns=[c for c in df.columns if c not in (DELTA_TYPE, DELTA_TS)]
                )
    return out


def expected_counts(n_rows: int, scenario: dict[str, Any], *, fix_zero: bool) -> dict[str, int]:
    """Rows per kind: the shared rule, with the reference's minimum of one change per table for a
    fraction of 0 only when ``fix_zero`` is off."""

    def changed(fraction: float) -> int:
        if n_rows == 0:
            return 0
        if fraction <= 0 and fix_zero:
            return 0
        return min(max(1, int(n_rows * fraction)), n_rows)

    inserts = scenario["inserts"] if n_rows else 0
    upd, dele = changed(scenario["update_fraction"]), changed(scenario["delete_fraction"])
    if fix_zero:
        dele = min(dele, n_rows - upd)
    return {"INSERT": inserts, "UPDATE": upd, "DELETE": dele}


def continue_exact(
    parts: dict[str, dict[str, pd.DataFrame]],
    start: dict[str, pd.DataFrame],
    pk: dict[str, list[str]],
    fk: dict[str, dict[str, str]],
    scenario: dict[str, Any],
    *,
    is_shape: bool,
) -> dict[str, Any]:
    """The deterministic properties of one run, and the measures of the defects that were fixed."""
    r: dict[str, Any] = {
        "count_mismatch": [],
        "pk_range": [],
        "delete_not_copy": [],
        "update_bad": [],
        "columns": [],
    }
    r["overlap"] = 0
    r["insert_orphans"] = 0
    r["insert_refs_deleted_parent"] = 0
    r["totals"] = {"INSERT": 0, "UPDATE": 0, "DELETE": 0}
    new_keys: dict[str, set[Any]] = {}
    deleted_keys: dict[str, set[Any]] = {}
    for table, kinds in parts.items():
        key = pk[table][0] if pk.get(table) else None
        if key:
            deleted_keys[table] = key_set(kinds["DELETE"], key) if len(kinds["DELETE"]) else set()
            new_keys[table] = key_set(kinds["INSERT"], key) if len(kinds["INSERT"]) else set()
    for table, kinds in parts.items():
        df0 = start[table]
        key = pk[table][0] if pk.get(table) else None
        want = expected_counts(len(df0), scenario, fix_zero=is_shape)
        for kind, df in kinds.items():
            r["totals"][kind] += len(df)
            if len(df) != want[kind]:
                r["count_mismatch"].append(f"{table}.{kind}: {len(df)} != {want[kind]}")
        for df in kinds.values():
            if len(df) and list(df.columns) != list(df0.columns):
                r["columns"].append(table)
        if not key:
            continue
        index = df0.set_index(key, drop=False)
        ins, upd, dele = kinds["INSERT"], kinds["UPDATE"], kinds["DELETE"]
        if len(ins) and is_int_key(df0, key):
            top = int(df0[key].max())
            if sorted(ins[key].tolist()) != list(range(top + 1, top + 1 + len(ins))):
                r["pk_range"].append(table)
        if len(dele):
            same = index.loc[dele[key]].reset_index(drop=True)
            if not same_frame(
                same[list(df0.columns)], dele[list(df0.columns)].reset_index(drop=True)
            ):
                r["delete_not_copy"].append(table)
        if len(upd):
            if upd[key].duplicated().any() or not upd[key].isin(index.index).all():
                r["update_bad"].append(f"{table}: keys")
            orig = index.loc[upd[key]].reset_index(drop=True)
            for col in fk.get(table, {}):
                if col not in pk[table] and not t21.same_values(
                    orig[col], upd[col].reset_index(drop=True)
                ):
                    r["update_bad"].append(f"{table}.{col}: foreign key changed")
        if len(upd) and len(dele):
            r["overlap"] += len(set(upd[key]) & set(dele[key]))
        for col, parent in fk.get(table, {}).items():
            if col in pk[table] or not len(ins):
                continue
            pkey = pk[parent][0]
            valid = key_set(start[parent], pkey) | new_keys.get(parent, set())
            r["insert_orphans"] += orphans(ins, col, valid)
            r["insert_refs_deleted_parent"] += int(
                ins[col].isin(deleted_keys.get(parent, set())).sum()
            )
    return r


def applied_orphans(
    parts: dict[str, dict[str, pd.DataFrame]],
    start: dict[str, pd.DataFrame],
    pk: dict[str, list[str]],
    fk: dict[str, dict[str, str]],
) -> int:
    """Foreign-key values with no parent after the delta is applied with hard deletes
    (informational: a DELETE row is a soft delete, and neither tool removes the deleted row's
    children)."""
    state: dict[str, pd.DataFrame] = {}
    for table, df in start.items():
        key = pk[table][0] if pk.get(table) else None
        kinds = parts[table]
        if key:
            df = df[~df[key].isin(kinds["DELETE"][key])] if len(kinds["DELETE"]) else df
        state[table] = (
            pd.concat([df, kinds["INSERT"]], ignore_index=True) if len(kinds["INSERT"]) else df
        )
    total = 0
    for table, cols in fk.items():
        for col, parent in cols.items():
            if col in pk[table]:
                continue
            total += orphans(state[table], col, key_set(state[parent], pk[parent][0]))
    return total


def update_stats(
    upd: pd.DataFrame, start: pd.DataFrame, key: str, columns: list[str]
) -> dict[str, dict[str, Any]]:
    """Per column of an UPDATE: whether each value changed, by what factor (numbers) or days
    (dates)."""
    orig = start.set_index(key, drop=False).loc[upd[key]].reset_index(drop=True)
    new = upd.reset_index(drop=True)
    out: dict[str, dict[str, Any]] = {}
    for col in columns:
        a, b = orig[col], new[col]
        k = t21.kind(a)
        changed = ~((a == b) | (a.isna() & b.isna())).to_numpy()
        entry: dict[str, Any] = {"changed": pd.Series(changed.astype(float)), "n": len(a)}
        if k == "numeric" and changed.any():
            av, bv = t21.as_num(a[changed]), t21.as_num(b[changed])
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = bv / av
            entry["ratio"] = pd.Series(ratio[np.isfinite(ratio)])
        if k == "datetime" and changed.any():
            entry["days"] = pd.Series(
                (t21.as_num(b[changed]) - t21.as_num(a[changed])) / (86400 * 1e9)
            )
        out[col] = entry
    return out


def tvd_to_spec(
    old: pd.Series, new: pd.Series, spec: dict[str, dict[str, float]]
) -> list[dict[str, Any]]:
    """For every listed state, the distance of the observed next-state frequencies to the spec."""
    rows = []
    for state, nxt in spec.items():
        mask = old.astype(str) == state
        n = int(mask.sum())
        if not n:
            continue
        total = sum(nxt.values())
        want = pd.Series({k: v / total for k, v in nxt.items()})
        got = new[mask].astype(str).value_counts(normalize=True)
        d = t21.tvd(want, got)
        noise = math.sqrt(max(len(want), 2) / (2 * math.pi * n))
        rows.append({"state": state, "n": n, "tvd": d, "tol": 3 * noise, "ok": d <= 3 * noise})
    return rows


# ---- foreign-key fan-out (T-21 (f)) ----------------------------------------------------------


def _fanout_of(counts: np.ndarray) -> dict[str, float]:
    total = counts.sum()
    if total == 0 or len(counts) == 0:
        return {"cv": float("nan"), "top1pct": float("nan"), "no_children": float("nan")}
    top = max(1, len(counts) // 100)
    return {
        "cv": float(counts.std() / counts.mean()),
        "top1pct": float(np.sort(counts)[::-1][:top].sum() / total),
        "no_children": float((counts == 0).mean()),
    }


BOOTSTRAP_REPS = 200


def fanout_metrics(
    child: pd.DataFrame, column: str, parent_keys: set[Any]
) -> dict[str, tuple[float, float]]:
    """``{metric: (value, standard error)}`` of the children-per-parent distribution over the
    parents
    that exist: the coefficient of variation of the counts, the share of the children held by
    the top
    1% of parents, and the fraction of parents without children. Children whose value is not a
    parent
    key are not counted. The standard error is a bootstrap over the children (200 fixed-seed
    resamples), the sampling noise of the statistic."""
    keys = sorted(parent_keys)
    values = child[column].dropna()
    values = values[values.isin(parent_keys)]
    position = pd.Index(keys).get_indexer(values)
    counts = np.bincount(position, minlength=len(keys)).astype(float)
    point = _fanout_of(counts)
    rng = np.random.default_rng(0)
    boots = (
        [
            _fanout_of(
                np.bincount(rng.choice(position, size=len(position)), minlength=len(keys)).astype(
                    float
                )
            )
            for _ in range(BOOTSTRAP_REPS)
        ]
        if len(position)
        else []
    )
    return {
        k: (point[k], float(np.std([b[k] for b in boots])) if boots else float("nan"))
        for k in point
    }


def fanout_check(
    impl: dict[str, tuple[float, float]],
    ref: dict[str, tuple[float, float]],
    others: list[dict[str, tuple[float, float]]],
) -> dict[str, Any]:
    """T-21 (f), each metric of ``impl`` against ``ref``: within the larger of (a) 1.5 x the
    reference's largest seed-to-seed distance (any two of its five runs) plus 0.002, the shape of
    the (c) and (d) tolerances, and (b) 5 standard errors of the difference, the sampling-noise term
    of the null-rate rule (the counterpart of the KS critical value, which these statistics
    lack)."""
    out: dict[str, Any] = {"ok": True, "metrics": {}}
    runs = [ref, *others]
    for name, (value, se) in impl.items():
        spread = max(abs(a[name][0] - b[name][0]) for a, b in itertools.combinations(runs, 2))
        noise = 5 * math.sqrt(se**2 + ref[name][1] ** 2)
        tol = max(1.5 * spread + 0.002, noise)
        ok = bool(abs(value - ref[name][0]) <= tol)
        out["metrics"][name] = {
            "shape": value,
            "reference": ref[name][0],
            "spread": spread,
            "noise": noise,
            "tol": tol,
            "ok": ok,
        }
        out["ok"] &= ok
    return out


def repair_orphans(
    tables: dict[str, pd.DataFrame],
    pk: dict[str, list[str]],
    fk: dict[str, dict[str, str]],
    rng: np.random.Generator,
) -> dict[str, pd.DataFrame]:
    """The documented TT-ORPHANS rule applied to a reference snapshot, written independently of
    Shape's code: a child row whose parent is gone takes the foreign key of a child row that has a
    parent, chosen at random (so a parent is chosen in proportion to its children). The
    comparison of
    everything else then does not depend on the fix. ``rng`` is one generator for a whole run, so
    the draws of different months are independent."""
    out = {t: df.copy() for t, df in tables.items()}
    for table, cols in fk.items():
        for col, parent in cols.items():
            if col in pk[table]:
                continue
            valid = key_set(out[parent], pk[parent][0])
            values = out[table][col]
            known = values.isin(valid)
            bad = values.notna() & ~known
            if bad.any() and known.any():
                out[table].loc[bad, col] = rng.choice(values[known].to_numpy(), size=int(bad.sum()))
    return out


def updated_column(df: pd.DataFrame, key: str) -> str | None:
    """The one column a time-travel update changes: the first numeric column that is neither the key
    nor an ``*_id`` column."""
    for col in df.columns:
        if (
            col != key
            and not col.endswith("_id")
            and pd.api.types.is_numeric_dtype(df[col])
            and not pd.api.types.is_bool_dtype(df[col])
        ):
            return str(col)
    return None
