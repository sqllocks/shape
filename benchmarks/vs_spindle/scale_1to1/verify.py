"""Scale-mode equivalence (P6-13, T-21): ``shape generate --scale-mode local_single|local_mp``
against the pinned baseline's retail at medium. Runs in the *baseline* venv (needs pandas, scipy
and the baseline importable); Shape is driven through its own command line in its own venv.

    source scripts/env.sh && "$SPINDLE_PY" \\
        benchmarks/vs_spindle/scale_1to1/verify.py [--negative-control] [--scale medium]

For each mode the harness

1. runs ``shape generate retail --scale S --seed 1042 --scale-mode MODE --sink parquet -o DIR``
   (the product command, in a fresh process) and reads the part files back, one table per folder,
   into the layout ``domain_1to1/verify.py`` reads (``<table>.parquet`` and ``_SUCCESS``, tables in
   the plan's generation order);
2. checks that each table has exactly the rows the scale preset gives, and that no part file is
   larger than the chunk size;
3. runs ``domain_1to1/verify.py --impl shape`` on that output *as it is* (the comparison is the
   one that gates domains, not a copy: T-21 (a)-(h) against baseline seed 42, with seeds 43-46 as
   its own spread; Shape's seed is 1042). Exit 0 only when it exits 0.

``--negative-control`` proves the check can fail. Each case is run through the same comparison and
must exit 1: a numeric column scaled by 1.5, a categorical column overwritten, foreign keys
broken, rows dropped from a table, and the baseline's *own* ``local_mp`` output (its router sizes
the chunks by the sum of all tables' rows, so it writes the wrong number of rows: order 786,160
where the preset says 500,000). Exit codes: 0 every check holds (every control was flagged), 1 a
check failed (a control was not flagged), 2 a run could not be produced.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import BENCH_OUT_DIR, SHAPE_PY, SPINDLE_PY, SPINDLE_ROOT  # noqa: E402

DOMAIN = "retail"
IMPL_SEED = 1042
MODES = ("local_single", "local_mp")
CHUNK = 500_000
DOMAIN_VERIFY = HERE.parent / "domain_1to1" / "verify.py"
OUT = BENCH_OUT_DIR / "scale_1to1"


def shape_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    code = "import sys; from shape.cli.main import main; sys.exit(main(sys.argv[1:]))"
    return subprocess.run(
        [str(SHAPE_PY), "-c", code, *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def generation_order(scale: str) -> list[str]:
    r = shape_cli("generate", DOMAIN, "--scale", scale, "--dry-run", "--json")
    if r.returncode != 0:
        raise RuntimeError(f"shape generate --dry-run failed: {r.stderr[-800:]}")
    return [t for level in json.loads(r.stdout)["levels"] for t in level]


def read_parts(folder: Path) -> tuple[list[Path], int]:
    """The part files of one table folder, in order, and the largest part's rows."""
    import pyarrow.parquet as pq

    parts = sorted(folder.glob("part-*.parquet"))
    return parts, max((pq.ParquetFile(p).metadata.num_rows for p in parts), default=0)


def flatten(parts_dir: Path, run_dir: Path, order: list[str], mode: str, scale: str) -> dict:
    """Part files to the layout the domain verifier reads. Returns the facts checked later."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    rows: dict[str, int] = {}
    largest = 0
    part_counts: dict[str, int] = {}
    for table in order:
        parts, biggest = read_parts(parts_dir / table)
        if not parts:
            raise RuntimeError(f"no part files for {table} in {parts_dir}")
        largest = max(largest, biggest)
        tbl = pa.concat_tables([pq.read_table(p) for p in parts])
        pq.write_table(tbl, run_dir / f"{table}.parquet", compression="snappy")
        rows[table] = tbl.num_rows
        part_counts[table] = len(parts)
    (run_dir / "_SUCCESS").write_text(
        json.dumps(
            {"impl": "shape", "domain": DOMAIN, "scale": scale, "seed": IMPL_SEED, "rows": rows}
        )
    )
    return {"rows": rows, "largest_part": largest, "parts": part_counts, "mode": mode}


def run_mode(mode: str, scale: str, root: Path) -> dict:
    """The command, the flattened output, and the facts about it."""
    parts = root / "parts"
    shutil.rmtree(parts, ignore_errors=True)
    env = {"SHAPE_JOBS_DIR": str(root / "jobs")}
    t0 = time.time()
    r = shape_cli(
        "generate", DOMAIN, "--scale", scale, "--seed", str(IMPL_SEED),
        "--scale-mode", mode, "--chunk-size", str(CHUNK), "--sink", "parquet", "-o", str(parts),
        "--json", env=env,
    )  # fmt: skip
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-800:] + r.stderr[-1500:])
        raise RuntimeError(f"shape generate --scale-mode {mode} exited {r.returncode}")
    result = json.loads(r.stdout)
    facts = flatten(parts, out_dir(root), generation_order(scale), mode, scale)
    facts["command_seconds"] = round(time.time() - t0, 2)
    facts["cli_rows"] = result["tables"]
    return facts


def out_dir(root: Path) -> Path:
    return root / "shape" / DOMAIN / SCALE_FOR_RUN[0] / f"seed{IMPL_SEED}"


SCALE_FOR_RUN = ["medium"]


def preset_rows(scale: str) -> dict[str, int]:
    r = shape_cli("presets", DOMAIN, "--json")
    return {t: int(n) for t, n in json.loads(r.stdout)[scale].items()}


def link_baseline(root: Path) -> None:
    """The baseline runs are shared: ``<root>/spindle`` points at ``$BENCH_OUT_DIR/spindle``."""
    shared = BENCH_OUT_DIR / "spindle"
    shared.mkdir(parents=True, exist_ok=True)
    link = root / "spindle"
    if not link.exists():
        root.mkdir(parents=True, exist_ok=True)
        link.symlink_to(shared, target_is_directory=True)


def compare(root: Path, scale: str, label: str) -> tuple[int, Path]:
    """``domain_1to1/verify.py --impl shape`` on ``root``'s run; returns (exit code, report)."""
    link_baseline(root)
    report = root / f"{label}_report.json"
    env = {**os.environ, "BENCH_OUT_DIR": str(root)}
    cmd = [
        str(SPINDLE_PY), str(DOMAIN_VERIFY), "--domain", DOMAIN, "--scale", scale,
        "--impl", "shape", "--out", str(report),
    ]  # fmt: skip
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    (root / f"{label}.log").write_text(r.stdout + "\n" + r.stderr)
    return r.returncode, report


# ─── negative controls ─────────────────────────────────────────────────────────────────────────


def _tamper(run_dir: Path, how: str) -> None:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if how == "numeric":
        p = run_dir / "order_line.parquet"
        t = pq.read_table(p)
        col = "unit_price" if "unit_price" in t.column_names else next(
            n
            for n, ty in zip(t.column_names, t.schema.types, strict=True)
            if pa.types.is_floating(ty)
        )  # fmt: skip
        t = t.set_column(t.column_names.index(col), col, pc.multiply(t[col], 1.5))
        pq.write_table(t, p)
    elif how == "categorical":
        p = run_dir / "order.parquet"
        t = pq.read_table(p)
        col = "status" if "status" in t.column_names else next(
            n
            for n, ty in zip(t.column_names, t.schema.types, strict=True)
            if pa.types.is_string(ty)
        )  # fmt: skip
        t = t.set_column(t.column_names.index(col), col, pa.array(["X"] * t.num_rows, t[col].type))
        pq.write_table(t, p)
    elif how == "fk":
        p = run_dir / "order.parquet"
        t = pq.read_table(p)
        t = t.set_column(
            t.column_names.index("customer_id"),
            "customer_id",
            pc.add(t["customer_id"], 10_000_000),
        )
        pq.write_table(t, p)
    elif how == "rows":
        p = run_dir / "customer.parquet"
        t = pq.read_table(p)
        pq.write_table(t.slice(0, t.num_rows - 5), p)
    else:
        raise ValueError(how)


def negative_controls(good: Path, scale: str) -> list[tuple[str, bool]]:
    """Each tampered copy of a good run must be flagged (exit 1) by the comparison."""
    outcomes = []
    for how in ("numeric", "categorical", "fk", "rows"):
        root = OUT / "neg" / how
        shutil.rmtree(root / "shape", ignore_errors=True)
        shutil.copytree(good / "shape", root / "shape")
        _tamper(out_dir(root), how)
        code, _ = compare(root, scale, f"neg_{how}")
        print(
            f"  negative control {how}: comparison exit {code} "
            f"({'flagged' if code == 1 else 'MISSED'})"
        )
        outcomes.append((how, code == 1))
    outcomes.append(("baseline_local_mp", baseline_mp_control(scale)))
    return outcomes


def baseline_mp_control(scale: str) -> bool:
    """The baseline's own ``local_mp`` output, read as if it were Shape's, must be flagged."""
    root = OUT / "neg" / "baseline_local_mp"
    parts = root / "parts"
    shutil.rmtree(parts, ignore_errors=True)
    worker = HERE / "baseline_mp_worker.py"
    r = subprocess.run(
        [
            str(SPINDLE_PY), str(worker), "--scale", scale,
            "--seed", str(IMPL_SEED), "--out", str(parts),
        ],  # fmt: skip
        capture_output=True, text=True,
    )  # fmt: skip
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-1500:])
        raise RuntimeError("the baseline's local_mp could not be run")
    produced = flatten(parts, out_dir(root), generation_order(scale), "baseline_local_mp", scale)
    want = preset_rows(scale)
    wrong = {t: (want[t], n) for t, n in produced["rows"].items() if want[t] != n}
    code, _ = compare(root, scale, "neg_baseline_local_mp")
    print(
        f"  negative control baseline_local_mp: rows differing from the preset {wrong}; exit {code}"
    )
    return code == 1 and bool(wrong)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scale", default="medium")
    ap.add_argument("--mode", choices=MODES, action="append", help="default: both")
    ap.add_argument("--negative-control", action="store_true")
    a = ap.parse_args(argv)
    SCALE_FOR_RUN[0] = a.scale
    modes = a.mode or list(MODES)
    if not SPINDLE_ROOT.exists():
        print(f"ERROR: {SPINDLE_ROOT} is missing: run setup_spindle.sh", file=sys.stderr)
        return 2
    want = preset_rows(a.scale)
    failed: list[str] = []
    good_root: Path | None = None
    for mode in modes:
        root = OUT / mode
        print(f"[{mode}] shape generate --scale-mode {mode} ...", flush=True)
        try:
            facts = run_mode(mode, a.scale, root)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        bad = {t: (want[t], n) for t, n in facts["rows"].items() if want[t] != n}
        if bad:
            failed.append(f"{mode}: rows differ from the preset (want, got): {bad}")
        if facts["largest_part"] > CHUNK:
            failed.append(f"{mode}: a part file has {facts['largest_part']} rows (> {CHUNK})")
        print(f"[{mode}] {sum(facts['rows'].values()):,} rows in {facts['command_seconds']}s; "
              f"parts per table {facts['parts']}")  # fmt: skip
        code, report = compare(root, a.scale, "t21")
        print(f"[{mode}] domain_1to1/verify.py --impl shape: exit {code} ({report})")
        if code == 2:
            print(f"ERROR: the comparison could not run; see {root / 't21.log'}", file=sys.stderr)
            return 2
        if code != 0:
            failed.append(f"{mode}: T-21 comparison exit {code}")
        (root / "facts.json").write_text(json.dumps(facts, indent=1))
        good_root = good_root or root
    if a.negative_control and good_root is not None:
        print("negative controls (each must be flagged):", flush=True)
        for name, flagged in negative_controls(good_root, a.scale):
            if not flagged:
                failed.append(f"negative control {name} was not flagged")
    if failed:
        print("FAIL:\n  " + "\n  ".join(failed))
        return 1
    print(
        "PASS: " + ", ".join(modes) + (" (negative controls flagged)" if a.negative_control else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
