"""Case: the SCD2 file-drop simulator (``shape_simulation.scd2_file_drops``) against the baseline's.

Mechanism parity (same input, same seed: the same rows in the same deltas, apart from the
allow-listed version columns), the version-chain invariant that shows the defects, T-21 on the
written rows and the negative controls. See ``verify.py`` for the rules.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import sim_common as sc
import sim_compare as cmp
import sim_trees as trees

NAME = "scd2_file_drops"

ALLOWED: dict[str, dict[str, str]] = {
    "SCD-1": {
        "what": "valid_from of an expired version",
        "baseline": "the state is built from the table, not from the snapshot, so the first time a "
        "record changes its expired row has no valid_from (null) although the snapshot gave it one",
        "shape": "the expired row keeps the valid_from of the version it expires (the initial date, "
        "or the day the entity was inserted)",
    },
    "SCD-2": {
        "what": "version columns of an inserted entity",
        "baseline": "an insert copies the first entity's row as it is in the state; while that "
        "entity has never changed it has no version columns, so the insert has valid_from null, "
        "valid_to null and is_current null (not true)",
        "shape": "an insert has valid_from = the day, valid_to null and is_current true",
    },
    "SCD-4": {
        "what": "changing an integer column that has nulls",
        "baseline": "pandas holds such a column as float, so a changed value is the product with "
        "the factor, unrounded: an id column ends with values such as 889.73",
        "shape": "an integer stays an integer: the product is rounded to the nearest integer "
        "(halves to even)",
    },
    "SCD-5": {
        "what": "a business key that is not numeric",
        "baseline": "the first delta mixes text keys with the integer keys of the inserts, and the "
        "write fails with an Arrow conversion error after the snapshot is on disk",
        "shape": "the run raises a ValueError that names the key column, before anything is written",
    },
    "SCD-3": {
        "what": "column order of the delta files",
        "baseline": "a delta's columns are the union of its rows' keys in order of first "
        "appearance: valid_to, is_current, _delta_type and valid_from come after the table's "
        "columns in an order that depends on the first row, and differs from the snapshot's",
        "shape": "every delta has the snapshot's columns in the snapshot's order, then _delta_type",
    },
}

TABLES_ORDER = (
    ["order"],
    {
        "domain": "retail",
        "business_key_column": "order_id",
        "scd2_columns": ["status", "order_total", "promotion_id", "shipping_address_id"],
        "num_delta_days": 12,
        "daily_change_rate": 0.1,
        "daily_new_rate": 0.05,
        "seed": 31,
    },
)
JOBS: dict[str, tuple[list[str], dict[str, Any]]] = {
    "customers": (
        ["customer"],
        {
            "domain": "retail",
            "business_key_column": "customer_id",
            "scd2_columns": ["loyalty_tier", "email", "is_active", "signup_date"],
            "num_delta_days": 10,
            "daily_change_rate": 0.05,
            "daily_new_rate": 0.02,
            "formats": ["parquet", "csv"],
            "seed": 30,
        },
    ),
    "orders_with_nulls": TABLES_ORDER,
}
INLINE = {
    "t": {
        "id": [f"k{i}" for i in range(60)],
        "n": [i * 3 for i in range(60)],
        "flag": [i % 2 == 0 for i in range(60)],
        "amount": [i * 1.5 for i in range(60)],
        "label": [f"name{i}" for i in range(60)],
    }
}
INLINE_CONFIG: dict[str, Any] = {
    "domain": "retail",
    "business_key_column": "id",
    "scd2_columns": ["n", "flag", "amount", "label"],
    "num_delta_days": 6,
    "daily_change_rate": 0.1,
    "daily_new_rate": 0.1,
    "seed": 32,
}
T21_CONFIG: dict[str, Any] = {
    "domain": "retail",
    "business_key_column": "order_id",
    "scd2_columns": ["status", "order_total"],
    "num_delta_days": 10,
    "daily_change_rate": 0.05,
    "daily_new_rate": 0.02,
}
VERSION_COLUMNS = ("valid_from", "valid_to", "is_current")


# ---- the two sides ------------------------------------------------------------------------


def _result(res: Any, root: Path) -> dict[str, Any]:
    rel = lambda ps: [str(Path(p).relative_to(root)) for p in ps]  # noqa: E731
    return {
        "initial": str(Path(res.initial_load_path).relative_to(root)),
        "deltas": rel(res.delta_paths),
        "manifests": rel(res.manifest_paths),
        "stats": res.stats,
    }


def baseline_side(job: dict[str, Any]) -> dict[str, Any]:
    from sqllocks_spindle.simulation.scd2_file_drops import SCD2FileDropConfig, SCD2FileDropSimulator

    if "inline" in job:
        tables = {n: pd.DataFrame(cols) for n, cols in job["inline"].items()}
    else:
        tables = {t: pd.read_parquet(Path(job["input_dir"]) / f"{t}.parquet") for t in job["tables"]}
    root = Path(job["out_dir"]) / "landing"
    cfg = SCD2FileDropConfig(base_path=str(root), **job["config"])
    return _result(SCD2FileDropSimulator(tables=tables, config=cfg).run(), root)


def shape_side(job: dict[str, Any]) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from shape_simulation.scd2_file_drops import SCD2FileDropConfig, SCD2FileDropSimulator

    if "inline" in job:
        tables = {n: pa.table(cols) for n, cols in job["inline"].items()}
    else:
        tables = {t: pq.read_table(Path(job["input_dir"]) / f"{t}.parquet") for t in job["tables"]}
    root = Path(job["out_dir"]) / "landing"
    cfg = SCD2FileDropConfig(base_path=str(root), **job["config"])
    return _result(SCD2FileDropSimulator(tables=tables, config=cfg).run(), root)


# ---- helpers ------------------------------------------------------------------------------


def _root(result: dict[str, Any]) -> Path:
    return Path(result["out_dir"]) / "landing"


def _day_of(path: str) -> pd.Timestamp | None:
    for part in path.split("/"):
        if part.startswith("dt="):
            return pd.Timestamp(part[3:])
    return None


def _inserted_days(root: Path, result: dict[str, Any], key: str) -> dict[Any, pd.Timestamp]:
    """Shape's own record of the day each inserted entity first appeared."""
    days: dict[Any, pd.Timestamp] = {}
    for rel in result["deltas"]:
        frame = cmp.read_frame(root / rel)
        day = _day_of(rel)
        for k in frame.loc[frame["_delta_type"] == "insert", key]:
            days.setdefault(k, day)
    return days


def _explain(
    seen: dict[str, int], initial_date: str, key: str, inserted: dict[Any, pd.Timestamp]
) -> Any:
    """The allowed differences of a delta file. SCD-3 is the column order (columns are compared
    as a set). SCD-1, SCD-2: only ``valid_from`` and ``is_current``, only on the rows the entries
    name, and Shape's values there are the right ones. SCD-4: a column Shape holds as integer, whose
    baseline value is the unrounded product: the two differ only by the rounding Shape does at each
    change (a bound that grows with the days run)."""

    def explain(path: str, base: pd.DataFrame, shape: pd.DataFrame) -> bool:
        if "/delta/" not in path or len(base) != len(shape):
            return False
        def whole(s: pd.Series) -> bool:
            return bool((s.dropna() % 1 == 0).all())

        int_cols = [
            c
            for c in shape.columns
            if c in base.columns
            and c not in ("valid_from", "is_current")
            and pd.api.types.is_float_dtype(base[c].dtype)
            and pd.api.types.is_float_dtype(shape[c].dtype)
            and whole(shape[c])
            and not whole(base[c])
        ]
        rest = ("valid_from", "is_current", *int_cols)
        ok, _ = cmp.frames_equal(base, shape, ignore=rest)
        if not ok:
            return False
        day, first = _day_of(path), pd.Timestamp(initial_date)
        for c in int_cols:  # SCD-4
            nb, ns = base[c].isna().to_numpy(), shape[c].isna().to_numpy()
            if not (nb == ns).all():
                return False
            # The baseline keeps the unrounded product, Shape rounds at every change: after k
            # changes they differ by at most 0.5 * (1 + 1.2 + ... + 1.2^(k-1)), k at most the days run.
            k = max(0, (day - first).days) if day is not None else 0
            bound = 0.5 * (1.2**k - 1) / 0.2
            gap = np.abs(base[c][~nb].to_numpy(dtype=float) - shape[c][~ns].to_numpy(dtype=float))
            if not (gap <= bound).all():
                return False
            seen["rounded_int_columns"] = seen.get("rounded_int_columns", 0) + 1
        bv, sv = cmp.norm_series(base["valid_from"]), cmp.norm_series(shape["valid_from"])
        bc, sc_ = base["is_current"], shape["is_current"]
        kind = shape["_delta_type"].reset_index(drop=True)
        for i in range(len(base)):
            same_from = (pd.isna(bv.iloc[i]) and pd.isna(sv.iloc[i])) or bv.iloc[i] == sv.iloc[i]
            same_cur = (pd.isna(bc.iloc[i]) and pd.isna(sc_.iloc[i])) or bc.iloc[i] == sc_.iloc[i]
            if same_from and same_cur:
                continue
            if kind.iloc[i] == "insert":  # SCD-2
                good = bool(
                    pd.isna(bv.iloc[i]) and sv.iloc[i] == day and pd.isna(bc.iloc[i]) and sc_.iloc[i] == True  # noqa: E712
                )
                seen["inserts"] = seen.get("inserts", 0) + int(good)
            else:  # SCD-1: an expired row whose valid_from the baseline lost
                want = inserted.get(shape[key].iloc[i], first)
                good = bool(pd.isna(bv.iloc[i]) and sv.iloc[i] == want and same_cur)
                seen["expired"] = seen.get("expired", 0) + int(good)
            if not good:
                return False
        return True

    return explain


def chain_problems(frames: list[pd.DataFrame], key: str) -> dict[str, int]:
    """Version-chain violations once the deltas are applied over the snapshot (a later record of
    the same entity and valid_from replaces an earlier one): a version without valid_from, a
    current flag that is neither true nor false, a gap or overlap between an entity's versions
    (the valid_to of one must be the valid_from of the next), a last version that has an end, and
    an entity without exactly one current version."""
    allrows = pd.concat([cmp.norm_frame(f.reindex(columns=frames[0].columns)) for f in frames], ignore_index=True)
    bad = {"no_valid_from": int(allrows["valid_from"].isna().sum())}
    bad["no_current_flag"] = int(allrows["is_current"].isna().sum())
    state = allrows.drop_duplicates(subset=[key, "valid_from"], keep="last")
    gaps = open_end = not_one_current = 0
    for _, g in state.groupby(key, sort=False):
        g = g.sort_values("valid_from", na_position="first", kind="stable")
        cur = g["is_current"].fillna(False).astype(bool)
        if int(cur.sum()) != 1:
            not_one_current += 1
        ends, starts = g["valid_to"].iloc[:-1].tolist(), g["valid_from"].iloc[1:].tolist()
        gaps += sum(1 for e, s in zip(ends, starts, strict=True) if pd.isna(e) or pd.isna(s) or e != s)
        open_end += int(not pd.isna(g["valid_to"].iloc[-1]))
    bad["version_gaps"], bad["not_exactly_one_current"], bad["last_version_closed"] = gaps, not_one_current, open_end
    return bad


def _frames(root: Path, result: dict[str, Any], entity: str) -> tuple[pd.DataFrame, list[pd.DataFrame]]:
    initial = cmp.read_frame(root / result["initial"])  # the first format
    deltas = [
        cmp.read_frame(root / d)
        for d in result["deltas"]
        if f"/{entity}/" in f"/{d}" and d.endswith(".parquet")
    ]
    return initial, deltas


def _concat(initial: pd.DataFrame, deltas: list[pd.DataFrame]) -> pd.DataFrame:
    """The snapshot and the deltas as one frame in the snapshot's column order (the delta
    column order is the allow-list's SCD-3, compared on its own)."""
    cols = list(initial.columns)
    return pd.concat([initial, *[d.reindex(columns=cols) for d in deltas]], ignore_index=True)


# ---- mechanism parity ---------------------------------------------------------------------


def mechanism(
    ctx: Any, checks: sc.Checks, name: str, params: dict[str, Any]
) -> dict[str, Any]:
    b = ctx.run("baseline", NAME, name, **params)
    s = ctx.run("shape", NAME, name, **params)
    if "error" in b or "error" in s:
        checks.add(f"{name}: both ran", False, f"baseline {b.get('error')}; Shape {s.get('error')}")
        return {}
    seen: dict[str, int] = {}
    cfg = params["config"]
    key = cfg["business_key_column"]
    trees.compare_trees(
        checks,
        name,
        _root(b),
        _root(s),
        explain=_explain(seen, cfg.get("initial_load_date", "2024-01-01"), key, _inserted_days(_root(s), s, key)),
        same_column_order=False,
    )
    checks.add(f"{name}: stats equal", b["stats"] == s["stats"], f"{s['stats']}")
    checks.add(
        f"{name}: initial snapshot has the same columns in the same order",
        list(cmp.read_frame(_root(b) / b["initial"]).columns) == list(cmp.read_frame(_root(s) / s["initial"]).columns),
        "",
    )
    return {"baseline": b, "shape": s, **seen}


# ---- probes -------------------------------------------------------------------------------


def probes(ctx: Any, checks: sc.Checks, runs: dict[str, dict[str, Any]]) -> None:
    for name, info in runs.items():
        if not info:
            continue
        b, s = info["baseline"], info["shape"]
        entity = next(iter(JOBS.get(name, ([name],))[0])) if name in JOBS else "t"
        bi, bd = _frames(_root(b), b, entity)
        si, sd = _frames(_root(s), s, entity)
        key = {"customers": "customer_id", "orders_with_nulls": "order_id"}.get(name, "id")
        pb, ps = chain_problems([bi, *bd], key), chain_problems([si, *sd], key)
        checks.add(
            f"{name}: Shape's version chain is consistent (no missing valid_from or is_current, no gaps, one current version per entity)",
            all(v == 0 for v in ps.values()),
            f"Shape {ps}",
        )
        if name == "customers":
            checks.add(
                "SCD-1 / SCD-2 probe: the baseline's chain is broken, and the differences found are exactly those rows",
                pb["no_valid_from"] > 0 and pb["no_current_flag"] > 0 and info.get("expired", 0) > 0 and info.get("inserts", 0) > 0,
                f"baseline {pb}; explained expired rows {info.get('expired')}, inserts {info.get('inserts')}",
            )
            order_b = [list(d.columns) for d in bd]
            order_s = [list(d.columns) for d in sd]
            snap = list(si.columns)
            checks.add(
                "SCD-3 probe: baseline delta columns differ from the snapshot's, Shape's are the snapshot's plus _delta_type",
                any(c != [*snap, "_delta_type"] for c in order_b) and all(c == [*snap, "_delta_type"] for c in order_s),
                f"baseline {order_b[0]}; Shape {order_s[0]}",
            )


def probe_scd4(checks: sc.Checks, info: dict[str, Any]) -> None:
    """SCD-4: the baseline's changed integer ids are fractional; Shape's are whole numbers."""
    if not info:
        checks.add("SCD-4 probe: ran", False, "the orders_with_nulls job did not run")
        return
    b, s = info["baseline"], info["shape"]
    _, bd = _frames(_root(b), b, "order")
    _, sd = _frames(_root(s), s, "order")
    frac = lambda ds: int(sum(((d["shipping_address_id"].dropna() % 1) != 0).sum() for d in ds))  # noqa: E731
    checks.add(
        "SCD-4 probe: baseline has fractional shipping_address_id values after a change, Shape has none",
        frac(bd) > 0 and frac(sd) == 0 and info.get("rounded_int_columns", 0) > 0,
        f"fractional values: baseline {frac(bd)}, Shape {frac(sd)}; delta files explained {info.get('rounded_int_columns')}",
    )


def probe_scd5(ctx: Any, checks: sc.Checks) -> None:
    """SCD-5: a text business key. Both raise; the baseline only after writing the snapshot."""
    b = ctx.run("baseline", NAME, "string_keys", inline=INLINE, config=INLINE_CONFIG)
    s = ctx.run("shape", NAME, "string_keys", inline=INLINE, config=INLINE_CONFIG)
    wrote = any(trees.is_data(p) for p in trees.list_files(Path(b["out_dir"])))
    wrote_s = any(trees.is_data(p) for p in trees.list_files(Path(s["out_dir"])))
    checks.add(
        "SCD-5 probe: baseline fails late (snapshot already written), Shape raises first and names the key column",
        "error" in b and wrote and "error" in s and not wrote_s and "business key" in s["error"],
        f"baseline {b.get('error', '')[:70]} (snapshot written: {wrote}); Shape {s.get('error')} (written: {wrote_s})",
    )


# ---- T-21 ---------------------------------------------------------------------------------


def t21(ctx: Any, checks: sc.Checks) -> None:
    def run_one(side: str, tool: str, seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
        r = ctx.run(
            side,
            NAME,
            f"t21_{side}_{seed}",
            input_dir=str(ctx.input_dir(tool, seed)),
            tables=["order"],
            config={**T21_CONFIG, "seed": seed},
        )
        if "error" in r:
            raise RuntimeError(r["error"])
        init, deltas = _frames(_root(r), r, "order")
        return _concat(init, deltas), r["stats"]

    def parts(df: pd.DataFrame) -> pd.DataFrame:
        """status as its components (T-21 (e)): the base value, the change count (a ``_v<N>``
        suffix) and whether the entity was inserted by the run (a ``_new<key>`` suffix, which
        carries the new key and is unique)."""
        out = df.copy()
        text = out["status"].astype(str)
        out["status_version"] = pd.to_numeric(text.str.extract(r"_v(\d+)")[0]).fillna(1).astype(int)
        out["status_inserted"] = text.str.contains("_new").astype(int)
        out["status"] = text.str.split("_").str[0]
        return out

    ref, ref_stats = run_one("baseline", "baseline", ctx.ref_seed)
    spread = [run_one("baseline", "baseline", sd) for sd in ctx.seeds]
    shape, shape_stats = run_one("shape", "shape", ctx.shape_seed)
    skip = ("valid_from", "is_current")  # SCD-1 / SCD-2: shown by the version-chain probe
    result = cmp.t21_columns(parts(ref), [parts(f) for f, _ in spread], parts(shape), skip=skip)
    bad = [c for c, v in result.items() if not v.get("equivalent", False)]
    checks.add(f"T-21 order: (a)-(e) on {len(result) - 1} columns (valid_from, is_current: SCD-1/2)", not bad, f"not equivalent: {bad}")
    for key in ("initial_rows", "total_deltas", "total_new", "total_updates"):
        ok, why = cmp.count_within(shape_stats[key], ref_stats[key], [st[key] for _, st in spread])
        checks.add(f"T-21 order: {key}", ok, why)
    checks.add(
        "T-21 order: valid_from and is_current of Shape's rows are complete (no nulls where the baseline has them)",
        int(cmp.norm_frame(shape)["valid_from"].isna().sum()) == 0 and int(shape["is_current"].isna().sum()) == 0,
        "",
    )


# ---- run ----------------------------------------------------------------------------------


def run(ctx: Any) -> sc.Checks:
    checks = sc.Checks(NAME)
    runs: dict[str, dict[str, Any]] = {}
    for name, (tables, config) in JOBS.items():
        runs[name] = mechanism(ctx, checks, name, {"input_dir": str(ctx.exact_dir), "tables": tables, "config": config})
    probes(ctx, checks, runs)
    probe_scd4(checks, runs.get("orders_with_nulls", {}))
    probe_scd5(ctx, checks)
    t21(ctx, checks)
    return checks


# ---- negative controls --------------------------------------------------------------------


def negative_controls(ctx: Any) -> sc.Checks:
    checks = sc.Checks(NAME + " negative control")
    tables, config = JOBS["customers"]
    params = {"input_dir": str(ctx.exact_dir), "tables": tables, "config": config}
    b = ctx.run("baseline", NAME, "neg_base", **params)
    s = ctx.run("shape", NAME, "neg_shape", **params)

    inserted = _inserted_days(_root(s), s, "customer_id")

    def caught(label: str, tamper: Any) -> None:
        work = sc.work_dir(NAME, "neg_tampered")
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(_root(s), work)
        tamper(work)
        probe = sc.Checks("probe")
        seen: dict[str, int] = {}
        trees.compare_trees(
            probe,
            "tampered",
            _root(b),
            work,
            explain=_explain(seen, "2024-01-01", "customer_id", inserted),
            same_column_order=False,
        )
        checks.add(f"caught: {label}", not probe.ok, "; ".join(c.name for c in probe.items if not c.ok))

    def delta_files(root: Path) -> list[Path]:
        return sorted(root.rglob("customer_delta.parquet"))

    def change_value(root: Path) -> None:
        p = delta_files(root)[0]
        df = pd.read_parquet(p)
        df.loc[df.index[0], "customer_id"] = int(df["customer_id"].iloc[0]) + 100000
        df.to_parquet(p)

    def change_loyalty(root: Path) -> None:
        p = delta_files(root)[1]
        df = pd.read_parquet(p)
        df["loyalty_tier"] = df["loyalty_tier"] + "x"
        df.to_parquet(p)

    def wrong_valid_from(root: Path) -> None:
        # an expired row whose valid_from is not the initial date: SCD-1 must not hide this
        p = delta_files(root)[0]
        df = pd.read_parquet(p)
        i = df.index[df["is_current"] == False][0]  # noqa: E712
        df.loc[i, "valid_from"] = pd.Timestamp("2030-01-01")
        df.to_parquet(p)

    def wrong_flag(root: Path) -> None:
        p = delta_files(root)[0]
        df = pd.read_parquet(p)
        i = df.index[df["_delta_type"] == "insert"][0]
        df.loc[i, "is_current"] = False
        df.to_parquet(p)

    def drop_delta(root: Path) -> None:
        delta_files(root)[2].unlink()

    def drop_row(root: Path) -> None:
        p = delta_files(root)[3]
        pd.read_parquet(p).iloc[1:].to_parquet(p)

    caught("a changed key", change_value)
    caught("a changed text column", change_loyalty)
    caught("an expired row with another valid_from than the initial date", wrong_valid_from)
    caught("an inserted row flagged not current", wrong_flag)
    caught("a missing delta file", drop_delta)
    caught("a missing delta row", drop_row)

    # the chain invariant itself must flag a broken chain
    si, sd = _frames(_root(s), s, "customer")
    clean = chain_problems([si, *sd], "customer_id")
    checks.add("control: untouched Shape chain is consistent", all(v == 0 for v in clean.values()), str(clean))
    broken = [d.copy() for d in sd]
    broken[0].loc[broken[0].index[0], "valid_from"] = None
    checks.add("caught: a version without valid_from", chain_problems([si, *broken], "customer_id")["no_valid_from"] > 0, "")
    gap = [d.copy() for d in sd]
    j = gap[0].index[gap[0]["is_current"] == False][0]  # noqa: E712
    gap[0].loc[j, "valid_to"] = pd.Timestamp("2030-01-01")
    checks.add("caught: a gap between versions", chain_problems([si, *gap], "customer_id")["version_gaps"] > 0, "")
    return checks
