"""The nightly parity suite (P8-02): ``run.py --full`` over every domain and every profiling
dataset, spread over several jobs, then merged, judged and recorded.

    source scripts/env.sh && python benchmarks/vs_refengine/nightly.py shards
    source scripts/env.sh && python benchmarks/vs_refengine/nightly.py run --shard profile
    source scripts/env.sh && python benchmarks/vs_refengine/nightly.py merge --in-dir D --out R \\
        --history H [--event schedule] [--run-id ID]
    python benchmarks/vs_refengine/nightly.py streak --history H --need 7

``run`` runs one shard of ``run.py --full`` (``SHARDS``) and writes
``<out-dir>/<shard>/results.json`` and ``<out-dir>/<shard>/status.json`` (the exit code).
A hosted job may run for at most six hours, so the full suite does not fit in one job; each
workload's ratio is still measured inside one job (T-19). Extra ``run.py`` options after
``--`` select a subset (a local dry run); a subset run is recorded but never counts as a
nightly run.

``merge`` combines the shards into one ``results.json`` (same schema, plus a ``nightly`` block)
and decides whether the night is green. A night is green only when:

* every shard in ``SHARDS`` ran, exited 0 and wrote its results;
* every shard measured the same Shape commit (with a clean tree) and the same baseline commit;
* the shards together recorded every workload of the full suite: both implementations on every
  profiling dataset (``run.FULL_PROFILE_DATASETS``), ``shape`` on every baseline domain at medium
  and on retail at large, ``reference_port`` on retail medium and large, and the stream workload;
* every one of those workloads has a verifier that exited 0 (and, where the timed output was
  verified again, that second run exited 0 too) and a recorded median.

The verdict is appended to a JSON-lines history. ``streak`` counts the latest run of consecutive
green scheduled nights (one per calendar day, no day missing; a red night or a missing day ends
it) and exits 0 when it reaches ``--need`` (the P8-02 acceptance is 7).

Standard library only; the shard runner needs the section 1 environment like ``run.py``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RUN_PY = HERE / "run.py"
GENERATE_SHARDS = 3

# name -> run.py options (always with --full). The nightly workflow's matrix lists these names.
SHARDS: dict[str, list[str]] = {
    "profile": ["--only", "profile"],
    "stream": ["--only", "stream"],
    **{
        f"generate-{k}": ["--only", "generate", "--shard", f"{k}/{GENERATE_SHARDS}"]
        for k in range(1, GENERATE_SHARDS + 1)
    },
}
IMPLS = ("reference_port", "shape")


def _load_run() -> Any:
    spec = importlib.util.spec_from_file_location("vs_refengine_run_for_nightly", RUN_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _utc_now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────────────────────────────────────────────────────────────────────
# run one shard
# ─────────────────────────────────────────────────────────────────────────────


def run_shard(shard: str, out_dir: Path, extra: list[str]) -> int:
    """Run one shard of ``run.py --full``; write its results and status. Returns run.py's
    exit code."""
    d = out_dir / shard
    d.mkdir(parents=True, exist_ok=True)
    results = d / "results.json"
    results.unlink(missing_ok=True)
    cmd = [sys.executable, str(RUN_PY), "--full", *SHARDS[shard], *extra, "--out", str(results)]
    started = _utc_now()
    print("+", " ".join(cmd), flush=True)
    rc = subprocess.run(cmd).returncode
    status = {
        "shard": shard,
        "exit_code": rc,
        "run_py_args": [*SHARDS[shard], *extra],
        "subset": bool(extra),
        "started_utc": started,
        "finished_utc": _utc_now(),
    }
    (d / "status.json").write_text(json.dumps(status, indent=1) + "\n")
    print(f"shard {shard}: run.py exit {rc}; wrote {d}")
    return rc


# ─────────────────────────────────────────────────────────────────────────────
# merge and judge
# ─────────────────────────────────────────────────────────────────────────────


def verifier_passed(rec: dict[str, Any]) -> bool:
    """True when the record's verifier exited 0 and, where the timed output was verified again,
    the first run exited 0 too."""
    ver = rec.get("verifier")
    if not isinstance(ver, dict):
        return False
    if ver.get("status") != "pass" or ver.get("exit_code") != 0:
        return False
    first = ver.get("first_run")
    if first is not None and (first.get("status") != "pass" or first.get("exit_code") != 0):
        return False
    return True


def required_ids(run: Any, baseline_domains: list[str]) -> dict[str, set[str]]:
    """Every workload id, per implementation, that a green night must have recorded."""
    gen = run.generate_plan(True, baseline_domains)
    exp: dict[str, list[str]] = run.expected_ids(
        run.FULL_PROFILE_DATASETS, gen, run.FULL_STREAM_SCALES, "all"
    )
    return {impl: set(ids) for impl, ids in exp.items()}


def merge(
    in_dir: Path, shards: list[str] | None = None, run: Any = None
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Merge the shard results under ``in_dir``. Returns (merged results or None when no shard
    wrote results, nightly verdict)."""
    run = run if run is not None else _load_run()
    names = list(SHARDS) if shards is None else shards
    reasons: list[str] = []
    shard_info: dict[str, Any] = {}
    parts: dict[str, dict[str, Any]] = {}
    subset = shards is not None and set(shards) != set(SHARDS)
    for name in names:
        d = in_dir / name
        info: dict[str, Any] = {"exit_code": None, "results": False}
        st_file, res_file = d / "status.json", d / "results.json"
        if st_file.is_file():
            st = json.loads(st_file.read_text())
            info["exit_code"] = st.get("exit_code")
            info["run_py_args"] = st.get("run_py_args")
            subset = subset or bool(st.get("subset"))
        else:
            reasons.append(f"shard {name}: no status (the job did not finish)")
        if info["exit_code"] not in (None, 0):
            reasons.append(f"shard {name}: run.py exited {info['exit_code']}")
        if res_file.is_file():
            parts[name] = json.loads(res_file.read_text())
            info["results"] = True
            info["machine"] = parts[name].get("machine")
            info["selection"] = parts[name].get("selection")
        else:
            reasons.append(f"shard {name}: no results.json")
        shard_info[name] = info

    merged: dict[str, Any] | None = None
    if parts:
        first = next(iter(parts.values()))
        merged = {
            "schema_version": first["schema_version"],
            "mode": first["mode"],
            "runs": first["runs"],
            "generated_utc": max(p["generated_utc"] for p in parts.values()),
            "machine": {**first["machine"], "shards": {n: p["machine"] for n, p in parts.items()}},
            "refengine_commit": first["refengine_commit"],
            "shape_commit": first["shape_commit"],
            "shape_tree_dirty": any(p["shape_tree_dirty"] for p in parts.values()),
            "refengine": {"workloads": {}},
            "reference_port": {"workloads": {}},
            "shape": {"workloads": {}},
        }
        for key in ("refengine_commit", "shape_commit", "mode", "runs"):
            seen = sorted({str(p[key]) for p in parts.values()})
            if len(seen) > 1:
                reasons.append(f"shards disagree on {key}: {seen}")
        if merged["mode"] != "full":
            reasons.append(f"mode is {merged['mode']!r}, not 'full'")
        if merged["shape_tree_dirty"]:
            reasons.append("measured from a modified Shape tree")
        for name, p in parts.items():
            for tool in ("refengine", *IMPLS):
                for wid, rec in ((p.get(tool) or {}).get("workloads") or {}).items():
                    dest = merged[tool]["workloads"]
                    if tool == "refengine":
                        if wid not in dest or dest[wid]["median_s"] is None:
                            dest[wid] = rec
                    elif wid in dest:
                        reasons.append(f"{tool} {wid} recorded by two shards (also {name})")
                    else:
                        dest[wid] = rec
        kmb = [p["kernel_microbench"] for p in parts.values() if "kernel_microbench" in p]
        if kmb:
            merged["kernel_microbench"] = kmb[0]

    # coverage and verifiers: judged against the full suite, not the shards' own lists
    domains: set[str] = set()
    for p in parts.values():
        domains |= set((p.get("selection") or {}).get("baseline_domains") or [])
    if not domains:
        reasons.append("no shard recorded the baseline domain list")
    need = required_ids(run, sorted(domains))
    missing: list[str] = []
    failed: list[str] = []
    passed = 0
    for impl in IMPLS:
        recs = (merged or {}).get(impl, {}).get("workloads", {})
        for wid in sorted(need[impl]):
            rec = recs.get(wid)
            if rec is None:
                missing.append(f"{impl} {wid}")
            elif not verifier_passed(rec) or rec.get("median_s") is None:
                status = (rec.get("verifier") or {}).get("status")
                failed.append(f"{impl} {wid} (verifier {status})")
            else:
                passed += 1
    if missing:
        reasons.append(f"{len(missing)} workload(s) not recorded")
    if failed:
        reasons.append(f"{len(failed)} workload(s) without a passing verifier")
    if subset:
        reasons.append("a subset run (not the full suite)")

    verdict: dict[str, Any] = {
        "green": not reasons,
        "full_suite": not subset,
        "reasons": reasons,
        "required": sum(len(v) for v in need.values()),
        "passed": passed,
        "missing": missing,
        "failed": failed,
        "baseline_domains": sorted(domains),
        "shards": shard_info,
        "shape_commit": merged["shape_commit"] if merged else None,
    }
    if merged is not None:
        merged["nightly"] = verdict
        errs = run.validate(merged, json.loads(run.SCHEMA_FILE.read_text()))
        if errs:
            verdict["green"] = False
            reasons.append("merged results do not match results.schema.json: " + "; ".join(errs))
    return merged, verdict


# ─────────────────────────────────────────────────────────────────────────────
# history and streak
# ─────────────────────────────────────────────────────────────────────────────


def history_record(
    verdict: dict[str, Any], date: str, event: str, run_id: str | None
) -> dict[str, Any]:
    return {
        "date": date,
        "event": event,
        "run_id": run_id,
        "green": verdict["green"],
        "full_suite": verdict["full_suite"],
        "shape_commit": verdict["shape_commit"],
        "required": verdict["required"],
        "passed": verdict["passed"],
        "reasons": verdict["reasons"],
    }


def read_history(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def append_history(path: Path, rec: dict[str, Any]) -> None:
    """Append ``rec``; a record with the same run id replaces the earlier one (a re-run)."""
    recs = [r for r in read_history(path) if rec["run_id"] is None or r["run_id"] != rec["run_id"]]
    recs.append(rec)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))


def streak(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The latest run of consecutive green scheduled nights of the full suite, newest first.
    Only scheduled full-suite runs count; one per calendar day (the last record of a day wins);
    a red night or a missing day ends the run."""
    by_day: dict[dt.date, dict[str, Any]] = {}
    for r in history:
        if r.get("event") != "schedule" or not r.get("full_suite"):
            continue
        by_day[dt.date.fromisoformat(r["date"])] = r
    out: list[dict[str, Any]] = []
    prev: dt.date | None = None
    for day in sorted(by_day, reverse=True):
        r = by_day[day]
        if not r["green"] or (prev is not None and day != prev - dt.timedelta(days=1)):
            break
        out.append(r)
        prev = day
    return out


# ─────────────────────────────────────────────────────────────────────────────
# command line
# ─────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("shards", help="print the shard names and their run.py options (JSON)")
    r = sub.add_parser("run", help="run one shard of run.py --full")
    r.add_argument("--shard", choices=sorted(SHARDS), required=True)
    r.add_argument("--out-dir", type=Path, default=None, help="default: $BENCH_OUT_DIR/nightly")
    r.add_argument("extra", nargs="*", help="after --: run.py options selecting a subset")
    m = sub.add_parser("merge", help="merge the shards, judge the night, append to the history")
    m.add_argument("--in-dir", type=Path, required=True)
    m.add_argument("--out", type=Path, required=True, help="merged results.json")
    m.add_argument("--verdict", type=Path, default=None, help="default: <out dir>/nightly.json")
    m.add_argument("--history", type=Path, default=None, help="JSON-lines history to append to")
    m.add_argument("--shards", default=None, help="comma list (a subset run; never green)")
    m.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "manual"))
    m.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    m.add_argument("--date", default=None, help="UTC date of the night (default: today)")
    s = sub.add_parser("streak", help="exit 0 when the latest green streak reaches --need")
    s.add_argument("--history", type=Path, required=True)
    s.add_argument("--need", type=int, default=7)
    a = ap.parse_args(argv)

    if a.cmd == "shards":
        print(json.dumps(SHARDS, indent=1))
        return 0
    if a.cmd == "run":
        if a.out_dir is None:
            sys.path.insert(0, str(HERE))
            from paths import BENCH_OUT_DIR

            a.out_dir = BENCH_OUT_DIR / "nightly"
        return run_shard(a.shard, a.out_dir, a.extra)
    if a.cmd == "merge":
        shards = None if a.shards is None else [x for x in a.shards.split(",") if x]
        unknown = sorted(set(shards or []) - set(SHARDS))
        if unknown:
            ap.error(f"unknown shards {unknown}; known: {sorted(SHARDS)}")
        merged, verdict = merge(a.in_dir, shards)
        if merged is not None:
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_text(json.dumps(merged, indent=1) + "\n")
            print(f"wrote {a.out}")
        vpath = a.verdict or a.out.parent / "nightly.json"
        vpath.parent.mkdir(parents=True, exist_ok=True)
        vpath.write_text(json.dumps(verdict, indent=1) + "\n")
        date = a.date or dt.datetime.now(dt.UTC).date().isoformat()
        if a.history is not None:
            append_history(a.history, history_record(verdict, date, a.event, a.run_id))
            run_now = streak(read_history(a.history))
            print(f"green streak: {len(run_now)} night(s)")
        print(
            f"night {date}: {'GREEN' if verdict['green'] else 'RED'} "
            f"({verdict['passed']}/{verdict['required']} workloads verified)"
        )
        for reason in verdict["reasons"]:
            print(f"  - {reason}")
        for line in verdict["missing"] + verdict["failed"]:
            print(f"    {line}")
        return 0 if verdict["green"] else 1
    run_now = streak(read_history(a.history))
    days = ", ".join(f"{r['date']} ({(r['shape_commit'] or '?')[:7]})" for r in run_now)
    print(f"green streak: {len(run_now)} of {a.need} night(s) needed: {days or '-'}")
    return 0 if len(run_now) >= a.need else 1


if __name__ == "__main__":
    raise SystemExit(main())
