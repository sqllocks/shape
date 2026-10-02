"""Shared machinery of the simulation parity harness (P6-04): running the baseline, and the
comparison rules (T-21's, applied to simulators).

Used by every ``case_*.py`` and by ``verify.py``; runs in the Shape venv (numpy, scipy and
pyarrow). The baseline runs in its own venv through ``baseline_worker.py``.

The comparison rules, per output table of a simulator (the reference baseline seed is 42, its
own spread comes from seeds 43-46 against it, Shape runs at seed 1042; the set is fixed and has
no option, and a verdict from another set counts for nothing):

* **(a) shape:** the same column names in the same order and the same Arrow types (units of a
  timestamp and ``large_string`` do not count; ``names.TYPE_ALIASES`` lists the accepted
  pairs); the same row count where the table is deterministic, else within
  ``max(5 sqrt(mean), 1.5 x the spread of the baseline seeds)`` of the baseline's mean.
* **(b) null rate** within ``max(5 sigma, 1.5 x the baseline's spread)``.
* **(c) numeric and datetime columns:** KS <= max(critical value at alpha = 0.001,
  1.5 x the baseline's largest seed-to-seed KS + 0.002).
* **(d) categorical columns:** TVD <= max(3 x multinomial noise, 1.5 x the baseline's spread +
  0.002) and vocabulary overlap >= 0.999.
* **(e) identifiers and formatted strings:** every value matches the column's pattern (>= 0.999),
  identifiers are unique, and the distinct-count ratio stays within the baseline's spread.
* **(f) integrity:** every foreign key resolves (100%), as in the baseline.
* **deterministic columns** (outputs that depend on the input only) are equal to the baseline's
  value by value.
* **summary statistics** (``result.stats``): the same keys; each number within
  ``max(1.5 x the baseline seeds' spread, floor)`` of the baseline's mean (a count also within
  5 sqrt(mean)); each distribution within the TVD rule.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from scipy import stats as sps

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import names  # noqa: E402
from paths import BENCH_OUT_DIR, SPINDLE_PY, SPINDLE_ROOT  # noqa: E402

REF_SEED = 42
BASELINE_SEEDS = (43, 44, 45, 46)
SHAPE_SEED = 1042
ALPHA = 0.001
PIN = "422e78df2267e73bb2fa976267e48cb437861e2f"
VOCAB_MIN = 0.999
CACHE = BENCH_OUT_DIR / "simulation_1to1"


class HarnessError(RuntimeError):
    """The inputs of a verdict are missing or a command failed (exit code 2)."""


# ---- results --------------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    label: str
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, **detail: Any) -> bool:
        self.checks.append(Check(f"{self.label}:{name}", bool(ok), detail))
        return bool(ok)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "checks": len(self.checks),
            "failed": len(self.failed),
            "failed_detail": [{"name": c.name, **_jsonable(c.detail)} for c in self.failed],
        }


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return str(x)
    return x


@dataclass
class Run:
    """One simulator run: its output tables and its summary statistics."""

    tables: dict[str, pa.Table]
    stats: dict[str, Any]


@dataclass
class Context:
    quick: bool = False
    only_controls: bool = False
    skip_controls: bool = False


# ---- the baseline ---------------------------------------------------------------------------


def pin_check() -> None:
    """The baseline checkout must be the pinned commit (T-20)."""
    out = subprocess.run(
        ["git", "-C", str(SPINDLE_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    if out.returncode != 0 or out.stdout.strip() != PIN:
        raise HarnessError(
            f"the baseline checkout at {SPINDLE_ROOT} is not the pinned commit {PIN} "
            "(run benchmarks/vs_spindle/setup_spindle.sh)"
        )


def _digest(*parts: Any) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(json.dumps(part, sort_keys=True, default=str).encode())
    h.update((HERE / "baseline_worker.py").read_bytes())
    return h.hexdigest()[:16]


def read_run(directory: Path, tables: Iterable[str] | None = None) -> Run:
    out = {p.stem: pq.read_table(p) for p in sorted(directory.glob("*.parquet"))}
    if tables is not None:
        missing = [t for t in tables if t not in out]
        if missing:
            raise HarnessError(f"{directory}: missing {missing}")
    return Run(out, json.loads((directory / "stats.json").read_text()))


def baseline_runs(
    sim: str,
    config: dict[str, Any],
    inputs: dict[str, pa.Table] | None,
    seeds: Iterable[int] = (REF_SEED, *BASELINE_SEEDS),
) -> dict[int, Run]:
    """The baseline simulator ``sim`` run at ``seeds`` (only the fixed set is accepted),
    cached under ``$BENCH_OUT_DIR/simulation_1to1`` by the job's digest."""
    seeds = tuple(seeds)
    if seeds != (REF_SEED, *BASELINE_SEEDS):
        raise HarnessError("the baseline seeds are fixed at 42, 43, 44, 45, 46 (T-21)")
    if not SPINDLE_PY.exists():
        raise HarnessError(f"{SPINDLE_PY} not found (run benchmarks/vs_spindle/setup_spindle.sh)")
    pin_check()
    input_files: dict[str, str] = {}
    key = _digest(sim, config, {k: v.schema.to_string() + str(v.num_rows) for k, v in (inputs or {}).items()}, seeds)
    work = CACHE / "baseline" / f"{sim}-{key}"
    if not (work / "DONE").exists():
        work.mkdir(parents=True, exist_ok=True)
        for name, table in (inputs or {}).items():
            path = work / f"input_{name}.parquet"
            pq.write_table(table, path)
            input_files[name] = str(path)
        job = work / "job.json"
        job.write_text(
            json.dumps(
                {"sim": sim, "config": config, "seeds": list(seeds), "inputs": input_files, "out": str(work)}
            )
        )
        res = subprocess.run(
            [str(SPINDLE_PY), str(HERE / "baseline_worker.py"), str(job)],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise HarnessError(f"baseline worker failed for {sim}:\n{res.stderr[-2000:]}")
    return {s: read_run(work / f"seed{s}") for s in seeds}


# ---- columns --------------------------------------------------------------------------------


def type_name(t: pa.DataType) -> str:
    if pa.types.is_timestamp(t):
        return f"timestamp[{t.tz or 'naive'}]"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "string"
    if pa.types.is_dictionary(t):
        return type_name(t.value_type)
    return str(t)


def kind_of(t: pa.DataType) -> str:
    if pa.types.is_timestamp(t) or pa.types.is_date(t):
        return "time"
    if pa.types.is_boolean(t):
        return "enum"
    if pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "num"
    return "enum"


def valid_values(col: pa.ChunkedArray) -> list[Any]:
    return [v for v in col.to_pylist() if v is not None]


def null_rate(col: pa.ChunkedArray) -> float:
    return col.null_count / max(len(col), 1)


def numbers(col: pa.ChunkedArray, origin_us: int = 0) -> np.ndarray:
    """A numeric or time column as floats (times: seconds since ``origin_us``); nulls and NaN
    are dropped."""
    t = col.type
    if pa.types.is_timestamp(t):
        arr = pc.cast(col, pa.timestamp("us", t.tz), safe=False).cast(pa.int64())  # ns -> us truncates
        x = arr.to_numpy(zero_copy_only=False).astype(np.float64)
        x = x[~np.isnan(x)] if arr.null_count else x
        return (x - origin_us) / 1e6
    if pa.types.is_date(t):
        arr = pc.cast(col, pa.date32()).cast(pa.int32())
        x = arr.to_numpy(zero_copy_only=False).astype(np.float64)
        x = x[~np.isnan(x)] if arr.null_count else x
        return x * 86400.0 - origin_us / 1e6
    x = pc.cast(col, pa.float64()).to_numpy(zero_copy_only=False).astype(np.float64)
    return x[~np.isnan(x)]


def freq(values: Iterable[Any]) -> dict[str, float]:
    counts: dict[str, int] = {}
    n = 0
    for v in values:
        counts[str(v)] = counts.get(str(v), 0) + 1
        n += 1
    return {k: c / n for k, c in counts.items()} if n else {}


def tvd(a: dict[str, float], b: dict[str, float]) -> float:
    keys = a.keys() | b.keys()
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def ks(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) == 0 or len(b) == 0:
        return float("nan")
    return float(sps.ks_2samp(a, b, method="asymp").statistic)


def ks_crit(n: int, m: int, alpha: float = ALPHA) -> float:
    c = math.sqrt(-0.5 * math.log(alpha / 2))
    return c * math.sqrt((n + m) / (n * m)) if n and m else float("nan")


def tvd_noise(k: int, n: int, m: int) -> float:
    return math.sqrt(max(k, 2) / (2 * math.pi)) * (1 / math.sqrt(max(n, 1)) + 1 / math.sqrt(max(m, 1)))


def nanmax(xs: Iterable[float]) -> float:
    ys = [x for x in xs if not math.isnan(x)]
    return max(ys) if ys else float("nan")


# ---- comparison -----------------------------------------------------------------------------


@dataclass
class Col:
    """How one column is compared. ``kind`` is one of: ``auto`` (numeric and time: KS;
    otherwise categorical: TVD), ``num``, ``time``, ``enum``, ``id`` (unique, ``regex``),
    ``pattern`` (every value matches one of ``regexes``), ``vocab`` (values from ``vocab``),
    ``exact`` (equal to the baseline's, row by row, within ``tol``), ``const`` (every value equals
    ``value``) and ``skip`` (checked by the case itself). For ``enum``, ``vocab`` adds a declared value set to
    the values the reference run happened to draw (a rare flag may not occur in one seed)."""

    kind: str = "auto"
    regex: str | None = None
    regexes: tuple[str, ...] = ()
    vocab: frozenset[Any] | None = None
    value: Any = None
    tol: float = 0.0
    nulls: bool = True  # compare the null rate
    origin: tuple[int, int] = (0, 0)  # time origin (microseconds) of (shape, baseline)


@dataclass
class TableSpec:
    rows: str = "random"  # "random" (count rule) or "exact"
    columns: dict[str, Col] = field(default_factory=dict)
    key: tuple[str, ...] = ()  # sort both sides by these before comparing ``exact`` columns


def _sorted(table: pa.Table, key: tuple[str, ...]) -> pa.Table:
    if not key:
        return table
    return table.take(pc.sort_indices(table, sort_keys=[(k, "ascending") for k in key]))


def compare_counts(
    rep: Report, label: str, shape: float, base: list[float], *, exact: bool = False, floor: float = 0.0
) -> None:
    mean, spread = float(np.mean(base)), max(base) - min(base)
    if exact:
        rep.add(f"{label}:count", all(shape == b for b in base), shape=shape, baseline=base)
        return
    tol = max(5 * math.sqrt(abs(mean)), 1.5 * spread, floor)
    rep.add(f"{label}:count", abs(shape - mean) <= tol, shape=shape, baseline_mean=mean, baseline=base, tol=tol)


def compare_scalar(rep: Report, label: str, shape: float, base: list[float], *, floor: float = 0.0, count: bool = False) -> None:
    mean, spread = float(np.mean(base)), max(base) - min(base)
    tol = max(1.5 * spread, floor, 5 * math.sqrt(abs(mean)) if count else 0.0)
    rep.add(label, abs(shape - mean) <= tol, shape=shape, baseline_mean=mean, baseline=base, tol=tol)


def _check_schema(rep: Report, tname: str, shape: pa.Table, ref: pa.Table) -> bool:
    if shape.num_rows == 0 and ref.num_rows == 0 and not ref.column_names:
        return rep.add(f"{tname}:schema", True, note="both empty (the baseline's empty frame has no columns)")
    ok_names = shape.column_names == ref.column_names
    if shape.num_rows == 0 and ref.num_rows == 0:  # an empty pandas frame has object columns only
        return rep.add(f"{tname}:schema", ok_names, shape=shape.column_names, baseline=ref.column_names, note="both empty: names only")
    bad: dict[str, Any] = {}
    for name in set(shape.column_names) & set(ref.column_names):
        a, b = shape.schema.field(name).type, ref.schema.field(name).type
        if pa.types.is_null(b) or pa.types.is_null(a):
            continue
        ta, tb = type_name(a), type_name(b)
        alias = names.TYPE_ALIASES.get((tname, name))
        if ta != tb and not (alias and {ta, tb} == set(alias)):
            bad[name] = (ta, tb)
    return rep.add(
        f"{tname}:schema",
        ok_names and not bad,
        shape=shape.column_names,
        baseline=ref.column_names,
        type_mismatch=bad,
    )


def compare_table(
    rep: Report,
    tname: str,
    shape: pa.Table,
    base: dict[int, pa.Table],
    spec: TableSpec,
) -> None:
    """Apply the rules to one output table. ``base`` maps the baseline seeds (42 is the
    reference) to that seed's table."""
    ref = base[REF_SEED]
    exact_rows = spec.rows == "exact"
    compare_counts(rep, tname, shape.num_rows, [t.num_rows for t in base.values()], exact=exact_rows)
    if not _check_schema(rep, tname, shape, ref):
        return
    if shape.num_rows == 0 or ref.num_rows == 0:
        return
    others = [base[s] for s in BASELINE_SEEDS]
    s_sorted, r_sorted = (_sorted(shape, spec.key), _sorted(ref, spec.key)) if exact_rows else (shape, ref)
    for cname in shape.column_names:
        col = spec.columns.get(cname, Col())
        _compare_column(rep, f"{tname}.{cname}", col, shape, ref, others, cname, s_sorted, r_sorted)


def _compare_column(
    rep: Report,
    label: str,
    col: Col,
    shape: pa.Table,
    ref: pa.Table,
    others: list[pa.Table],
    cname: str,
    s_sorted: pa.Table,
    r_sorted: pa.Table,
) -> None:
    sc, rc = shape.column(cname), ref.column(cname)
    n, m = len(sc), len(rc)
    kind = col.kind if col.kind != "auto" else kind_of(sc.type if not pa.types.is_null(sc.type) else rc.type)
    if kind == "exact":
        a, b = s_sorted.column(cname), r_sorted.column(cname)
        same = len(a) == len(b) and a.null_count == b.null_count
        worst = 0.0
        if same:
            if pa.types.is_floating(a.type) or pa.types.is_integer(a.type) or pa.types.is_decimal(a.type):
                x = pc.cast(a, pa.float64()).to_numpy(zero_copy_only=False)
                y = pc.cast(b, pa.float64()).to_numpy(zero_copy_only=False)
                worst = float(np.nanmax(np.abs(x - y))) if len(x) else 0.0
                same = bool(np.array_equal(np.isnan(x), np.isnan(y))) and worst <= col.tol + 1e-9
            elif pa.types.is_timestamp(a.type) or pa.types.is_date(a.type):
                x = numbers(a, col.origin[0])
                y = numbers(b, col.origin[1])
                same = len(x) == len(y) and bool(np.allclose(x, y, atol=col.tol + 1e-6, rtol=0))
            else:
                same = [None if v is None else str(v) for v in a.to_pylist()] == [
                    None if v is None else str(v) for v in b.to_pylist()
                ]
        rep.add(f"{label}:exact", same, worst_abs_diff=worst)
        return
    if kind == "skip":
        return
    if kind == "const":
        vs = {str(v) for v in valid_values(sc)}
        vb = {str(v) for v in valid_values(rc)}
        rep.add(f"{label}:const", vs == vb == {str(col.value)} or (not vs and not vb), shape=sorted(vs), baseline=sorted(vb))
        return
    if col.nulls:
        ns, nr = null_rate(sc), null_rate(rc)
        drift = nanmax(abs(null_rate(o.column(cname)) - nr) for o in others)
        p = max(ns, 1.0 / max(n, 1))
        tol = max(5 * math.sqrt(p * (1 - p) * (1 / n + 1 / m)), 1.5 * drift, 1e-12)
        rep.add(f"{label}:null_rate", abs(ns - nr) <= tol, shape=ns, baseline=nr, tol=tol)
    if kind in ("num", "time"):
        a = numbers(sc, col.origin[0])
        b = numbers(rc, col.origin[1])
        bs = [numbers(o.column(cname), col.origin[1]) for o in others]
        drift = nanmax(ks(b, x) for x in bs)
        d = ks(a, b)
        crit = ks_crit(len(a), len(b))
        tol = max(crit, 1.5 * drift + 0.002) if not math.isnan(drift) else crit
        rep.add(f"{label}:ks", math.isnan(d) or d <= tol, ks=d, tol=tol, baseline_spread=drift, n=len(a), m=len(b))
        return
    vs_, vr = [str(v) for v in valid_values(sc)], [str(v) for v in valid_values(rc)]
    if kind == "id":
        rx = re.compile(col.regex) if col.regex else None
        ok_fmt = rx is None or all(rx.fullmatch(v) for v in vs_) and all(rx.fullmatch(v) for v in vr)
        rep.add(f"{label}:format", ok_fmt, regex=col.regex)
        rep.add(f"{label}:unique", len(set(vs_)) == len(vs_) and len(set(vr)) == len(vr))
        return
    if kind == "pattern":
        rxs = [re.compile(r) for r in col.regexes]

        def share(vals: list[str]) -> dict[str, float]:
            hit = [next((str(i) for i, r in enumerate(rxs) if r.fullmatch(v)), "none") for v in vals]
            return freq(hit)

        fs, fr = share(vs_), share(vr)
        rep.add(f"{label}:format", fs.get("none", 0.0) <= 1 - VOCAB_MIN and fr.get("none", 0.0) <= 1 - VOCAB_MIN, shape_unmatched=fs.get("none", 0.0), baseline_unmatched=fr.get("none", 0.0))
        drift = nanmax(tvd(fr, share([str(v) for v in valid_values(o.column(cname))])) for o in others)
        d = tvd(fs, fr)
        tol = max(3 * tvd_noise(len(rxs), len(vs_), len(vr)), 1.5 * drift + 0.002)
        rep.add(f"{label}:pattern_share", d <= tol, tvd=d, tol=tol, shape=fs, baseline=fr)
        dr_s, dr_b = len(set(vs_)) / max(len(vs_), 1), len(set(vr)) / max(len(vr), 1)
        dr_drift = nanmax(
            abs(len({str(v) for v in valid_values(o.column(cname))}) / max(len(valid_values(o.column(cname))), 1) / max(dr_b, 1e-12) - 1)
            for o in others
        )
        dr_tol = max(0.05, 1.5 * dr_drift)
        rep.add(f"{label}:distinct_ratio", abs(dr_s / max(dr_b, 1e-12) - 1) <= dr_tol, shape=dr_s, baseline=dr_b, tol=dr_tol)
        return
    if kind == "vocab":
        vocab = col.vocab or frozenset()
        rep.add(
            f"{label}:vocab",
            all(v in vocab for v in vs_) or sum(v in vocab for v in vs_) / max(len(vs_), 1) >= VOCAB_MIN,
            outside=sorted({v for v in vs_ if v not in vocab})[:5],
        )
        return
    # categorical (T-21 (d))
    fs, fr = freq(vs_), freq(vr)
    drift = nanmax(tvd(fr, freq(str(v) for v in valid_values(o.column(cname)))) for o in others)
    d = tvd(fs, fr)
    tol = max(3 * tvd_noise(len(fr), len(vs_), len(vr)), 1.5 * drift + 0.002)
    rep.add(f"{label}:tvd", d <= tol, tvd=d, tol=tol, baseline_spread=drift, distinct=len(fr))
    known = set(fr) | {str(v) for v in (col.vocab or ())}  # a declared value set counts as known
    overlap = sum(1 for v in vs_ if v in known) / len(vs_) if vs_ else 1.0
    rep.add(f"{label}:vocab_overlap", overlap >= VOCAB_MIN, overlap=overlap)


def compare_stats(
    rep: Report,
    shape: dict[str, Any],
    base: dict[int, dict[str, Any]],
    *,
    exact: Iterable[str] = (),
    counts: Iterable[str] = (),
    floors: dict[str, float] | None = None,
    skip: Iterable[str] = (),
) -> None:
    """The summary statistics: same keys; numbers by the spread rule, mappings by TVD."""
    exact, counts, skip = set(exact), set(counts), set(skip)
    floors = floors or {}
    ref = base[REF_SEED]
    rep.add("stats:keys", list(shape) == list(ref), shape=list(shape), baseline=list(ref))
    for k, v in shape.items():
        if k in skip or k not in ref:
            continue
        series = [b[k] for b in base.values() if k in b]
        if isinstance(v, dict):
            fs = {kk: c / max(sum(v.values()), 1) for kk, c in v.items()}
            fr = {kk: c / max(sum(ref[k].values()), 1) for kk, c in ref[k].items()}
            drift = nanmax(
                tvd(fr, {kk: c / max(sum(b[k].values()), 1) for kk, c in b[k].items()}) for s, b in base.items() if s != REF_SEED
            )
            tol = max(3 * tvd_noise(len(fr), max(sum(v.values()), 1), max(sum(ref[k].values()), 1)), 1.5 * drift + 0.002)
            known = set().union(*(b[k].keys() for b in base.values() if isinstance(b.get(k), dict)))
            rep.add(f"stats:{k}", fs.keys() <= known and tvd(fs, fr) <= tol, tvd=tvd(fs, fr), tol=tol)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            if k in exact:
                rep.add(f"stats:{k}", all(v == x for x in series), shape=v, baseline=series)
            else:
                compare_scalar(rep, f"stats:{k}", float(v), [float(x) for x in series], floor=floors.get(k, 0.0), count=k in counts)
        else:
            rep.add(f"stats:{k}", all(v == x for x in series), shape=v, baseline=series[:1])


def compare_vector(rep: Report, label: str, shape: np.ndarray, base: dict[int, np.ndarray]) -> None:
    """A derived numeric vector (say, page views per session) by the KS rule."""
    ref = base[REF_SEED]
    drift = nanmax(ks(ref, base[s]) for s in BASELINE_SEEDS)
    d = ks(shape, ref)
    crit = ks_crit(len(shape), len(ref))
    tol = max(crit, 1.5 * drift + 0.002) if not math.isnan(drift) else crit
    rep.add(f"{label}:ks", len(shape) > 0 and len(ref) > 0 and d <= tol, ks=d, tol=tol, n=len(shape), m=len(ref))


def compare_categories(rep: Report, label: str, shape: list[Any], base: dict[int, list[Any]]) -> None:
    """A derived categorical vector (say, the last funnel stage reached) by the TVD rule."""
    ref = base[REF_SEED]
    fs, fr = freq(shape), freq(ref)
    drift = nanmax(tvd(fr, freq(base[s])) for s in BASELINE_SEEDS)
    d = tvd(fs, fr)
    tol = max(3 * tvd_noise(len(fr), len(shape), len(ref)), 1.5 * drift + 0.002)
    rep.add(f"{label}:tvd", len(shape) > 0 and d <= tol, tvd=d, tol=tol, shape=fs, baseline=fr)


def baseline_once(
    sim: str, config: dict[str, Any], inputs: dict[str, pa.Table] | None, seed: int, tag: str
) -> Run:
    """One uncached baseline run (an allow-list probe), identified by ``tag``."""
    pin_check()
    key = _digest(sim, config, seed, tag, str(time.time_ns()))
    work = CACHE / "probe" / f"{sim}-{key}"
    work.mkdir(parents=True, exist_ok=True)
    input_files: dict[str, str] = {}
    for name, table in (inputs or {}).items():
        path = work / f"input_{name}.parquet"
        pq.write_table(table, path)
        input_files[name] = str(path)
    job = work / "job.json"
    job.write_text(json.dumps({"sim": sim, "config": config, "seeds": [seed], "inputs": input_files, "out": str(work)}))
    res = subprocess.run([str(SPINDLE_PY), str(HERE / "baseline_worker.py"), str(job)], capture_output=True, text=True)
    if res.returncode != 0:
        raise HarnessError(f"baseline worker failed for {sim}:\n{res.stderr[-2000:]}")
    return read_run(work / f"seed{seed}")


def baseline_many(
    sim: str, config: dict[str, Any], inputs: dict[str, pa.Table] | None, seeds: Iterable[int], tag: str
) -> dict[int, Run]:
    """Uncached baseline runs at the given seeds in one worker (an allow-list probe about the
    spread of a property across seeds)."""
    pin_check()
    seeds = tuple(seeds)
    key = _digest(sim, config, seeds, tag, str(time.time_ns()))
    work = CACHE / "probe" / f"{sim}-{key}"
    work.mkdir(parents=True, exist_ok=True)
    input_files: dict[str, str] = {}
    for name, table in (inputs or {}).items():
        path = work / f"input_{name}.parquet"
        pq.write_table(table, path)
        input_files[name] = str(path)
    job = work / "job.json"
    job.write_text(json.dumps({"sim": sim, "config": config, "seeds": list(seeds), "inputs": input_files, "out": str(work)}))
    res = subprocess.run([str(SPINDLE_PY), str(HERE / "baseline_worker.py"), str(job)], capture_output=True, text=True)
    if res.returncode != 0:
        raise HarnessError(f"baseline worker failed for {sim}:\n{res.stderr[-2000:]}")
    return {s: read_run(work / f"seed{s}") for s in seeds}


def conform(table: pa.Table, like: pa.Table) -> pa.Table:
    """``table`` with the columns of ``like``, in its order: columns ``table`` lacks are null
    (typed as in ``like``). ``table`` must not have a column ``like`` lacks."""
    extra = [c for c in table.column_names if c not in like.column_names]
    if extra:
        raise HarnessError(f"columns {extra} are not in the Shape table")
    cols = [
        table.column(f.name) if f.name in table.column_names else pa.nulls(table.num_rows, type=f.type)
        for f in like.schema
    ]
    return pa.Table.from_arrays(cols, names=like.column_names)


# ---- cases ----------------------------------------------------------------------------------


def mutations(run: Run) -> dict[str, Run]:
    """Deliberate defects in Shape's output (negative controls of the comparison itself): a
    renamed column, a scaled column, extra nulls, a changed category, dropped rows."""
    out: dict[str, Run] = {}
    tables = {k: v for k, v in run.tables.items() if v.num_rows > 20}

    def variant(table_name: str, table: pa.Table) -> Run:
        return Run({**run.tables, table_name: table}, run.stats)

    for tname, t in tables.items():
        floats = [f.name for f in t.schema if pa.types.is_floating(f.type)]
        text = [f.name for f in t.schema if pa.types.is_string(f.type) and 1 < len(set(t.column(f.name).drop_null().to_pylist())) <= 20]
        if "rename" not in out and t.num_columns > 1:
            names_ = list(t.column_names)
            names_[1] = names_[1] + "_x"
            out["rename a column"] = variant(tname, t.rename_columns(names_))
        if floats and "scale" not in out:
            i = t.column_names.index(floats[0])
            col = pc.multiply(t.column(floats[0]), 1.1)
            out["scale a numeric column by 1.1"] = variant(tname, t.set_column(i, floats[0], col))
        if floats and "nulls" not in out:
            i = t.column_names.index(floats[0])
            keep = pa.array(np.arange(t.num_rows) % 10 != 0)
            col = pc.if_else(keep, t.column(floats[0]), pa.scalar(None, t.schema.field(floats[0]).type))
            out["null 10% of a numeric column"] = variant(tname, t.set_column(i, floats[0], col))
        if text and "category" not in out:
            i = t.column_names.index(text[0])
            vals = t.column(text[0]).drop_null().to_pylist()
            top = max(set(vals), key=vals.count)
            swap = pa.array(np.arange(t.num_rows) % 5 == 0)
            col = pc.if_else(pc.and_(swap, pc.equal(t.column(text[0]), top)), pa.scalar("zzz-new-category"), t.column(text[0]))
            out["relabel a fifth of a category"] = variant(tname, t.set_column(i, text[0], col))
    biggest = max(tables, key=lambda k: tables[k].num_rows, default=None)
    if biggest:
        t = tables[biggest]
        out["drop 30% of the rows"] = variant(biggest, t.filter(pa.array(np.arange(t.num_rows) % 10 >= 3)))
    return out


def run_case(module: Any, ctx: Context) -> tuple[list[Report], list[dict[str, Any]], list[Report]]:
    """Run one case module: its configurations against the baseline, its negative controls and
    its allow-list probes. Returns ``(reports, controls, probes)``."""
    inputs = module.inputs(ctx.quick)
    reports: list[Report] = []
    controls: list[dict[str, Any]] = []
    probes: list[Report] = []
    configs = module.configs(ctx.quick)
    baselines: dict[str, dict[int, Run]] = {}
    shape_runs: dict[str, Run] = {}
    for cid, cfg in configs.items():
        baselines[cid] = baseline_runs(module.SIM, cfg, inputs)
        shape_runs[cid] = module.run_shape(cfg, SHAPE_SEED, inputs)
    if not ctx.only_controls:
        for cid, cfg in configs.items():
            rep = Report(f"{module.NAME}[{cid}]")
            module.compare(rep, shape_runs[cid], baselines[cid], cfg, inputs, ctx.quick)
            reports.append(rep)
    if not ctx.skip_controls:
        for cname, (cid, overrides) in module.controls(ctx.quick).items():
            cfg = {**configs[cid], **overrides}
            run = module.run_shape(cfg, SHAPE_SEED, inputs)
            rep = Report(f"{module.NAME}[{cid}] control {cname}")
            module.compare(rep, run, baselines[cid], configs[cid], inputs, ctx.quick)
            controls.append({"control": f"{module.NAME}:{cname}", "detected": bool(rep.failed), "failed_checks": [c.name for c in rep.failed][:6]})
    if not ctx.skip_controls:
        first = next(iter(configs))
        for what, run in mutations(shape_runs[first]).items():
            rep = Report(f"{module.NAME}[{first}] output control {what}")
            try:
                module.compare(rep, run, baselines[first], configs[first], inputs, ctx.quick)
            except (TypeError, ValueError, KeyError, IndexError, ZeroDivisionError) as exc:
                # a comparison that cannot even read the damaged output has not accepted it
                rep.add("comparison could not read the output", False, error=f"{type(exc).__name__}: {exc}")
            controls.append({"control": f"{module.NAME}:output: {what}", "detected": bool(rep.failed), "failed_checks": [c.name for c in rep.failed][:6]})
    if hasattr(module, "probes") and not ctx.only_controls:
        probes = module.probes(ctx)
    return reports, controls, probes


def invariant(rep: Report, name: str, shape: Any, base: dict[int, Any], equal: Callable[[Any, Any], bool] | None = None) -> None:
    """A property computed per run: Shape's value must match the baseline's at every seed
    (equal for booleans and exact quantities, by ``equal`` otherwise)."""
    eq = equal or (lambda a, b: a == b)
    ok = all(eq(shape, v) for v in base.values())
    rep.add(name, ok, shape=shape, baseline=list(base.values())[:2])
