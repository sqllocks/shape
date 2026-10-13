"""Stream parity against authenticated release events; no defect allowances."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import stream_common as sc  # noqa: E402 (harness path bootstrap)
from paths import BASELINE_SHAPE, BENCH_OUT_DIR, SHAPE_VENV  # noqa: E402 (harness path bootstrap)
from paths import HERE as BENCH  # noqa: E402 (harness path bootstrap)
from recorded_store import RecordingError, sha256  # noqa: E402 (harness path bootstrap)
from recorded_store import load as recording  # noqa: E402 (harness path bootstrap)

spec = importlib.util.spec_from_file_location(
    "domain_stream_comparison", BENCH / "domain_1to1" / "verify.py"
)
assert spec and spec.loader
dv = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dv
spec.loader.exec_module(dv)
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?Z?$")


class MissingInput(Exception):
    """An output file is missing, or a command did not run."""


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
    mapped = {k: sc.FIELD_MAP.get(k, k) if tool == "baseline" else k for k in cols}
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


def ks_tolerance(ref: Events, others: list[Events], col: str) -> float:
    """The KS tolerance T-21 (c) allows ``col``: max(critical value, 1.5 x the baseline's spread +
    0.002)."""
    B = dv.merge_baselines([dv.baseline_distances(ref.df[col], o.df[col]) for o in others])
    crit = dv.ks_crit(len(ref.df), len(ref.df))
    return float(max(crit, 1.5 * B["ks"] + 0.002))


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


def command(
    tool: str, scale: str, seed: int, out: Path, max_events: int | None, *extra: str
) -> list[str]:
    dv.validate_seed(tool, seed)
    exe = BASELINE_SHAPE if tool == "baseline" else SHAPE_VENV / "bin" / "shape"
    cmd = [
        str(exe),
        "stream",
        sc.DOMAIN,
        "--table",
        sc.TABLE,
        "--scale",
        scale,
        "--no-realtime",
        "--sink",
        "file",
        "-o",
        str(out),
        "--seed",
        str(seed),
    ]
    if max_events is not None:
        cmd += ["--max-events", str(max_events)]
    return [*cmd, *extra]


def run_command(cmd: list[str], out: Path) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    Path(str(out) + ".checkpoint").unlink(missing_ok=True)
    env = dict(os.environ)
    if cmd[0] == str(BASELINE_SHAPE):
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if r.returncode:
        tail = "\n".join((r.stdout + "\n" + r.stderr).splitlines()[-200:])
        print(
            "stream command " + " ".join(cmd) + "; exit " + str(r.returncode) + "\n" + tail,
            file=sys.stderr,
        )
        raise MissingInput("stream command failed with exit " + str(r.returncode))
    return r.stdout + r.stderr


def ensure(tool: str, scale: str, seed: int, max_events: int | None, generate: bool) -> Path:
    out = sc.out_file(tool, scale, seed, max_events)
    if generate and (tool == "shape" or not out.is_file()):
        run_command(command(tool, scale, seed, out, max_events), out)
    if not out.is_file():
        raise MissingInput("stream input is unavailable")
    return out


def event_digest(path: Path) -> dict[str, Any]:
    from shape.repro import dataset_id

    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    table = pa.Table.from_pylist(rows)
    return {
        "events": table.num_rows,
        "field_names": list(rows[0]) if rows else [],
        "dataset_id": dataset_id({sc.TABLE: table}),
        "jsonl_sha256": sha256(path),
    }


def authenticate_events(path: Path, expected: dict[str, Any], scale: str, seed: int) -> None:
    actual = event_digest(path)
    if any(
        actual[k] != expected[k] for k in ("events", "field_names", "dataset_id", "jsonl_sha256")
    ):
        raise RecordingError(
            f"stream:retail:order:{scale}: seed{seed}: event digest or shape differs"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scale", choices=("small", "medium"), default=sc.SCALE)
    ap.add_argument("--no-generate", action="store_true")
    ap.add_argument("--negative-control", action="store_true")
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    try:
        record = recording(
            "stream",
            "retail:order:" + a.scale,
            inputs={
                "fixtures/schemas/retail_3nf.json": sha256(
                    BENCH / "fixtures" / "schemas" / "retail_3nf.json"
                )
            },
        )
        runs = {}
        whole = None
        for variant, n in (("whole", None), ("prefix", sc.PREFIX_EVENTS[a.scale])):
            base = []
            for seed in (42, 43, 44, 45, 46):
                p = ensure("baseline", a.scale, seed, n, not a.no_generate)
                authenticate_events(p, record["seeds"][str(seed)][variant], a.scale, seed)
                base.append(load("baseline", p))
            current = load("shape", ensure("shape", a.scale, 1042, n, not a.no_generate))
            rows = record["seeds"]["42"]["whole"]["events"]
            expected = rows if n is None else n
            runs[variant] = compare_events(current, base[0], base[1:], rows, expected)
            print(variant + ": " + ("PASS" if runs[variant]["ok"] else "FAIL"))
            if variant == "whole":
                whole = (current, base[0], base[1:], rows, expected)
        ok = all(r["ok"] for r in runs.values())
        report = {"workload": "stream:retail:order:" + a.scale, "runs": runs}
        if a.negative_control:
            assert whole is not None
            current, ref, others, rows, expected = whole
            controls = {}
            for name, bad in perturbations(current, ref, others).items():
                caught = not compare_events(bad, ref, others, rows, expected)["ok"]
                controls[name] = caught
                print(name + ": " + ("caught" if caught else "MISSED"))
            report["negative_control"] = controls
            ok = ok and all(controls.values())
        dest = (
            Path(a.report)
            if a.report
            else BENCH_OUT_DIR / "verify" / ("stream_" + a.scale + ".json")
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(report, sort_keys=True, indent=2, default=str) + "\n")
        return 0 if ok else 1
    except (RecordingError, MissingInput, OSError, KeyError) as exc:
        print(
            str(exc)
            if isinstance(exc, (RecordingError, MissingInput))
            else "stream:retail:order:" + a.scale + ": unavailable input",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
