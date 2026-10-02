"""STREAM-EMIT equivalence verifier: ``shape stream`` against the baseline's stream command.

Runs in the *baseline* venv (needs pandas and scipy):

    source scripts/env.sh && "$SPINDLE_PY" benchmarks/vs_spindle/stream_1to1/verify.py \\
        [--scale small|medium] [--no-generate] [--negative-control]

The workload is the plan's STREAM-EMIT row (3.4): one table of the retail domain, ``--no-realtime``,
``--sink file``, the baseline at seeds 42-46 (42 is the reference, 43-46 its own spread) and Shape
at seed 1042 (T-21; the seed set is fixed, there is no option, and a verdict from another set
counts for nothing). Each is run twice: for the whole table, and with ``--max-events N`` (the first
N events in event-time order). Outputs are the files the real commands write
(``stream_common.out_file``); ``--no-generate`` reads the ones already there (bench.py's timed
output) and exits 2 when one is missing.

What is checked, for the whole table and for the prefix:

* **Names and order.** The baseline's event field names, mapped to Shape's (``FIELD_MAP``, D-13),
  equal Shape's field names, in the same order, in every event of both files.
* **Events.** The same number (the table's rows, or N). In each file: ``seq`` unique and in range
  (all of ``0..rows-1`` for the whole table), the table field constant, the event-time field equal
  to the first datetime column of its event, events in non-decreasing event-time order, and every
  field of one JSON type class.
* **The event multiset, T-21 (b)-(e), per field** (``domain_1to1/verify.py``'s ``compare_column``,
  with the baseline's own seed-to-seed spread from seeds 43-46): null rate within
  max(5 sigma, 1.5 x the spread); numeric and datetime fields KS <= max(critical value at
  alpha=0.001, 1.5 x the spread + 0.002); low-cardinality fields TVD <= max(3 x multinomial noise,
  1.5 x the spread + 0.002) with vocabulary overlap >= 0.999; high-cardinality strings vocabulary
  overlap >= 0.999 and a distinct-count ratio within the spread. T-21 (a), (f) and (g) concern
  generated tables and their foreign keys and are covered by ``domain_1to1/verify.py``; the table
  here is the same rows.
* **Allow-list** (``stream_common.ALLOWED``): baseline defects Shape fixes, each shown by a probe
  (the baseline ignores ``--anomaly-fraction``; Shape honours it, deterministically). Anything
  else that differs fails.

``--negative-control`` perturbs Shape's events in memory (a scaled amount, a renamed category, a
raised null rate, shuffled events, reordered fields) and requires the verifier to fail each time;
it exits 1 if any perturbation goes undetected.

Exit codes: 0 every check passed, 1 a check failed, 2 inputs missing or a command failed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import stream_common as sc  # noqa: E402
from paths import BENCH_OUT_DIR, SHAPE_VENV, SPINDLE_VENV  # noqa: E402

assert sc.BASELINE_SEEDS == (43, 44, 45, 46) and sc.REF_SEED == 42 and sc.SHAPE_SEED == 1042

_spec = importlib.util.spec_from_file_location(
    "domain_verify", HERE.parent / "domain_1to1" / "verify.py"
)
assert _spec and _spec.loader
dv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dv)

DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?Z?$")
REVERSE_MAP = {v: k for k, v in sc.FIELD_MAP.items()}


class MissingInput(Exception):
    """An output file is missing, or a command did not run."""


# ─────────────────────────────────────────────────────────────────────────────
# running the commands
# ─────────────────────────────────────────────────────────────────────────────


def command(tool: str, scale: str, seed: int, out: Path, max_events: int | None, *extra: str):
    exe = (
        (SPINDLE_VENV if tool == "spindle" else SHAPE_VENV)
        / "bin"
        / ("spindle" if tool == "spindle" else "shape")
    )
    cmd = [str(exe), "stream", sc.DOMAIN, "--table", sc.TABLE, "--scale", scale]
    cmd += ["--no-realtime", "--sink", "file", "-o", str(out), "--seed", str(seed)]
    if max_events is not None:
        cmd += ["--max-events", str(max_events)]
    return [*cmd, *extra]


def run_command(cmd: list[str], out: Path) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    Path(f"{out}.checkpoint").unlink(missing_ok=True)
    print("+", " ".join(cmd).replace(str(BENCH_OUT_DIR), "$BENCH_OUT_DIR"), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise MissingInput(f"{' '.join(cmd)} exited {r.returncode}:\n{r.stderr[-1500:]}")
    return r.stdout + r.stderr


def ensure(tool: str, scale: str, seed: int, max_events: int | None, generate: bool) -> Path:
    out = sc.out_file(tool, scale, seed, max_events)
    marker = Path(f"{out}.verified-run")
    if generate and not (out.exists() and marker.exists()):
        run_command(command(tool, scale, seed, out, max_events), out)
        marker.write_text("ok\n")
    if not out.exists():
        raise MissingInput(f"{out} is missing (run without --no-generate)")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# loading
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Events:
    """One output file, in Shape's field names."""

    tool: str
    path: str
    names: list[str]  # the field names as written (baseline names mapped to Shape's)
    inconsistent_order: int  # events whose key order differs from the first event's
    types: dict[str, list[str]]  # per field, the JSON type classes seen (null aside)
    df: pd.DataFrame
    raw_time: pd.Series  # the event-time field as written
    extra: dict[str, Any] = field(default_factory=dict)


def _json_class(v: Any) -> str:
    return type(v).__name__


def load(tool: str, path: Path) -> Events:
    cols: dict[str, list[Any]] = {}
    first: tuple[str, ...] | None = None
    bad = 0
    types: dict[str, set[str]] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            ev = json.loads(line)
            keys = tuple(ev)
            if first is None:
                first = keys
                for k in keys:
                    cols[k] = []
                    types[k] = set()
            elif keys != first:
                bad += 1
            for k, v in ev.items():
                cols[k].append(v)
    if first is None:
        raise MissingInput(f"{path} holds no events")
    for k, vals in cols.items():
        types[k] = {_json_class(v) for v in vals if v is not None}
    mapped = {k: sc.FIELD_MAP.get(k, k) if tool == "spindle" else k for k in cols}
    df = pd.DataFrame({mapped[k]: v for k, v in cols.items()})
    raw_time = df[sc.FIELD_TIME].copy() if sc.FIELD_TIME in df else pd.Series(dtype=object)
    for c in df.columns:
        s = df[c]
        if s.dtype == object or pd.api.types.is_string_dtype(s.dtype):
            nn = s.dropna()
            if len(nn) and isinstance(nn.iloc[0], str) and DATETIME_RE.match(nn.iloc[0]):
                df[c] = pd.to_datetime(s, format="ISO8601")
    return Events(
        tool,
        str(path),
        [mapped[k] for k in first],
        bad,
        {mapped[k]: sorted(v) for k, v in types.items()},
        df,
        raw_time,
    )


# ─────────────────────────────────────────────────────────────────────────────
# the checks
# ─────────────────────────────────────────────────────────────────────────────


def structure_checks(ev: Events, rows: int | None, expected_events: int) -> dict[str, bool]:
    df = ev.df
    out: dict[str, bool] = {}
    out["consistent_field_order"] = ev.inconsistent_order == 0
    out["event_count"] = len(df) == expected_events
    seq = df[sc.FIELD_SEQ]
    out["seq_unique"] = bool(seq.is_unique)
    if rows is not None:
        out["seq_in_range"] = bool(seq.min() >= 0 and seq.max() < rows)
        if len(df) == rows:
            out["seq_covers_every_row"] = bool(set(seq.tolist()) == set(range(rows)))
    out["table_field_constant"] = bool((df[sc.FIELD_TABLE] == sc.TABLE).all())
    dt_cols = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    time_cols = [c for c in dt_cols if c != sc.FIELD_TIME]
    out["has_event_time"] = sc.FIELD_TIME in df.columns and bool(time_cols)
    if out["has_event_time"]:
        first = time_cols[0]
        out["event_time_is_first_datetime_column"] = bool(
            (df[sc.FIELD_TIME] == df[first]).all()
            and ev.names.index(first) == min(ev.names.index(c) for c in time_cols)
        )
        out["events_in_event_time_order"] = bool(df[sc.FIELD_TIME].is_monotonic_increasing)
    return out


def compare_events(
    shape: Events, ref: Events, others: list[Events], rows: int | None, expected: int
) -> dict[str, Any]:
    """Every check of one workload (whole table or prefix); ``ok`` is the verdict."""
    res: dict[str, Any] = {"checks": {}, "columns": {}}
    ck = res["checks"]
    ck["field_names_and_order"] = shape.names == ref.names
    res["field_names"] = {"shape": shape.names, "baseline_mapped": ref.names}
    ck["json_types"] = shape.types == ref.types
    if not ck["json_types"]:
        res["json_types"] = {"shape": shape.types, "baseline": ref.types}
    for label, ev in (("shape", shape), ("baseline", ref)):
        for k, v in structure_checks(ev, rows, expected).items():
            ck[f"{label}:{k}"] = v
    for ev in others:
        ck[f"baseline_seed_set:{Path(ev.path).name}"] = ev.names == ref.names
    # T-21 (b)-(e): per field, against the baseline's own spread
    if shape.names == ref.names:
        for col in shape.names:
            sp, im = ref.df[col], shape.df[col]
            B = dv.merge_baselines([dv.baseline_distances(sp, o.df[col]) for o in others])
            r = dv.compare_column(sp, im, B, None, None)
            res["columns"][col] = r
            ck[f"T21:{col}"] = bool(r["equivalent"])
    res["ok"] = all(ck.values())
    return res


# ─────────────────────────────────────────────────────────────────────────────
# the allow-listed defect, shown by a probe
# ─────────────────────────────────────────────────────────────────────────────


def probe_anomaly_fraction(generate: bool) -> dict[str, Any]:
    """ST-1: the baseline ignores ``--anomaly-fraction``; Shape honours it."""
    scale, seed, n, frac = "small", 7, 3000, 0.2
    d = BENCH_OUT_DIR / "stream" / "probe"
    out: dict[str, Any] = {"id": "ST-1", **sc.ALLOWED["ST-1"]}

    def run(tool: str, name: str, *extra: str) -> Path:
        p = d / f"{tool}_{name}.jsonl"
        if generate or not p.exists():
            run_command(command(tool, scale, seed, p, n, *extra), p)
        return p

    sp0, sp1 = run("spindle", "plain"), run("spindle", "anomaly", "--anomaly-fraction", str(frac))
    sh0, sh1 = run("shape", "plain"), run("shape", "anomaly", "--anomaly-fraction", str(frac))
    sh2 = run("shape", "anomaly2", "--anomaly-fraction", str(frac))
    out["baseline_identical_with_flag"] = sp0.read_bytes() == sp1.read_bytes()
    a = [json.loads(x) for x in sh0.read_bytes().splitlines()]
    b = [json.loads(x) for x in sh1.read_bytes().splitlines()]
    changed = sum(x != y for x, y in zip(a, b, strict=True)) / max(len(a), 1)
    out["shape_changed_share"] = changed
    out["shape_deterministic"] = sh1.read_bytes() == sh2.read_bytes()
    out["ok"] = bool(
        out["baseline_identical_with_flag"]
        and 0.5 * frac <= changed <= 1.5 * frac
        and out["shape_deterministic"]
        and len(a) == len(b) == n
    )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# negative control
# ─────────────────────────────────────────────────────────────────────────────


def ks_tolerance(ref: Events, others: list[Events], col: str) -> float:
    """The KS tolerance T-21 (c) allows ``col``: max(critical value, 1.5 x the baseline's spread +
    0.002)."""
    B = dv.merge_baselines([dv.baseline_distances(ref.df[col], o.df[col]) for o in others])
    crit = dv.ks_crit(len(ref.df), len(ref.df))
    return max(crit, 1.5 * B["ks"] + 0.002)


def perturbations(shape: Events, ref: Events, others: list[Events]) -> dict[str, Events]:
    """Copies of Shape's events with one thing made wrong each. A numeric field is perturbed
    where T-21 is tightest: some fields vary so much between the baseline's own seeds (a handful
    of popular values dominate) that their tolerance is wide, and a 20% shift stays inside it."""
    from copy import deepcopy

    out: dict[str, Events] = {}
    plain = [c for c in shape.names if not c.startswith("_shape")]
    numeric = min(
        (c for c in plain if shape.types[c] in (["float"], ["int"])),
        key=lambda c: ks_tolerance(ref, others, c),
    )
    cat = next(c for c in plain if pd.api.types.is_string_dtype(shape.df[c].dtype))
    nullable = next(c for c in plain if pd.api.types.is_numeric_dtype(shape.df[c]) and c != numeric)
    stamp = next(c for c in plain if pd.api.types.is_datetime64_any_dtype(shape.df[c]))
    tol = ks_tolerance(ref, others, numeric)

    e = deepcopy(shape)
    e.df[numeric] = e.df[numeric] * 1.2
    out[f"{numeric} scaled by 1.2 (KS tolerance {tol:.4f})"] = e

    e = deepcopy(shape)
    top = e.df[cat].value_counts().index[0]
    e.df[cat] = e.df[cat].replace({top: str(top) + "_x"})
    out[f"{cat} category {top!r} renamed"] = e

    e = deepcopy(shape)
    rng = np.random.default_rng(0)
    e.df.loc[rng.random(len(e.df)) < 0.15, nullable] = np.nan
    out[f"{nullable} nulls raised by 15%"] = e

    e = deepcopy(shape)
    e.df[stamp] = e.df[stamp] + pd.Timedelta(days=400)
    e.df[sc.FIELD_TIME] = e.df[stamp]
    out[f"{stamp} shifted by 400 days"] = e

    e = deepcopy(shape)
    e.df = e.df.sample(frac=1.0, random_state=0).reset_index(drop=True)
    out["events shuffled (not in event-time order)"] = e

    e = deepcopy(shape)
    e.names = [*e.names[:-2], e.names[-1], e.names[-2]]
    out["event-time and seq fields swapped"] = e

    e = deepcopy(shape)
    e.df = e.df.iloc[: len(e.df) - 5]
    out["five events missing"] = e
    return out


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────


def workload(scale: str, max_events: int | None, generate: bool, rows: int) -> tuple[Any, ...]:
    expected = rows if max_events is None else max_events
    ref = load("spindle", ensure("spindle", scale, sc.REF_SEED, max_events, generate))
    others = [
        load("spindle", ensure("spindle", scale, s, max_events, generate))
        for s in sc.BASELINE_SEEDS
    ]
    shape = load("shape", ensure("shape", scale, sc.SHAPE_SEED, max_events, generate))
    return shape, ref, others, expected


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scale", choices=("small", "medium"), default=sc.SCALE)
    ap.add_argument(
        "--no-generate", action="store_true", help="read the existing outputs; generate nothing"
    )
    ap.add_argument("--negative-control", action="store_true")
    ap.add_argument("--report", default=None)
    a = ap.parse_args(argv)
    generate = not a.no_generate
    t0 = time.time()
    report: dict[str, Any] = {
        "workload": f"stream:{sc.DOMAIN}:{sc.TABLE}:{a.scale}",
        "seeds": {
            "reference": sc.REF_SEED,
            "baseline_spread": list(sc.BASELINE_SEEDS),
            "shape": sc.SHAPE_SEED,
        },
        "field_map": sc.FIELD_MAP,
        "allowed": sc.ALLOWED,
        "runs": {},
    }
    try:
        ref_full = load("spindle", ensure("spindle", a.scale, sc.REF_SEED, None, generate))
        rows = len(ref_full.df)
        verdicts: dict[str, dict[str, Any]] = {}
        loaded: dict[str, tuple[Any, ...]] = {}
        for label, n in (
            ("whole table", None),
            (f"first {sc.PREFIX_EVENTS[a.scale]}", sc.PREFIX_EVENTS[a.scale]),
        ):
            loaded[label] = workload(a.scale, n, generate, rows)
        for label, (shape, ref, others, expected) in loaded.items():
            verdicts[label] = compare_events(shape, ref, others, rows, expected)
            print(f"\n== {label}: {len(shape.df):,} events", flush=True)
            for k, v in verdicts[label]["checks"].items():
                print(f"  {'PASS' if v else 'FAIL'}  {k}")
        probe = probe_anomaly_fraction(generate)
        print(f"\n== allow-list ST-1 ({probe['what']}): {'PASS' if probe['ok'] else 'FAIL'}")
        print(
            f"  baseline output identical with the flag: {probe['baseline_identical_with_flag']}; "
            f"Shape changed {probe['shape_changed_share']:.1%} of events, deterministic: "
            f"{probe['shape_deterministic']}"
        )
    except MissingInput as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    report["runs"] = {
        k: {kk: vv for kk, vv in v.items() if kk != "columns"}
        | {
            "columns": {
                c: {kk: vv for kk, vv in r.items() if kk != "top10"}
                for c, r in v["columns"].items()
            }
        }
        for k, v in verdicts.items()
    }
    report["probe"] = probe
    ok = all(v["ok"] for v in verdicts.values()) and probe["ok"]

    if a.negative_control:
        shape, ref, others, expected = loaded["whole table"]
        undetected = []
        print("\n== negative control: each perturbation must make the verifier fail")
        for name, bad in perturbations(shape, ref, others).items():
            res = compare_events(bad, ref, others, rows, expected)
            detected = not res["ok"]
            failed = [k for k, v in res["checks"].items() if not v][:3]
            print(
                f"  {'detected' if detected else 'NOT DETECTED':12s} {name}  ({', '.join(failed)})"
            )
            if not detected:
                undetected.append(name)
        report["negative_control"] = {"undetected": undetected}
        print(
            "negative control:",
            "detected every perturbation" if not undetected else f"NOT DETECTED: {undetected}",
        )
        ok = ok and not undetected

    report["verdict"] = "PASS" if ok else "FAIL"
    report["seconds"] = round(time.time() - t0, 1)
    rp = Path(a.report) if a.report else BENCH_OUT_DIR / "verify" / f"stream_{a.scale}.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(f"\nVERDICT {report['verdict']}  (report: {rp})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
