"""Readers and comparers of the simulation parity harness (pandas, scipy; runs in the baseline venv).

* ``frames_equal``: two frames hold the same values (columns by name, numbers to a relative
  tolerance, dates by instant, nulls equal), whatever file format or dtype they were read from;
* ``t21_columns``: T-21 (a)-(e) per column, with the baseline's own seed-to-seed spread, through
  ``domain_1to1/verify.py``'s ``compare_column`` (the rule that retail passes 60/60 columns on);
* ``count_within``: the same form for a count that is a random draw.
"""

from __future__ import annotations

import importlib.util
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2}(\.\d+)?)?(Z|[+-]00:00)?$")

_dv: Any = None


def dv() -> Any:
    """``domain_1to1/verify.py`` as a module (T-21's column comparison)."""
    global _dv
    if _dv is None:
        spec = importlib.util.spec_from_file_location(
            "domain_verify", HERE.parent / "domain_1to1" / "verify.py"
        )
        assert spec and spec.loader
        _dv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_dv)
    return _dv


def read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".jsonl":
        if path.stat().st_size == 0:
            return pd.DataFrame()
        return pd.read_json(path, lines=True, convert_dates=False, dtype=False)
    raise ValueError(f"cannot read {path}")


def norm_series(s: pd.Series) -> pd.Series:
    """The series with dates as ``datetime64[ns]`` (zone dropped) when its values are dates or
    date text; anything else is unchanged."""
    if pd.api.types.is_datetime64_any_dtype(s.dtype):
        out = s
        if getattr(out.dt, "tz", None) is not None:
            out = out.dt.tz_convert("UTC").dt.tz_localize(None)
        return out.astype("datetime64[ns]")
    if s.dtype == object or str(s.dtype) in ("str", "string"):
        nn = s.dropna()
        head = list(nn.head(50))
        if head and all(isinstance(v, str) for v in head) and all(DATETIME_RE.match(v) for v in head):
            parsed = pd.to_datetime(s, errors="coerce", utc=True, format="ISO8601")
            return parsed.dt.tz_localize(None).astype("datetime64[ns]")
        if head and all(hasattr(v, "year") and hasattr(v, "day") for v in head):
            return pd.to_datetime(s, errors="coerce").astype("datetime64[ns]")
    return s


def norm_frame(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({c: norm_series(df[c]) for c in df.columns})


def series_equal(
    a: pd.Series, b: pd.Series, rtol: float = 1e-9, dt_tol_ns: int = 0
) -> str | None:
    """None when equal, else what differs. Instants may differ by ``dt_tol_ns`` nanoseconds."""
    if len(a) != len(b):
        return f"length {len(a)} vs {len(b)}"
    na, nb = a.isna().to_numpy(), b.isna().to_numpy()
    if not (na == nb).all():
        return f"{int((na != nb).sum())} nulls differ"
    av, bv = a[~na], b[~nb]
    if len(av) == 0:
        return None
    if pd.api.types.is_datetime64_any_dtype(av.dtype) or pd.api.types.is_datetime64_any_dtype(
        bv.dtype
    ):
        x = pd.to_datetime(av).astype("datetime64[ns]").to_numpy()
        y = pd.to_datetime(bv).astype("datetime64[ns]").to_numpy()
        bad = int((np.abs(x.astype("int64") - y.astype("int64")) > dt_tol_ns).sum())
        return f"{bad} instants differ" if bad else None
    xs, ys = pd.to_numeric(av, errors="coerce"), pd.to_numeric(bv, errors="coerce")
    if xs.notna().all() and ys.notna().all():  # numbers, not text that happens to parse
        x, y = xs.to_numpy(dtype=np.float64), ys.to_numpy(dtype=np.float64)
        bad = int((~np.isclose(x, y, rtol=rtol, atol=0.0)).sum())
        return f"{bad} numbers differ" if bad else None
    bad = int((av.astype(str).to_numpy() != bv.astype(str).to_numpy()).sum())
    return f"{bad} values differ" if bad else None


def frames_equal(
    a: pd.DataFrame,
    b: pd.DataFrame,
    *,
    ignore: tuple[str, ...] = (),
    same_order: bool = False,
    rtol: float = 1e-9,
    dt_tol_ns: int = 0,
) -> tuple[bool, str]:
    """Same columns (as sets, or in order), same rows in the same order, equal values."""
    ca = [c for c in a.columns if c not in ignore]
    cb = [c for c in b.columns if c not in ignore]
    if same_order:
        if ca != cb:
            return False, f"columns {ca} vs {cb}"
    elif set(ca) != set(cb):
        return False, (
            f"columns only in baseline {sorted(set(ca) - set(cb))}, "
            f"only in Shape {sorted(set(cb) - set(ca))}"
        )
    if len(a) != len(b):
        return False, f"rows {len(a)} vs {len(b)}"
    a, b = norm_frame(a), norm_frame(b)
    for c in ca:
        why = series_equal(
            a[c].reset_index(drop=True), b[c].reset_index(drop=True), rtol, dt_tol_ns
        )
        if why:
            return False, f"column {c}: {why}"
    return True, f"{len(a)} rows, {len(ca)} columns"


def t21_columns(
    ref: pd.DataFrame,
    spread: list[pd.DataFrame],
    shape: pd.DataFrame,
    *,
    skip: tuple[str, ...] = (),
) -> dict[str, dict[str, Any]]:
    """T-21 (a)-(e) for the columns of one output table: the names and order equal, then per
    column the null rate, KS or TVD and vocabulary checks of ``compare_column``, with the
    baseline's own seed-to-seed spread (``spread``, seeds 43-46) against the reference ``ref``."""
    mod = dv()
    ref, shape = norm_frame(ref), norm_frame(shape)
    spread = [norm_frame(s) for s in spread]
    cols = [c for c in ref.columns if c not in skip]
    out: dict[str, dict[str, Any]] = {
        "(a) names and order": {
            "equivalent": cols == [c for c in shape.columns if c not in skip],
            "baseline": cols,
            "shape": [c for c in shape.columns if c not in skip],
        }
    }
    for c in cols:
        if c not in shape.columns:
            continue
        bs = [mod.baseline_distances(ref[c], s[c]) for s in spread if c in s.columns]
        if not bs:
            continue
        try:
            out[c] = mod.compare_column(ref[c], shape[c], mod.merge_baselines(bs), None, None)
        except Exception as exc:  # a column the comparison cannot read is a failure, with its cause
            out[c] = {"equivalent": False, "error": f"{type(exc).__name__}: {exc}"}
    return out


def count_within(shape: float, ref: float, spread: list[float]) -> tuple[bool, str]:
    """A count (rows, files, events) that is a random draw: within max(5 sigma, 1.5 x the
    baseline's seed-to-seed range), sigma = sqrt(count) (T-21 (b)'s form for a count)."""
    seeds = [ref, *spread]
    tol = max(5 * math.sqrt(max(ref, 1.0)), 1.5 * (max(seeds) - min(seeds)))
    return abs(shape - ref) <= tol, f"Shape {shape:g} vs baseline {ref:g} (tol {tol:.1f})"
