"""Case: the file-drop simulator (``shape_simulation.file_drop``) against the baseline's.

Mechanism parity (same input, same seed: identical trees), the allow-list probes, T-21 on the
written rows, and the negative controls. See ``verify.py`` for the rules.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import sim_common as sc
import sim_compare as cmp
import sim_trees as trees

NAME = "file_drop"

ALLOWED: dict[str, dict[str, str]] = {
    "FD-1": {
        "what": "multi_file_enabled",
        "baseline": "the run raises (it splits a frame with numpy, which returns arrays, and then "
        "asks the array for .empty): a drop with several files per partition cannot be made",
        "shape": "the partition is split into multi_file_chunks files of nearly equal size, each "
        "with a checksum in the manifest",
    },
    "FD-2": {
        "what": "late arrivals landing in the same partition",
        "baseline": "each source slot's late rows are written to <partition>_00900 immediately, so "
        "a later slot's late rows for the same partition replace the file: those rows are lost, "
        "and the result lists the replaced path twice",
        "shape": "the late rows of every slot that land in one partition share that one 00900 file "
        "(oldest slot first); nothing is lost and each file is listed once",
    },
    "FD-3": {
        "what": "time column heuristic",
        "baseline": "a column named like a date (date, timestamp, created, updated) is the time "
        "column when pandas can parse its values: an integer column such as updated_count is "
        "read as nanoseconds since 1970, every row falls outside the date range and the drop "
        "silently writes nothing",
        "shape": "only dates, timestamps and ISO-8601 text can be the time column; a table without "
        "one is dealt out round robin",
    },
    "FD-5": {
        "what": "JSON Lines timestamps",
        "baseline": "a timestamp is written to the millisecond (pandas to_json's default unit), so "
        "a value with microseconds or nanoseconds silently loses them: the JSON Lines file does "
        "not hold what the Parquet file of the same drop holds",
        "shape": "the full precision of the column is written",
    },
    "FD-4": {
        "what": "invalid configuration",
        "baseline": "an unknown cadence is treated as daily (the manifest still says the unknown "
        "name), and an end date before the start date writes nothing without an error",
        "shape": "both raise a ValueError that names the setting",
    },
}

RANGE = {"date_range_start": "2022-01-01", "date_range_end": "2023-12-31"}
ENTITIES = ["order", "customer", "return", "promotion", "product"]
# name -> (tables, config)
JOBS: dict[str, tuple[list[str], dict[str, Any]]] = {
    "daily_all": (
        ENTITIES,
        {
            **RANGE,
            "domain": "retail",
            "lateness_probability": 0.2,
            "duplicates_enabled": True,
            "duplicate_probability": 0.05,
            "backfill_enabled": True,
            "max_days_back": 5,
            "restatement_enabled": True,
            "restatement_probability": 0.1,
            "seed": 11,
        },
    ),
    "hourly_formats": (
        ["order", "customer"],
        {
            **RANGE,
            "date_range_end": "2022-03-31",
            "domain": "retail",
            "cadence": "hourly",
            "formats": ["parquet", "csv", "jsonl"],
            "lateness_probability": 0.3,
            "seed": 12,
        },
    ),
    "quarter_hour_round_robin": (
        ["product", "store"],
        {
            "date_range_start": "2022-01-01",
            "date_range_end": "2022-01-02",
            "domain": "retail",
            "cadence": "every_15m",
            "duplicates_enabled": True,
            "seed": 13,
        },
    ),
    "plain": (
        ["order", "return"],
        {
            **RANGE,
            "domain": "retail",
            "lateness_enabled": False,
            "manifest_enabled": False,
            "done_flag_enabled": False,
            "file_naming": "{entity}-{dt}-{seq}.{ext}",
            "partitioning": "year=YYYY-MM-DD",
            "seed": 14,
        },
    ),
}
COLLISIONS = (
    ["order", "customer"],
    {**RANGE, "domain": "retail", "lateness_probability": 0.5, "max_days_late": 3, "seed": 5},
)
T21_TABLES = ["order", "return"]
T21_CONFIG: dict[str, Any] = {
    "date_range_start": "2022-01-01",
    "date_range_end": "2025-12-31",
    "domain": "retail",
    "lateness_probability": 0.1,
    "duplicates_enabled": True,
    "duplicate_probability": 0.05,
}


# ---- the two sides ------------------------------------------------------------------------


def _paths(res: Any, root: Path) -> dict[str, Any]:
    rel = lambda ps: [str(Path(p).relative_to(root)) for p in ps]  # noqa: E731
    return {
        "listed": rel(res.files_written),
        "manifests": rel(res.manifest_paths),
        "done": rel(res.done_flag_paths),
        "stats": res.stats,
    }


def baseline_side(job: dict[str, Any]) -> dict[str, Any]:
    from sqllocks_spindle.simulation import FileDropConfig, FileDropSimulator

    if "inline" in job:
        tables = {n: pd.DataFrame(cols) for n, cols in job["inline"].items()}
    else:
        tables = {t: pd.read_parquet(Path(job["input_dir"]) / f"{t}.parquet") for t in job["tables"]}
    root = Path(job["out_dir"]) / "landing"
    cfg = FileDropConfig(base_path=str(root), **job["config"])
    return _paths(FileDropSimulator(tables=tables, config=cfg).run(), root)


def shape_side(job: dict[str, Any]) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from shape_simulation.file_drop import FileDropConfig, FileDropSimulator

    if "inline" in job:
        tables = {n: pa.table(cols) for n, cols in job["inline"].items()}
    else:
        tables = {t: pq.read_table(Path(job["input_dir"]) / f"{t}.parquet") for t in job["tables"]}
    root = Path(job["out_dir"]) / "landing"
    cfg = FileDropConfig(base_path=str(root), **job["config"])
    return _paths(FileDropSimulator(tables=tables, config=cfg).run(), root)


# ---- helpers ------------------------------------------------------------------------------


def _both(ctx: Any, name: str, **params: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    return ctx.run("baseline", NAME, name, **params), ctx.run("shape", NAME, name, **params)


def _root(result: dict[str, Any]) -> Path:
    return Path(result["out_dir"]) / "landing"


def _rows(root: Path, entity: str, only_seq: str | None = None) -> pd.DataFrame:
    """Every data row of ``entity`` under ``root``, files in path order."""
    parts = []
    for rel, path in trees.list_files(root).items():
        if f"/{entity}/" in f"/{rel}" and trees.is_data(rel) and (only_seq is None or only_seq in rel):
            parts.append(cmp.read_frame(path))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _in_range(ctx: Any, table: str, start: str, end: str) -> pd.DataFrame:
    df = pd.read_parquet(ctx.exact_dir / f"{table}.parquet")
    col = next(c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c].dtype))
    lo, hi = pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1)
    return df[(df[col] >= lo) & (df[col] < hi)]


# ---- mechanism parity ---------------------------------------------------------------------


MS = 1_000_000  # one millisecond in nanoseconds


def _explain(allowed: dict[str, int]) -> Any:
    """The two allowed differences of a data file. FD-2: a 00900 file of Shape holds the
    baseline's rows as its last rows, and more (a JSON Lines file may also differ by FD-5).
    FD-5: a JSON Lines file whose instants differ by less than a millisecond."""

    def explain(path: str, base: pd.DataFrame, shape: pd.DataFrame) -> bool:
        tol = MS if path.endswith(".jsonl") else 0
        if "_00900." in path and len(shape) > len(base):
            tail = shape.iloc[len(shape) - len(base) :]
            ok, _ = cmp.frames_equal(base, tail, dt_tol_ns=tol)
            if ok:
                allowed["lost_rows"] = allowed.get("lost_rows", 0) + len(shape) - len(base)
                allowed["ms_truncated"] = allowed.get("ms_truncated", 0) + int(tol > 0)
            return ok
        if tol:
            ok, _ = cmp.frames_equal(base, shape, dt_tol_ns=tol)
            if ok:
                allowed["ms_truncated"] = allowed.get("ms_truncated", 0) + 1
            return ok
        return False

    return explain


def mechanism(
    ctx: Any, checks: sc.Checks, name: str, spec: tuple[list[str], dict[str, Any]]
) -> dict[str, Any]:
    tables, config = spec
    params = {"input_dir": str(ctx.exact_dir), "tables": tables, "config": config}
    b, s = _both(ctx, name, **params)
    if "error" in b or "error" in s:
        checks.add(f"{name}: both ran", False, f"baseline {b.get('error')}; Shape {s.get('error')}")
        return {}
    seen: dict[str, int] = {}
    info = trees.compare_trees(
        checks,
        name,
        _root(b),
        _root(s),
        ignore_columns=("_restated_at",),
        explain=_explain(seen),
    )
    sb, ss = b["stats"], s["stats"]
    # FD-2: the baseline counts a replaced file twice in the stats; Shape counts each file once.
    overcount = len(b["listed"]) - len(set(b["listed"]))
    bad = []
    for entity in sb:
        if entity not in ss or sb[entity]["rows_written"] != ss[entity]["rows_written"] or sb[entity]["formats"] != ss[entity]["formats"]:
            bad.append(entity)
    checks.add(f"{name}: stats rows_written and formats equal", not bad and set(sb) == set(ss), f"{sorted(sb)}; differ {bad}")
    files_b = sum(v["files"] for v in sb.values()) - overcount
    files_s = sum(v["files"] for v in ss.values())
    checks.add(
        f"{name}: stats files equal (baseline minus {overcount} replaced listings)",
        files_b == files_s,
        f"{files_b} vs {files_s}",
    )
    checks.add(
        f"{name}: files listed once (Shape)",
        len(s["listed"]) == len(set(s["listed"])) == len(list(p for p in trees.list_files(_root(s)) if trees.is_data(p))),
        f"{len(s['listed'])} listed",
    )
    info = {**info, **seen, "overcount": overcount, "baseline": b, "shape": s}
    return info


# ---- probes -------------------------------------------------------------------------------


def probe_fd2(ctx: Any, checks: sc.Checks) -> None:
    """FD-2: the baseline loses late rows when two slots send rows to one partition."""
    tables, config = COLLISIONS
    info = mechanism(ctx, checks, "late_collisions", COLLISIONS)
    b = ctx.run("baseline", NAME, "late_collisions", input_dir=str(ctx.exact_dir), tables=tables, config=config)
    s = ctx.run("shape", NAME, "late_collisions", input_dir=str(ctx.exact_dir), tables=tables, config=config)
    lost = {}
    for table in tables:
        expected = len(_in_range(ctx, table, config["date_range_start"], config["date_range_end"]))
        got_b, got_s = len(_rows(_root(b), table)), len(_rows(_root(s), table))
        lost[table] = (expected, got_b, got_s)
    checks.add(
        "FD-2 probe: baseline loses late rows, Shape loses none",
        all(gs == e for e, gb, gs in lost.values()) and any(gb < e for e, gb, gs in lost.values()),
        f"(in range, baseline wrote, Shape wrote) per table: {lost}",
    )
    checks.add(
        "FD-2 probe: baseline lists a replaced file twice; the replaced rows are the differences found",
        info.get("overcount", 0) > 0 and info.get("lost_rows", 0) == sum(e - gb for e, gb, _ in lost.values()),
        f"listed twice {info.get('overcount')}; lost rows seen as 00900 differences {info.get('lost_rows')}",
    )


def probe_fd5(checks: sc.Checks, info: dict[str, Any]) -> None:
    """FD-5: baseline JSON Lines timestamps are milliseconds; Shape's equal its Parquet's."""
    if not info:
        checks.add("FD-5 probe: ran", False, "the hourly_formats job did not run")
        return
    b, s = _root(info["baseline"]), _root(info["shape"])

    def sibling(root: Path) -> tuple[int, int]:
        same = differ = 0
        for rel, path in trees.list_files(root).items():
            if not rel.endswith(".jsonl") or "/retail_customer_" not in "/" + rel:
                continue
            twin = cmp.read_frame(path.with_suffix(".parquet"))
            if len(twin) == 0:  # an on-time slice whose rows were all late: an empty file
                continue
            ok, _ = cmp.frames_equal(cmp.read_frame(path), twin)
            same, differ = same + ok, differ + (not ok)
        return same, differ

    sb, ss = sibling(b), sibling(s)
    checks.add(
        "FD-5 probe: baseline JSON Lines lose sub-millisecond precision, Shape's keep it",
        sb[1] > 0 and ss[1] == 0 and info.get("ms_truncated", 0) > 0,
        f"customer files equal to their Parquet twin (equal, different): baseline {sb}, Shape {ss}; "
        f"{info.get('ms_truncated', 0)} files explained",
    )


def probe_fd1(ctx: Any, checks: sc.Checks) -> None:
    """FD-1: multi-file drops. The baseline raises; Shape's files hold the same rows, split."""
    config = {**RANGE, "domain": "retail", "lateness_enabled": False, "multi_file_enabled": True, "multi_file_chunks": 4, "seed": 21}
    params = {"input_dir": str(ctx.exact_dir), "tables": ["order"], "config": config}
    b = ctx.run("baseline", NAME, "multi_file", **params)
    s = ctx.run("shape", NAME, "multi_file", **params)
    checks.add("FD-1 probe: baseline raises for multi_file_enabled", "error" in b and "empty" in b["error"], str(b.get("error")))
    if "error" in s:
        checks.add("FD-1 probe: Shape runs multi_file_enabled", False, s["error"])
        return
    single = ctx.run("shape", NAME, "multi_file_off", **{**params, "config": {**config, "multi_file_enabled": False}})
    root, flat = _root(s), _root(single)
    split_ok, details = True, []
    import hashlib
    import json

    for rel, path in trees.list_files(root).items():
        if not rel.endswith("_manifest.json"):
            continue
        doc = json.loads(path.read_text())
        files = sorted(path.parent.glob("*.parquet"))
        rows = [len(pd.read_parquet(f)) for f in files]
        whole = len(pd.read_parquet(next((flat / rel).parent.glob("*.parquet"))))
        want = 4 if whole > 4 else 1
        ok = len(files) == want and sum(rows) == whole and max(rows) - min(rows) <= 1
        if want == 4:
            by_name = {d["name"]: d for d in doc.get("file_details", [])}
            ok &= all(
                by_name.get(f.name, {}).get("sha256") == hashlib.sha256(f.read_bytes()).hexdigest()
                and by_name[f.name]["size_bytes"] == f.stat().st_size
                for f in files
            )
        if not ok:
            split_ok = False
            details.append(rel)
    checks.add("FD-1 probe: Shape splits each partition into near-equal files with checksums", split_ok, f"bad {details[:3]}")
    a = _rows(root, "order")
    c = _rows(flat, "order")
    ok, why = cmp.frames_equal(a, c, same_order=True)
    checks.add("FD-1 probe: the split files hold the single-file run's rows, in order", ok, why)


def probe_fd3(ctx: Any, checks: sc.Checks) -> None:
    inline = {"t": {"item_id": list(range(1, 201)), "updated_count": [i % 7 for i in range(200)], "label": [f"x{i}" for i in range(200)]}}
    config = {"domain": "retail", "date_range_start": "2022-01-01", "date_range_end": "2022-01-10", "lateness_enabled": False, "seed": 3}
    b, s = _both(ctx, "numeric_name_heuristic", inline=inline, config=config)
    nb = sum(1 for p in trees.list_files(_root(b)) if trees.is_data(p)) if "error" not in b else -1
    rows_s = len(_rows(_root(s), "t")) if "error" not in s else -1
    checks.add(
        "FD-3 probe: baseline writes no data for an integer 'updated_count' column, Shape deals the rows out",
        nb == 0 and rows_s == 200,
        f"baseline data files {nb} (error {b.get('error')}); Shape rows {rows_s} (error {s.get('error')})",
    )


def probe_fd4(ctx: Any, checks: sc.Checks) -> None:
    base = {"domain": "retail", "date_range_start": "2022-01-01", "date_range_end": "2022-01-05", "lateness_enabled": False, "seed": 3}
    inline = {"t": {"id": list(range(10)), "created_at": ["2022-01-02"] * 10}}
    b, s = _both(ctx, "cadence_weekly", inline=inline, config={**base, "cadence": "weekly"})
    manifest = next((p for p in trees.list_files(_root(b)) if p.endswith("_manifest.json")), None) if "error" not in b else None
    said = None
    if manifest:
        import json

        said = json.loads((_root(b) / manifest).read_text()).get("cadence")
    checks.add(
        "FD-4 probe: baseline accepts cadence 'weekly' (and says so in its manifest), Shape raises",
        "error" not in b and said == "weekly" and "error" in s and "cadence" in s["error"],
        f"baseline error {b.get('error')} manifest cadence {said}; Shape error {s.get('error')}",
    )
    rev = {**base, "date_range_start": "2022-01-05", "date_range_end": "2022-01-01"}
    b, s = _both(ctx, "reversed_range", inline=inline, config=rev)
    nb = sum(1 for p in trees.list_files(_root(b)) if trees.is_data(p)) if "error" not in b else -1
    checks.add(
        "FD-4 probe: baseline writes nothing for an end date before the start without an error, Shape raises",
        "error" not in b and nb == 0 and "error" in s and "before" in s["error"],
        f"baseline files {nb} error {b.get('error')}; Shape error {s.get('error')}",
    )


# ---- T-21 ---------------------------------------------------------------------------------


def t21(ctx: Any, checks: sc.Checks) -> None:
    def run_one(side: str, tool: str, seed: int) -> Path:
        r = ctx.run(side, NAME, f"t21_{side}_{seed}", input_dir=str(ctx.input_dir(tool, seed)), tables=T21_TABLES, config={**T21_CONFIG, "seed": seed})
        if "error" in r:
            raise RuntimeError(r["error"])
        return _root(r)

    ref = run_one("baseline", "baseline", ctx.ref_seed)
    spread = [run_one("baseline", "baseline", s) for s in ctx.seeds]
    shape = run_one("shape", "shape", ctx.shape_seed)
    for entity in T21_TABLES:
        fr, fs = _rows(ref, entity), _rows(shape, entity)
        fspread = [_rows(r, entity) for r in spread]
        result = cmp.t21_columns(fr, fspread, fs)
        bad = [c for c, v in result.items() if not v.get("equivalent", False)]
        checks.add(f"T-21 {entity}: (a)-(e) on {len(result) - 1} columns", not bad, f"not equivalent: {bad}")
        counts = len(fr), [len(f) for f in fspread], len(fs)
        ok, why = cmp.count_within(counts[2], counts[0], counts[1])
        checks.add(f"T-21 {entity}: rows written", ok, why)
    files = lambda r: len([p for p in trees.list_files(r) if trees.is_data(p)])  # noqa: E731
    ok, why = cmp.count_within(files(shape), files(ref), [files(r) for r in spread])
    checks.add("T-21: data files written", ok, why)


# ---- run ----------------------------------------------------------------------------------


def run(ctx: Any) -> sc.Checks:
    checks = sc.Checks(NAME)
    seen: dict[str, dict[str, Any]] = {}
    for name, spec in JOBS.items():
        seen[name] = mechanism(ctx, checks, name, spec)
    probe_fd5(checks, seen.get("hourly_formats", {}))
    probe_fd2(ctx, checks)
    probe_fd1(ctx, checks)
    probe_fd3(ctx, checks)
    probe_fd4(ctx, checks)
    t21(ctx, checks)
    return checks


# ---- negative controls --------------------------------------------------------------------


def negative_controls(ctx: Any) -> sc.Checks:
    """Tamper with Shape's tree and require the comparison to fail each time."""
    checks = sc.Checks(NAME + " negative control")
    tables, config = JOBS["daily_all"]
    params = {"input_dir": str(ctx.exact_dir), "tables": tables, "config": config}
    b = ctx.run("baseline", NAME, "neg_base", **params)
    s = ctx.run("shape", NAME, "neg_shape", **params)

    def caught(label: str, tamper: Any) -> None:
        work = sc.work_dir(NAME, "neg_tampered")
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(_root(s), work)
        tamper(work)
        probe = sc.Checks("probe")
        trees.compare_trees(probe, "tampered", _root(b), work, ignore_columns=("_restated_at",))
        checks.add(f"caught: {label}", not probe.ok, "; ".join(c.name for c in probe.items if not c.ok))

    def parquets(root: Path, entity: str) -> list[Path]:
        return sorted(p for p in root.rglob("*.parquet") if f"/{entity}/" in p.as_posix())

    def change_value(root: Path) -> None:
        p = next(f for f in parquets(root, "order") if len(pd.read_parquet(f)) > 0)
        df = pd.read_parquet(p)
        df.loc[df.index[0], "order_total"] = float(df["order_total"].iloc[0]) + 1.0
        df.to_parquet(p)

    def drop_file(root: Path) -> None:
        parquets(root, "customer")[0].unlink()

    def rename_column(root: Path) -> None:
        p = parquets(root, "order")[0]
        pd.read_parquet(p).rename(columns={"status": "state"}).to_parquet(p)

    def corrupt_manifest(root: Path) -> None:
        import json

        p = next(root.rglob("_manifest.json"))
        doc = json.loads(p.read_text())
        doc["file_count"] += 1
        p.write_text(json.dumps(doc))

    def remove_flag(root: Path) -> None:
        next(root.rglob("_done")).unlink()

    def drop_row(root: Path) -> None:
        p = next(f for f in parquets(root, "order") if len(pd.read_parquet(f)) > 1)
        pd.read_parquet(p).iloc[1:].to_parquet(p)

    caught("one changed value", change_value)
    caught("a missing data file", drop_file)
    caught("a renamed column", rename_column)
    caught("a manifest file_count off by one", corrupt_manifest)
    caught("a missing _done flag", remove_flag)
    caught("a missing row", drop_row)

    # T-21: a shifted date column, a changed category share and a raised null rate must fail (b)-(e).
    ref = _rows(_root(ctx.run("baseline", NAME, "neg_t21_ref", input_dir=str(ctx.input_dir("baseline", ctx.ref_seed)), tables=T21_TABLES, config={**T21_CONFIG, "seed": ctx.ref_seed})), "order")
    spread = [
        _rows(_root(ctx.run("baseline", NAME, f"neg_t21_{sd}", input_dir=str(ctx.input_dir("baseline", sd)), tables=T21_TABLES, config={**T21_CONFIG, "seed": sd})), "order")
        for sd in ctx.seeds
    ]
    shape = _rows(_root(ctx.run("shape", NAME, "neg_t21_shape", input_dir=str(ctx.input_dir("shape", ctx.shape_seed)), tables=T21_TABLES, config={**T21_CONFIG, "seed": ctx.shape_seed})), "order")
    clean = cmp.t21_columns(ref, spread, shape)
    checks.add("control: untouched Shape output passes T-21", all(v.get("equivalent") for v in clean.values()), str([c for c, v in clean.items() if not v.get("equivalent")]))
    moved = shape.copy()
    moved["order_date"] = pd.to_datetime(moved["order_date"]) + pd.Timedelta(days=365)
    r = cmp.t21_columns(ref, spread, moved)
    checks.add("caught: every order_date a year later", not r["order_date"]["equivalent"], "KS")
    cat = shape.copy()
    cat["status"] = cat["status"].where(cat.index % 3 != 0, "unseen")
    r = cmp.t21_columns(ref, spread, cat)
    checks.add("caught: a third of status values replaced", not r["status"]["equivalent"], "TVD / vocabulary")
    nulls = shape.copy()
    nulls.loc[nulls.index % 4 == 0, "store_id"] = None
    r = cmp.t21_columns(ref, spread, nulls)
    checks.add("caught: a quarter of store_id nulled", not r["store_id"]["equivalent"], "null rate")
    return checks
